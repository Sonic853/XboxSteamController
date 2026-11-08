# LPC host 实现与集成合同

## 状态

已实现可编译的实验补丁，入口/存储/输入均有真实 ARM 指令模拟测试。本文是实现合同；`LPC_PATCH_RESEARCH.md` 保留较早的研究过程，其“未实现”描述已由本文取代。

- 源码：`src/host.c`、`src/bond_store.c/.h`、`src/host.ld`，链接共享 `src/mapping.c` 和 `src/runtime.c`。
- 构建配置：`config/host-patches.json`，schema 1，14处严格原字节/原目标校验。
- 验证：GCC **9.2.1**，Cortex-M0 / Thumb / `-Os` / freestanding / no-builtin / no-unwind / `-Wall -Wextra -Werror` / `-nostdlib` / `-lgcc`，没有 section GC。
- 使用主工程 `tools.build.build_payload/apply_payload` 已通过 section、symbol、保护区和hook检查。当前 `.text` **2816字节（0xb00）**，`.data=0`，`.bss=16`；扩展镜像结束于 **0x14b00**。
- 本轮host集成产物：`build/host-tests/round2-schema/host.bin`，SHA-256 `4e7fd8228b69d1370260ba5c64f2ea24d0040e12c3d8e4351d0368d15b26ba50`；同目录 `host-audit.json` 记录布局与14处改写。正式双MCU产物由主工程 `tools/build.py` 统一输出。
- 原host SHA-256：`2c42adb211c9f209dae5ac223ed451229d0aa08b139acfbd56f36b0d8add6fc4`。没有修改原文件、运行更新器、刷写设备或打开REA native会话。

## 开机、身份与输入合同

1. selector入口单次检测 **右肩RB6+Steam10**，优先于原面键组合；本次开机选择Xbox。旧左背20+Steam、左背20+Y2+Steam不触发Xbox。普通启动直接调用原 `0x64b4`，因此B+Steam、Y+Steam及其他原选择仍走官方逻辑。
2. **RB+Y2+Steam** 请求重新配对；没有Xbox bond内容时也自动请求配对。仅有mapping、128B bond仍全FF的日志不算已有绑定。设置原动作2/4000ms，已有绑定普通启动使用动作0/2000ms。
3. Xbox下 `0x6b58` 返回独立shadow transport=3；普通模式返回原 `0x10000998`。不为Xbox调用原transport setter或connection-index setter。flag随原scatter清零，不跨开机保存。
4. radio启动前清键鼠映射，设置49=2、7=7、8=7、48=0x18。连接callback先调用官方 `0x7a40`，再重应用设置。本轮另在 `0x725c` 增加 `host_input_build`，每次调用原 `0xcd3c(0)` 前检查这四项，发生漂移时恢复raw设置。
5. **每次Xbox官方builder tick强制 `mask |= 0x03f0`，并置官方forced-send标志 `0x1000032b=1`。** 六控制字段为按钮3B、扳机2B、附加状态3B、摇杆4B、左pad4B、右pad4B，总20B；加2B flags为22B。flags低四位继续由原packer写为4，典型flags为`0x03f4`。它覆盖radio要求的 `received & 0x03b0 == 0x03b0`，中立、不变化的轴也会发送。
6. 连接后的首帧另外强制accel/gyro字段，即mask包含`0x0ff0`，典型payload34B；后续IMU保持官方delta规则。builder最初 `0x8c1e` 已清零40B snapshot，未采得IMU时不会读取未初始化栈内容。原设置不启用四元数；已有mask的其他位不会被移除。
7. 输入发送频率沿用官方调度，未新设100Hz定时器。radio仍需按flags正确处理可选IMU；不能把22B当成唯一长度。
8. **不新增 `0x5f` 协议。** 原 `0x5f` 的时钟/周期处理完整透传，全量控制字段不依赖radio请求。

`host_snapshot_copy` 是 `0x91d0` 中间调用点的专用裸汇编桥，不是一般AAPCS函数：刻意改写live r5 mask；保存r0–r3/r4/LR，调用mask helper，恢复原memcpy参数后调用 `0x218c`。r4/r6/r7和SP保留，两个调用时SP均8字节对齐。测试既检查寄存器，也执行官方packer `0x91d4–0x9226` 和无delta门控 `0x9228–0x925c`，确认连续中立帧实际组包且send=true。

### RB物理依据与完整输入链

- 官方 `0xa296–0xa2bc` 在板版本≥8时将按钮index6交给 `0x80c4(6,1,14)`，得到映射字节 `RAM[0x2000021a]=0xae`。测试执行该原指令分支，再执行原 `0x8034→0xafa8`，确认读取active-low **P1_14**；硬件参考 `OpenSteamController/.../buttons.c` 同样定义右肩为P1_14。原 `0x8c10` builder将index6编码为buttons bit2（RB）。
- 本轮测试加载原scatter数据（压缩源 `0x11d2c`，恰结束于 `0x12024`），使用原字段表 `RAM 0x10000350`、真实settings默认表和setter/getter，执行 **`0x7200→host_input_build→0xcd3c→0x8c10→0xd7d2→0xa114→UART framing`**。传感器/外围服务与UART FIFO边界用显式stub替代，组包、flags门控及转义使用原指令。
- 实际输出 `0x51 + flags 0x03f4 + 20B控制数据`，覆盖按键、双扳机、摇杆和左右pad，连续无变化及归零输入仍完整发送。首帧IMU与裸桥ABI另有测试；这些结果超出前轮只测尾packer的范围。
- **已复现并修复的host缺陷：** 官方 `0x52c0` 恢复默认（feature `0x8e` 也调用此函数）会将setting8改回0。如果它发生在启动/连接callback应用raw设置之后，原 `0xcd3c(0)` 返回0，不再生成raw snapshot，后续发送路径使用非raw格式。旧版本只有启动/连接时重应用设置，无法覆盖这一时序；新 `0x725c` hook在原门控前修复。回归测试先执行真实reset并验证失败，再执行完整主循环调用链验证恢复。
- 这是模拟中明确可复现的缺口，**尚无实机证据证明用户设备恰好经历该reset，或它是“可配对重连但无输入”的唯一原因**。原transport/USB门控仍存在，完整链测试的 `0x8368` 返回未连接USB；radio timer、UART接收和HVX仍需结合radio侧诊断及硬件观察。

## 配置marker与独立绑定

### UART合同

- `5d | offset:u32le | size:u32le | body[]`；Xbox的offset0/size128成功回复将packet+5置为 **`0x58420180`**，实际body128B、逻辑UART包137B。
- 两处均覆盖：请求回复 `0xd6fc` 和主动启动推送 `0xd7ca`。后者原header未初始化，Xbox wrapper明确写offset0和marker。普通回复原样调用官方sender。
- 两处读取 `0xd6d8/0xd7b2` 在发送之前从独立journal取配置。不存在先载入Steam绑定再只换HID身份的阶段。
- `5e | offset:u32le | size:u32le | body[]` 在Xbox入口先检查：包至少9B、size非零、offset/size落在128B内、实际length精确等于size+9。之后沿用官方buffer和任务队列。
- deferred callback literal `0xd778` 指向 `host_config_write`。Xbox分支完全绕过 `0xd8bc`，因此不执行官方首次回写的connection-index轮换、不写EEPROM `0x149`、不进入原 `0x6d24`。普通分支仍调用原callback。
- 原发送函数 `0xd400` 的framing和转义继续使用。下节mapping RPC使用独立magic，原offset0/128B配置及普通非magic包保持其路径。

### 空FF配置已经核实

测试执行原nRF固件指令，而非只用重写的Python parser：

1. `0x1e52c(0, data, 128)` 将全FF复制到 `0x20002ce0`，置配置ready bit2，没有内容拒绝。
2. `0x1c70c` existing-config初始化成功返回0；实际运行 `0x1e550` 注册、`0x1e448`、`0x1e4d8`、`0x1c834`，得到 `0x20002eac` 的28B全FF头，ready字节 `0x20002194=1`。仅无关的状态reset `0x1c11c/0x1e300` 用stub替代。
3. 实际 `0x1c1f0` 检查头部+0x1a为FF，成功创建新bond（测试peer type2路径将其置FE）。
4. 地址路径另有静态字节依据：`0x18fa0` 读取config+0x6c七字节，type不是1或force时生成random-static；`0x1912c` 连接后经 `0x1e5a4` 写回。**不从Steam配置seed地址或密钥。**

重新配对时，read返回清空旧键的配置，仅可保留此Xbox journal内type1的自身七字节地址。第一笔成功回写基于相同reset view合并，再清pairing标志；后续partial write接续最新记录。radio的force流程可能生成新地址。用户取消配对、尚无成功回写时，旧Flash记录仍保留；下次普通Xbox启动可恢复旧bond。这是掉电保留旧有效记录的既定行为，不是立即持久“忘记所有设备”。

## 网页映射持久化与UART RPC

### 24B共享格式与存储API

校验、默认值和token均调用input侧共享 `mapping_default`、`mapping_valid`、`mapping_token`；token是全部24B的CRC32/IEEE。

|配置偏移|内容|
|---|---|
|0|版本1|
|1|bit0 invertRX，bit1 invertRY，bit2 leftClickRequired，bit3必须0，bit4–7 diagonal angles索引（0–15）|
|2–17|16个source的target ID，各0–17，含0=未绑定|
|18、20|little-endian u16 deadzone，各0–16000|
|22|little-endian u16 dpad threshold，256–30000|

当前共享默认：版本1、flags `0x64`；bindings `1,2,3,4,5,6,7,8,9,11,10,10,0,11,16,17`；deadzone 3000/3500、threshold 8000。

```c
void mapping_store_read(uint8_t out[24]);
uint32_t mapping_store_write(uint32_t expected_token, const uint8_t config[24]);
```

- read从最新有效journal读取；旧schema v1的FF扩展区、缺少XCM1或无效mapping均返回共享默认。
- write在主循环任务中校验配置，并在实际提交时比较当前已提交配置的token；不相等返回2。配置相同则成功免写，旧FF区域无需仅为默认配置消耗一页。
- mapping写保留完整128B bond；普通bond/partial写及重新配对写保留mapping。两者共享generation和整页CRC，重启仅依赖Flash，无永久mapping cache。
- mapping写不清pairing标志，也不把pairing的reset view写进bond；只有成功的bond回写消费该标志。

### 精确线协议

所有u32为little-endian，magic为 **`0x58434d31`**：

|方向|字节布局|长度|
|---|---|---|
|read请求|`5d \| magic:u32 \| 24:u32`|9B|
|write请求|`5e \| magic:u32 \| 28:u32 \| expectedCRC:u32 \| config24`|37B|
|统一回复|`5d \| magic:u32 \| status:u32 \| committedConfig24 \| currentCRC:u32`|37B|

status：0成功、1非法配置/请求、2token冲突、3busy；`0x10001…0x10004` 为host存储错误。mapping路径将ROM小错误编码为 `0x20000 | ROM_status`，例如ROM错误2返回 `0x20002`，不能误报token冲突。无论成功或失败，回复均携带当前已提交配置和CRC。

`host_uart_receive` 在普通/Xbox模式都先消费magic read/write；read立即回复。write检查精确长度、size与共享valid后，以短PRIMASK临界区检查/复制到原 **137B buffer `0x10001264`**；已占用返回3且不覆盖内容。随后调用原 `0x7e10` 入队 `host_config_write(0)`，返回0时释放buffer并回复3。入队成功不发送成功ACK。

原主循环 `0x7e5c→0x7e68` 执行任务后，`host_config_write` 按magic分派到mapping持久化，完成IAP及写后校验才发送回复；回复期间仍持有buffer，之后释放。队列仍为原5项、无新永久长缓冲；37B回复是临时栈对象，生成时256B写page已经退栈。radio通过原 `0x16a46` 直接发9/37B，`config_receive` 消费magic回复，不替换原 `0x186bc` 配置callback。

## Flash / RAM / IAP合同

### Flash

|范围|用途|
|---|---|
|`[0,0x2000)`|原bootloader，构建强制保护|
|`[0x2000,0x13010)`|原host镜像，仅14个声明hook可改|
|`[0x13010,0x14000)`|输出扩展间隙FF|
|`[0x14000,0x1e000)`|链接payload允许范围，当前只到0x14b00|
|`[0x1e000,0x20000)`|**新保留Xbox journal**：sector30和31，各4096B|

选择0x14000扩展而非0x12200 code cave，避免与原load数据或journal擦除单元混合。最终文件只pad到4B，不填充journal区，也不修改 `0x2010`（它原值0，不是应用长度）。独立日志是对这两个扇区的新所有权约定，并不声称实体设备那里已擦空；第一次无有效记录时擦除sector30。升级器是否保留这两个扇区另需确认。

每扇区16条256B记录，共32条。全部little-endian：

|偏移|大小|值|
|---|---|---|
|0|4|magic `0x314a4258`（XBJ1）|
|4|2|version=1|
|6|2|body length=128|
|8|4|generation（首条1，uint32 wrap）|
|12|4|generation按位取反|
|16|128|完整nRF bond配置body|
|144|4|mapping magic `0x314d4358`（XCM1）；旧v1记录为FF|
|148|24|共享mapping配置；无有效magic/配置时使用默认|
|172|76|保留扩展区，写入时保留旧记录内容；新记录初始化FF|
|248|4|commit标记 `0x4b4f4258`（XBOK）|
|252|4|标准CRC-32/ISO-HDLC，覆盖前252B|

记录schema仍为v1，body length仍128。扫描校验magic/version/length/generation complement/commit/CRC，按带符号generation差值选择最新。写入先复制旧完整256B，再合并bond或mapping并重算generation/CRC，数据未变免写；append跳过非空的无效/torn槽。当前扇区用满时擦另一个扇区，**绝不先擦含最新有效记录的扇区**。单次256B编程含commit和CRC，commit不是第二次写入的硬件原子标志；完整校验是有效性依据。CRC不提供数学上的零碰撞保证。

### RAM与栈

- `.bss NOLOAD`仅16B：`[0x10001fd0,0x10001fe0)`，由原scatter zero覆盖。
- 两个应用MSP字 `0x2000`、`0x20d0` 同时由`0x10001fe0`降低至`0x10001fd0`。只有修改两处才有效。
- ROM原顶部32B工作区 `[0x10001fe0,0x10002000)` 保持空出；不使用任何原EEPROM/校准RAM作为新scratch。
- 没有128B永久cache、额外永久IAP page或占用allocator间隙。256B page在deferred写任务栈上，源地址4B对齐；Flash目标256B对齐。
- GCC `.su`：`write_record=296`、`program=48`、`iap=48`，核心调用链392B；两个public write guard各16B、host write wrapper16B。`mapping_reply=48B`，与page不同时存活；mapping default/valid/token分别8/8/16B。
- 两个write guard在分配page之前检查其当前SP至少`0x10001ea0`，为核心链预留416B；IAP实际入口再次要求SP至少`0x10001d00`，给ROM留至`0x10001c00`的256B预算。原正常main→`0x7e5c`→`0x7e68`队列开销32B；模拟相同深度的host写回和真实原队列执行均通过。测试断言核心链≤416B、每次模拟ROM入口SP≥`0x10001d00`。
- 这些门限是显式保守预算与编译结果，不是已测得的最坏硬件栈水位。原固件/ROM/异步异常的完整栈峰值仍未实测。

16B状态布局（供实验调试）：

|地址/offset|字段|
|---|---|
|`0x10001fd0` +0|magic `0x31485358`|
|+4|xbox flag|
|+5|shadow transport=3|
|+6|pairing/reset view标志|
|+7|连接首帧IMU标志|
|+8|last_store_error|
|+12|最近一次成功持久化generation|

IAP使用ROM `0x1fff1ff1`：准备50，必要时擦除52，再准备50，复制51（256B）。时钟从官方Hz字 `0x100007d8`读取，检查1–50MHz后经官方除法得到kHz。整个准备/擦/复制期间保存PRIMASK、禁中断、退出时恢复原PRIMASK（包括原来已禁中断的情形），检查每次ROM状态。成功后逐字节比对page，再验证记录结构/CRC。

错误码：bond路径保留ROM原错误码；mapping路径按上节编码ROM错误。`0x10001`范围/长度、`0x10002`时钟、`0x10003`栈预算、`0x10004`写后校验失败。失败不清pairing标志，记录错误；原写buffer被释放，旧有效记录仍可读。

### 原数据不被覆盖的具体依据

- `0x6bc8` 映射原两个Steam BLE槽 `[0x300,0x400)` / `[0x400,0x500)`；Xbox两个读点改走Flash。
- 完整替换deferred Xbox写路径，跳过 `0x6cc8` 对EEPROM `0x149`的索引更新及原槽写入。
- EEPROM `0x800` 是提示音区，本实现没有使用它。IMU校准 `[0x200,0x21c)`、触控板校准 `0x600 + i*0x26`、原配置/私有无线区也不作为新存储。
- IAP动态测试断言所有写/擦目标仅sector30/31；构建保护原bootloader及所有未声明字节。
- 此隔离结论针对新增Xbox绑定读写与transport选择。原固件自身启动迁移/其他功能依旧存在；没有宣称整个官方固件任何时候都不写其EEPROM。

## 完整hook表

函数符号地址由ELF解析，literal自动置Thumb bit；下列为原镜像字节，不是新固件绝对地址常量。

|文件offset|原字节|类型/新符号|
|---|---|---|
|2000|`e01f0010`|exact→`d01f0010`|
|20d0|`e01f0010`|exact→`d01f0010`|
|616c|`00f0a2f9`|BL→`host_selector`|
|6b58|`00487047`|exact→`00480047`|
|6b5c|`98090010`|literal→`host_transport\|1`|
|f1bc|`fef7d0f9`|BL→`host_radio_start`|
|725c|`05f06efd`|BL→`host_input_build`|
|d6d8|`f9f776fa`|BL→`host_config_read`|
|d7b2|`f9f709fa`|BL→`host_config_read`|
|d6fc|`fff780fe`|BL→`host_reply_send`|
|d7ca|`fff719fe`|BL→`host_startup_send`|
|d778|`bdd80000`|literal→`host_config_write\|1`|
|d3e4|`05d60000`|literal→`host_uart_receive\|1`|
|91d0|`f8f7dcff`|BL→`host_snapshot_copy`|

## 测试与残余限制

执行：

```powershell
python -m pip install --target .tools/python unicorn==2.1.4
python -m unittest discover -s tests -p test_host.py -v
```

**33项通过**。测试自行编译真实C和裸汇编，加载固定SHA原BIN及所有14个hook，然后执行Thumb/M-class指令；硬件外设、ROM IAP由显式stub模拟。覆盖：

- 普通透传、RB物理GPIO映射、新旧组合键、raw设置恢复、marker和裸桥ABI。
- 原scatter/settings/完整输入链及UART转义、连续中立发送、真实reset导致的旧门控失败及新hook恢复。
- 原5e入队→写回→5d读取与启动推送、空FF nRF bond初始化。
- 独立CRC、partial merge/免写、generation wrap、双向扇区轮换、256种torn prefix、各rollover IAP阶段失败/掉电、PRIMASK恢复、低栈拒绝。
- mapping旧日志迁移/重启读回、bond与mapping双向保留、重新配对保留、mapping-only首次配对、非法配置、token冲突、torn mapping、IAP失败与ROM错误2编码。
- mapping read/write精确回复、无提前成功ACK、buffer busy、真实原队列满返回0及主循环间接调用提交；入队后配置变化时提交CAS拒绝，非默认已提交配置下invalid/busy/conflict/IAP失败回复均携带正确mapping/CRC。

最终统一构建并执行全工程测试：**128项全部通过**，另有23项网页协议测试通过。中途radio重连测试的0x8008已定位为未初始化device-manager时缺失白名单模型，见 `NO_INPUT_RESEARCH.md`；不是实机缺陷认定。最终产物与完整边界见 `VALIDATION.md`。全套结果不替代下列硬件验证。

真实剩余事项：

- **未实机验证** IAP耗时、Flash电压/掉电电气行为、ROM栈峰值、watchdog及禁中断期间UART FIFO溢出。模拟的掉电是在调用边界/部分program完成处，不能替代真实Flash测试。
- 原bond写仍沿用单137B buffer/5项队列，旧协议busy/入队失败无新增ACK重试、IAP错误只记录RAM；失败后的当次连接可能保留仅在radio RAM中的键，重启回到上次有效Flash配置。新增mapping RPC已有明确busy/失败回复和持久化后ACK，radio仍须处理UART超时及重读协调。
- 重新配对对由多条partial write组成的业务事务没有额外事务标记；每条记录自身原子恢复，不保证跨多条radio更新的整个配对事务同时提交。
- 上电组合检测是selector入口的一次采样；应先保持RB（重新配对再加Y）再按Steam。没有给新组合复制原100轮面键检测窗口。
- payload/journal新Flash范围和栈门限已有静态布局/字节和模拟依据，但没有原链接MAP或硬件完整栈水位。更新器是否全片擦除、是否接受扩展BIN没有执行验证。
- 本文不把host编译/模拟成功等同于Xbox平台实际GATT、配对、重连和输入兼容性；这需要双MCU产物的硬件验证。
