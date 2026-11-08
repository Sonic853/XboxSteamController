# LPC host 补丁研究：开机选择、BLE 配置交界与独立 profile

> 此文保留第一版实现前的研究记录。当前启动组合为 **右肩RB＋Steam / RB＋Y＋Steam**，日志、映射存储和raw设置恢复均已实现；当前合同见 [HOST_IMPLEMENTATION.md](HOST_IMPLEMENTATION.md)。下文旧左背方案及“未实现”不表示当前状态。

对象：SC2015 官方 BLE host `vcf_wired_controller_d0g_5b0f21bd.bin`。本文件仅为静态研究；没有改写原 BIN、运行固件或访问手柄，没有占用 REA native 会话。

## 结论

1. **开机选择及无 Steam 客户端的原始输入有明确补丁路径。** 在 `0x616c` 包装 selector；左背20+Steam10优先，额外Y2选择配对动作。普通开机调用原 `0x64b4`。
2. **profile 推荐随原始128字节配置回复的 header 传递。** hook `0xd6fc` 和 `0xd7ca` 两个发送点。Xbox 标记 `0x58420180` 放在 packet+5，实际 UART 数据长度仍137。nRF 必须先解析标记、再调用原 handler；此协议设计依据来自主 agent 对 nRF `0x189c8` 的核实。
3. **profile 不等于 transport，也不等于绑定槽。** transport 必须是3；profile 是独立易失变量，普通下次开机回原选择规则。通过包装 `0x6b58` 返回 shadow transport 可以完全避免为 Xbox 修改原 EEPROM transport / RAM 配置镜像。
4. **仅换 HID 身份不满足绑定隔离。** 两条配置读取路径和 `0x5e` 持久化均须分流。官方第一次 `0x5e` 会先轮换原连接索引，必须跳过。
5. **EEPROM `0x800` 已占用，不能分给 Xbox。** 本轮未认证其他128字节 EEPROM 空洞。独立 Flash 日志可设计，但尚没有现成、已验证的持久化补丁。

## 证据范围及复核

- 原文件 SHA-256：`2c42adb211c9f209dae5ac223ed451229d0aa08b139acfbd56f36b0d8add6fc4`；长度 `0x13010`。
- 原文件地址=文件偏移，ARM Cortex-M0 Thumb；函数指针需带 bit0，以下代码地址不带 Thumb 位。
- 复用 `analysis/host_analysis/exports/ble_final_{assembly.txt,decompiled.c}`、`ble_final_references.tsv` 和 `boot_chord_verification.json`。本轮重新核对全部导出的 **22251条指令字节** 与原 BIN 一致。
- Ghidra 导出遗漏 `0xd604`、`0xd8bc`、部分命令处理及 callback；本轮直接以 Capstone 5.0.9 对原字节补解。switch 内嵌字节表按 `0xda34` 的实际 helper 解读，不能把表字节当指令。
- 解压 `0x11d2c` 的 scatter 数据得到 `0x5e0` 字节，输入恰结束于 `0x12024`，与下一 scatter 段吻合。下面的 BLE delta字段表来自这份实际解压数据。
- 这些是补充静态字节证据，不是新的 REA Evidence，也不是实际设备时序或刷写验证。

## 1. 开机选择：最小可控入口

### 1.1 原路径及 ABI

```text
main 0xf164
  → 0x60d0                         init
    → 0x616c BL 0x64b4             selector（无参数）
  → 0x8064
  → 0xcfd0
  → 0xd560                         radio link start
```

关键原字节：

|位置|原字节|含义|
|---|---|---|
|`0x616c`|`00 f0 a2 f9`|`bl 0x64b4`，适合改为 selector wrapper|
|`0x64b4`|`f0 b5 87 b0`|保存r4–r7/LR，额外栈28字节|
|`0x8034`|`10 b5 02 f0 b7 ff 10 bd`|`button(r0=index)` → `0xafa8`；r0非零代表按下|
|`0x659e`|`00 f0 7b fb`|原 selector 调 `0x6c98`，可能持久化 transport|
|`0x65a4`|`07 f0 1e f9`|`0xd7e4(0x7a41)` 注册连接状态 callback|
|`0x6b58`|`00 48 70 47 98 09 00 10`|返回 `&RAM[0x10000998]`|

原检查顺序为 X4、Back11、A5、B3、Y2、Start9；Steam10控制最多100次循环。左背20不在这套条件中。

原 selector 的状态基址 literal `0x65ac = 0x100000d0`：

- `+0`：动作，0普通恢复、1切换目标、2配对。
- `+0xc` / `+0x10`：后续 `0x72d8` 使用的计时状态；不能拿来存 profile。
- 普通B分支设 `+0=0`、`+0x10=2000`。
- Y分支设 `+0=2`、`+0x10=4000`。
- `0xead4(r0=16字节结构,r1=transport)` 的 BLE 表在 `0x10cfc`：`{60000,4000,75000,2000}`。其中 `0x124f8` 是数值75000，不是文件尾数据指针。
- 原 transport setter `0x6c98(r0=byte*)` 比较 RAM `0x10000998`，不同才更新 RAM 并写 EEPROM `0x148`。connection-index setter `0x6cc8` 对应 RAM `0x10000999` / EEPROM `0x149`。

### 1.2 建议 wrapper 行为

在原 `0x616c` 处包装调用，显式保持8字节栈对齐与AAPCS的r4–r7约定：

1. 默认 profile=Steam（启动 scatter 已将新易失区清零）。
2. 检查 `button(20)` 和 `button(10)`。都按下才选择 Xbox，且优先于面键。
3. Xbox下再检查Y2：按下→动作2、计时4000；否则动作0、计时2000。
4. 注册连接状态 callback（见输入初始化章节），不执行原 `0x6c98`、`0x6cc8`。
5. 不匹配时正常调用原 `0x64b4`，保持其100轮选择行为。

这是**原 selector 入口处单次识别左背组合**，不等价于给新组合也提供原100轮窗口。用户先保持左背再按Steam的交互与此设计相符；若要求宽窗口，需要在原循环内部做更大的控制流改写。

建议独立 shadow transport 字节固定为3。包装 `0x6b58`：Xbox返回shadow地址，否则返回原 `0x10000998`。该 getter 及其 literal 共8字节可改成 `ldr r0,[pc,#0]; bx r0; .word wrapper|1`，保留调用者LR；不要在M0上生成不支持的Thumb-2任意指令。原 getter 返回前没有其他副作用。

已导出的所有 `0x10000998` 直接引用仅在 getter、setter和配置迁移 `0xc28c`；其余已识别 transport消费者经 getter。此结论是有界静态引用审计，未穷尽任意计算地址。

## 2. UART建立与配置回复交界

### 2.1 启动顺序

`0xd560`：清 `0x10001264` 长 `0x89` 的写请求缓冲；读transport；设置模式GPIO；`0xd030`复位/控制radio；delay(5)；`0xd370` UART setup；若transport3则 `0xd79c` 主动发送配置；delay(50)；`0xd80c`查询radio版本；注册后续服务。

`0xd370 → 0xb8dc` 初始化 UART0。原 `0xd3e4` literal 为 `05 d6 00 00`，作为额外栈参数传入 UART init，最终成为接收 callback `0xd605`。

UART消息 callback ABI：`r0=解转义后的packet指针，r1=packet字节数`。`0x7db0` 从 `0x10000dec` 和 `0x100002d8+2` 取包/长度，经 `+0x10` 指针调用。它是已恢复的 PendSV 延后处理路径，不是直接逐字节UART IRQ发送回调。

发送 ABI：`0xd400(r0=packet,r1=len)` → `0xbab4`。它包住 `0xace8/0xad0c` 临界区，发送起始字节02、转义数据、结束字节03。数据02/03/1f以 `1f,(byte xor 20)` 转义。**传逻辑包，不要自己加02/03，也不要绕过原转义器。**

底层 `0xba54` 在调用期间逐字节送UART或复制到TX ring，没有保留调用者buffer指针；因此marker wrapper可以原地修改当前reply后同步调用原发送函数，生命周期不需延长。这里没有证明TX ring拥塞时一定成功；保留原137字节消息规模，不额外插入握手帧。

### 2.2 原 `0x5d` 读取

`0xd604` switch按 `packet[0]-0x50` 分派；字节表 `0xd620`：`5d→0xd698`，`5e→0xd708`。

```text
请求：5d | offset:u32le | size:u32le                       (至少9字节)
回复：5d | offset:u32le | returned_size:u32le | data[]
```

`0xd698` 检查r1>=9；`0xd6a6` 为size+9分配内存；读请求offset；`0xd6cc` 取 `0x6b60()` 当前连接索引；`0xd6d8` 调：

```text
0x6bc8(r0=slot, r1=reply+9, r2=offset, r3=size)
```

该函数检查slot<2、offset+size<=0x100，再以 `0x5580(0x300+slot*0x100+offset, dst, size)` 读EEPROM。成功时 `0xd6e8` 将size写入reply+5，`0xd6ec` r1=size+9；失败时返回length字段0、wirelen9。`0xd6fc` 发送，`0xd702` 释放缓冲。

### 2.3 推荐的profile marker发送hook

|位置|原字节|调用时寄存器|改写范围|
|---|---|---|---|
|`0xd6fc`|`ff f7 80 fe`|r0=reply，r1=wirelen|请求回复发送wrapper|
|`0xd7ca`|`ff f7 19 fe`|r0=SP处packet，r1=137或9|主动配置发送wrapper|
|`0xd6d8`|`f9 f7 76 fa`|slot,dst,offset,size|若需要绑定隔离，可改读调用或统一包装 `0x6bc8`|
|`0xd7b2`|`f9 f7 09 fa`|slot,dst,0,128|主动推送亦须读Xbox独立绑定|

请求回复 wrapper：仅当Xbox flag、`packet[0]==0x5d`、wirelen137、offset0、原size128时，将packet+5写为 `0x58420180`，随后调用 `0xd401`。普通回复原样发送。该非对齐word应使用逐字节写或 `0x2238(r0=value,r1=dst)`，不要用M0不支持的非对齐STR。

**主动推送特殊点**：`0xd79c` 仅显式写packet[0]=5d，并读取128字节到packet+9；成功分支没有显式填写packet+1…+8。Xbox主动推送wrapper必须明确写offset0及上述marker；失败wirelen9不标记。不能把两处都当作“完整初始化好的相同header”。

依据主agent核实的nRF `0x189c8`：它使用packet+1作为offset，packet+9作为数据，实际UART长度-9作为size，**忽略packet+5**。因此该字段高位可以承载新协议，而不改变128字节配置。nRF wrapper应在原callback消费前锁存profile，随后调用原handler；启动先清profile，GATT初始化wrapper不能再次清掉它。

这个机制不增加往返/超时，优于先前特殊offset `0x58424c45,size4` 询问方案。仍需覆盖两处发送，处理配置到达顺序；marker只应识别完整约定的magic/version/profile/低长度，不能凭任一高位判断。

## 3. `0x5e` 写回、连接索引与绑定隔离

`0xd708` 接受 `5e|offset|size|data`，把请求放入 `0x10001264` 长137字节缓冲；经 `0x7e10(0xd8bd,0)` 排队处理。现有代码只检查总长度>=9、缓冲未占用，不能把它当作完善的新协议长度验证器。

`0xd8bc(r0=buffer_index)`：

1. 验证buffer[0]==5e。
2. 若 `RAM[0x1000030a]==0`，置1；读取 `0x6b60()` 当前索引，`(index+1)%2`，通过 `0x6cc8` 持久化到EEPROM `0x149`。
3. 调 `0x6d24(slot, buffer+9, offset, size)`；相同边界检查后，经 `0x559c` 写 `0x300+slot*0x100+offset`。
4. 临界区内清缓冲首字节。

关键字节：`0xd8f4 = f9 f7 e8 f9`（调用原索引setter）；`0xd914 = f9 f7 06 fa`（调用0x6d24）。

所以只包装 `0x6d24` 改写地址仍会污染Steam连接索引。Xbox需要在接收 `0x5e` 时整体分流，或在延后callback入口 `0xd8bc` 包装Xbox分支，直接跳过原索引更新。callback注册literal `0xd778` 原 `bd d8 00 00` 可替换为新的处理器指针。

### EEPROM已证范围

|区域|证据/用途|
|---|---|
|`[0,0x14c)`|`0x6b88`读取原配置；含硬件信息、原私有无线材料、配置版本迁移|
|`0x148/0x149`|transport / BLE连接索引|
|`[0x200,0x21c)`|IMU校准：`0x6f66`读；`0x9f2e`写，magic `0xaa5555aa`|
|`[0x300,0x500)`|两个BLE槽，各0x100；`0x6bc8`读、`0x6d24`写；`0x6ab4`也可在整个两槽范围写FF|
|`0x600 + index*0x26`|触控板校准：读 `0x967e`、写 `0xd92c`，正常两个触控板索引0/1|
|`[0x800,0xc00)`|实际提示音数据：启动 `0x3b60→0x6b28`读0x400；`0x3cc4→0x6c20`写0x400|
|`[0x800,0x1000)`|以上通用读写helper允许的最大范围，不能将上半部直接认定空闲|

`0x5580(offset,dst,len)`、`0x559c(offset,src,len)` 分别封装ROM IAP命令62、61，通过 `0x6014` 调 `0x1fff1ff1`。helper自身未验证每笔数据的语义所有权。

**本轮不能认证一个不会覆盖原数据的EEPROM新槽。** `[0x500,0x600)` 等未在上述正常调用中命中只是候选，不证明工厂数据/其他命令不使用；没有读取实体EEPROM。尤其 `0x800` 有明确冲突，必须拒绝。

独立Flash存储应使用新格式magic/version/generation/length/CRC，至少整扇区隔离；不能和代码补丁同扇区后直接擦除。需要在Xbox下同时覆盖主动推送、请求读、异步写回；空记录返回nRF可识别的“无绑定”内容并启动配对。不能在载入Steam128字节之后只切HID身份。普通profile所有分支调用原路径。

## 4. Flash、scatter、stack与临时变量

### 4.1 实际布局

bootloader `[0,0x2000)`；应用向量 `0x2000`；app Reset `0x20d4→0x20c0→0x29e0`；main跳板 `0x20c8`。`0x29e0`遍历下表，每项 `{source,destination,size,initializer}` 16字节，表 `[0x11bc0,0x11c20)`：

|项地址|source|destination|size|initializer|
|---|---|---|---|---|
|11bc0|11c20|10000000|10c|dc1c：word copy|
|11bd0|11d2c|10000200|5e0|2a26：压缩数据解包|
|11be0|12024|20000000|7c|dc1c：word copy|
|11bf0|11d2c|1000010c|4|dc2c：zero|
|11c00|12024|100007e0|1800|dc2c：zero|
|11c10|120a0|2000007c|464|dc2c：zero|

zero initializer根本不读取source。压缩源实测解析到 `0x12024`，最后有源段 `[0x12024,0x120a0)`。由此源镜像的已恢复有效load数据结束于 `0x120a0`，不是仅凭“看见FF”。

原文件 `[0x120a0,0x13010)` 全FF。可将**代码补丁候选区**明确限制为 `[0x12200,0x13000)`（3584字节），避开已有源数据、保持页边界，构建时断言整个区原为FF并限制新增段大小。

边界依据还包括：导出指令PC-relative literals审计没有将该范围作为内存对象读取；`0x10d04`的值 `0x124f8` 是75000ms超时；`0x75ac`的 `0x1d0fc` 是button mask；`0x47d8`的 `0x186a0` 是数值100000。不能把这些整数误判为Flash数据引用，也不能把未导出的callback当作不存在。

bootloader `0x13d0` 将应用数据写至 `0x2000+offset`，上界 `0x20000`、IAP sector范围2…31；production同芯片镜像长0x1fd10也佐证128KB布局。**这允许设计新的Flash扇区保留，但不是自动证明存储可安全擦写。** 新日志可考虑显式保留 `[0x1e000,0x20000)` 两个4KB扇区；它不与本BLE镜像load段重叠，但还需实现IAP擦写、掉电一致性及升级时的保留/清空策略。设备上这些字节可能是旧数据，不能声称物理Flash已擦空。

覆盖限制：上述代码候选具有scatter/字节/已恢复引用依据；仍无原链接MAP，也未执行全路径/任意计算地址证明。不要在文档中将它宣称为已实机认证的code cave。

### 4.2 独立易失profile：从栈顶显式划出16字节

原应用SP：`0x2000`及runtime literal `0x20d0` 均为 `e0 1f 00 10`（`0x10001fe0`）。RAM zero范围结束也正是 `0x10001fe0`。

可把**两处应用SP值**降低16字节至 `0x10001fd0`，将 `[0x10001fd0,0x10001fe0)` 明确归补丁：profile、shadow transport=3、pairing flag、version/magic等。它仍被原启动zero清零，此后正常向下增长的栈不再覆盖；不占EEPROM或原配置字段。只改向量、漏改 `0x20d0` 会失败。

这是显式重新分配栈空间，不是猜测BSS空洞。静态literal扫描在应用中未发现该16字节内部指针；原较高对象地址包括 `0x10001bb6`。没有测得最大栈深，16字节缩减后的栈裕量仍需验证。不要为128字节配置cache顺手继续大幅削栈。

bootloader `0xca4` 另会把MSP设回 `0x10001fe0`，随后进入ROM IAP ISP；此切换可覆盖scratch，但已经退出正常应用。易失profile不应被设计为跨ISP/bootloader保留。

`[0x10000110,0x10000200)` 不可直接拿来用：allocator `0xf244` 引用了 `0x10000114`、`0x10000110`、free-list `0x10000104`。即使这套堆边界很可疑，`0xd604` 的普通配置回复确实会调用它。`0x20000000`侧也有原状态和USB/ROM管理区，未证明空闲。

## 5. LPC原始BLE输入与无Steam客户端初始化

### 5.1 组包和发送链

```text
main event → 0x7200
  → 0xcd3c(0)
    transport3且非USB → setting8必须==7 → 0x8c10
  → 0xd7d2(report,0x40,1) → 0xa114
    → 0xd46c / 0xd408 / 0xd410       原UART framing
```

`0xcd62 = 08 20`，`0xcd64 = f8 f7 72 fa`（get setting8），`0xcd68 = 07 28`；不等于7直接返回0。**仅强制transport3不够。**

`0xa114` 对transport3且setting8==7：取report[0]为长度、report+1为payload，经原UART包type `0x51`（Flash `0x107d0`）发送。若setting8!=7则走键鼠形式。私有无线分支使用另一固定格式；不要把64字节USB报告直接喂给nRF当BLE payload。

BLE builder `0x8c10`采集完整按钮，Steam10=`0x2000`、左背20=`0x8000`、右背21=`0x10000`；扳机、摇杆、双触控板及IMU随后进入差量打包。它维护 `0x100013ac` 长40字节前态。

实际字段表 `RAM 0x10000350`（来自scatter解压）：

|mask|snapshot offset|bytes|字段|
|---|---|---|---|
|0010|0|3|按钮|
|0020|3|2|左右模拟扳机|
|0040|5|3|附加按钮/状态|
|0080|8|4|摇杆XY|
|0100|12|4|左触控板XY|
|0200|16|4|右触控板XY|
|0400|20|6|加速度|
|0800|26|6|角速度|
|1000|32|8|姿态四元数|

payload先放16位flags（低位带4），再按表拼接选中字段。**官方发送的是delta，不保证每帧携带所有轴。** nRF Xbox转换器必须保存状态、仅更新存在字段；连接生命周期必须重置输入缓存，不能把缺失字段清零。需要强制全量首帧时应另做明确的builder hook，不能误用普通“强制发送”标志当作“所有mask置位”。

### 5.2 可以直接复用的配置函数/命令

- `0x50ac()` → `0x5090()` 清键鼠映射；命令 `81 00` 对应此操作（原dispatcher用队列调用）。
- `0x52fc(r0=id,r1=value)` 设置RAM setting、按原上下界clamp，并调用 `0x6620(id,value)` 通知注册的消费者。不要绕过通知直接改setting数组。
- `0x533c(r0=三字节元组数组,r1=count)` 是批量setter。元组 `{id,u16le value}`。
- Feature命令 `87 0c 31 02 00 07 07 00 08 07 00 30 18 00`：49=2、7=7、8=7、48=0x18。与Receiver现有无Steam初始化一致，也与host实际分派 `87→0x361a→0x533c` 符合。
- 不用IMU时可将setting48设0；需要完整加速度/角速度时0x18启用相应mask；四元数还需bit2。默认48=0，因此不能假设IMU自动开启。
- 默认setting7=3、8=0、49=2，由 `0xf230→0x10836` 的默认/min/max表核实。8=7是必需变化，7=7选择原始左pad模式。
- `8e 00` 对应 `0x52c0` 恢复settings并清BLE前态；`85 00`恢复原映射。Xbox不能周期性误调用这些恢复命令覆盖自己的raw设置。

连接callback `0x7a40(r0=connected)` 在断开时会调用 `0xa208()` 恢复映射及 `0x52c0()` 恢复settings。所以应给Xbox注册一层callback：先调用 `0x7a41(connected)`，再执行clear mappings和raw settings。也在radio启动前完成一次配置。普通profile继续注册原callback。`0x4254` 读取 `0x1000030c` 指针并用 `0x10000309` 连接状态作为r0调用，签名已由汇编确认。

## 6. 可交付策略与未解决项

建议host改动顺序：

1. 显式划出16字节易失scratch，固定原SHA和每处原字节断言。
2. selector wrapper + transport getter shadow：左背+Steam仅本次Xbox，左背+Y+Steam设置动作2；普通B/Y原逻辑不改。
3. profile marker覆盖主动推送和请求回复，nRF在GATT建立前采纳。
4. 接入raw settings及连接callback重应用，nRF实现官方delta状态解码。
5. 若要求独立持久配对，完成独立Flash日志与所有配置读写分流后才能宣称完成。禁止用原EEPROM槽或0x800冒充隔离。

已证：上述地址、原字节、UART ABI、marker可写时点、原EEPROM映射、输入setting门控和scatter布局。

尚未证实/未实现：Flash日志IAP实现及掉电恢复、全芯片旧数据所有权、最大栈水位、无线启动竞态实测、Xbox平台的GATT/配对兼容性及具体刷写工具对扩展文件的处理。profile marker方案的nRF消费行为来自主agent对应镜像分析，本文件没有独立重新打开nRF native会话。
