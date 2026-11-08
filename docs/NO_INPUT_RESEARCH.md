# 已连接但无输入：radio 原指令链研究

日期：2026-11-09。范围：官方 radio 基线上的 Xbox patch 输入路径，以及新增配置 GATT 的 ABI/注册时机。

## 当前结论

- **尚未确认实机“连接但无输入”的根因。** 在明确的外设/SoftDevice 状态模型下，原 timer、UART 解帧、BLE dispatcher、HIDS 发送函数可形成完整发送链。
- host agent 已条件复现原 `0x52c0` 把 setting8 恢复为0，导致原 `0xcd3c(0)` 返回0；新增 `0x725c→host_input_build` 在构建前修复四项 raw settings。该复现尚不能证明用户实机经历了这个 reset，详见下文和 `HOST_IMPLEMENTATION.md`。
- 新配置入口发现并修正一个独立、确定的错误：`BLE_GATTS_EVT_WRITE.op` 是 **event+8**，不是 +24。`radio_config.c` 现检查 `event[8] == 1`。原 SoftDevice 的序列化指令和固定版本头文件互相佐证。这个新入口错误不能解释之前未包含它的已刷固件的无输入现象。
- `config_init()` 放在原 `0x18394` 返回后、回到 main service 前，符合原注册/广告时序。原 `0x18fa0` 是设备地址恢复/生成与设置，不是广告启动。
- 原三次 vendor UUID 注册使用同一 base，实际只占一个 vendor 槽。新增配置服务及两个 characteristic 共用另一 base，总计两个；原栈支持十个 vendor base。**UUID 槽余量不等于 attribute table 容量已验证。**

## 证据与复现

- 固件：`.tools/base/vcf_wired_controller_d0g_5a0e3f348_radio.bin`。
- SHA-256：`db45055fc5251a5800961337454a76fb7a0ccf30a09414f757df5b20d73b96bb`；继承的 `RadioBuild` 每次强制校验。
- 保存的静态证据：`D:/Projects/sc2015/analysis/radio_analysis/ble_assembly.txt`、`ble_decompiled.c`。关键字节用本地 ARM objdump 核对；未新开 native 分析会话。
- 执行证据：`tests/test_radio_integration.py`，继承当前 `tests/radio_harness.py`；Unicorn 2.1.4、Cortex-M0。使用当前链接脚本，编译 radio/config/input/mapping/runtime 和 libgcc，保留 `-Wall -Wextra -Werror`。
- 测试把当前 `config/radio-patches.json` 中的符号 hooks 安装进模拟 flash；使用原 `0x18258(0x1efec,0x20002000,0x1e4)` 执行压缩初始化数据恢复。

```powershell
$env:XSC_REQUIRE_EMULATION = '1'
python -m unittest discover -s tests -p test_radio_integration.py -v
```

修正op偏移后定向复验：**9通过、0失败、0 skip**；最终全工程 **128项通过**。测试的WRITE_CMD内部record操作码也按原 `0x38b2` switch表修正为5（WRITE_REQ为6），不能把属于其他过程的7当作WRITE_CMD。重连测试的0x8008模拟边界问题已通过显式空白名单模型修正。

| 测试 | 证据与结果 |
|---|---|
| `test_real_timer_create_start_swi_rtc_callback` | 原 main timer-init/init 块、原 create/start、SWI0 消费启动队列、两次 RTC1 回调；通过 |
| `test_uart_ring_service_deframer_dispatch_preserves_unfragmented_delta` | 原 FIFO/service/解帧/tag 分发，带 `02/03/1f` 转义的 34B 合法 delta；指针和长度保持；通过 |
| `test_original_connect_hids_connection_and_main_send_chain` | 原 dispatcher 更新两个连接号，UART 全快照经 timer/main 到原 HVX；通过 |
| `test_original_disconnect_reconnect_does_not_leave_hids_handle_stale` | disconnect 两连接号置 FFFF，新 connect 同步新号；旧输入失效，新全快照可发；通过 |
| `test_original_main_chain_exposes_incomplete_snapshot_as_no_hvx` | buttons-only 连续变化也不满足 0x3b0；补齐其余块后发送；通过 |
| `test_real_event_and_uart_chain_waits_for_actual_security_and_cccd_values` | auth event 不替代加密状态；加密后仍等 CCCD，value-get error 仍阻断；恢复后无需 write event 即发送；通过 |
| `test_shipped_softdevice_write_layout_and_config_service_without_tick` | 实际执行原 SD 序列化器，证明 op+8、offset+28；修复后原dispatcher→配置入口在无tick时也能响应，WRITE_CMD/非零offset被拒绝；通过 |
| `test_shipped_uuid_allocator_reuses_base_and_has_ten_vendor_slots` | 实际执行原 UUID allocator：重复 Valve base/type2，换短 UUID 不增槽，新 base/type3，填满十个 vendor 后返回 NO_MEM；通过 |
| `test_config_registration_precedes_deferred_original_advertising_start` | 原 init 返回后注册新 GATT；两个固定20B、无CCCD characteristic；init 时尚无 ADV_START，原 main service 处理广告请求后才出现 SVC73；通过 |

## 1. 原 timer/main 链

```text
main 1d388..1d396
  → app_timer_init 16d38(0,2,0x200022fc,NULL)
main 1d43a..1d440
  → hooked radio_init → 原18394 → config_init → timer_create 16cd8 → timer_start 16dc8
SWI0 16b4c → 176f0：处理 timer 操作队列
RTC1 16b2c → 177c4 → 17812：调用 patch tick
main 1d404..1d414
  → hooked radio_service → 原18744 → app_sched_execute 1b158 → 到达 wait 边界
```

BLE main 的**第二次** timer 初始化将 scheduler callback 设为 NULL；不能把第一次初始化的 `0x16d21` trampoline 当作最终模式。timer callback 直接由 RTC1 IRQ 路径执行。create 传 `&timer_pointer,1,tick`；start 传持久 timer 对象、328 ticks、NULL context。

测试按原代码写出的 RTC CC0 (`0x40011540`) 推进 COUNTER (`0x40011504`)，然后执行原 IRQ；没有直接调用 patch tick。它证明 callback 指针和原软件队列可用，不证明晶振、NVIC 抢占或真实低功耗唤醒正确。主循环块止于 `0x1d414`，未执行 SVC48 的硬件等待。

## 2. UART ABI：尚未分片

```text
RX FIFO 0x2000293c
 → main service / 18744 → 16a4e → 16c70
 → deframer 17b50 (状态0x20002058，callback0x2000205c)
 → tag dispatcher 19040
 → tag51 / table1eeec → 189f0
 → 18a0c 的 hook → radio_input(data,len)
```

线格式以 `02` 开始、`03` 结束；数据中的 `02/03/1f` 编码为 `1f,(byte^20)`。`0x19040` 以 tag-0x50 查询 `0x1eee8` 表；`0x189f0` 去掉 **一个 tag 字节**，传 `packet+1` 与 `uint16(packet_len-1)`。没有去掉 delta header，也没有把长度变成固定20B。

原 `0x19418` 对 low nibble=4 的数据走原 BLE 分片器；当前 hook 在该阶段之前。因此输入 parser 收到的应是完整 delta，而非已分片 BLE report。测试用 34B payload 验证这一点。

当前合法合成完整快照为 `b4 03` + buttons3B + triggers2B + stick4B + leftpad4B + rightpad4B，共19B。它验证 radio 路径，不能独自证明 LPC builder 格式。

### 与 host agent 的实际 builder 证据衔接

host agent 的 `tests/test_host.py::test_entire_input_chain_samples_and_transmits_through_real_uart_framing` 执行 `7200→cd3c→8c10→a114→UART framing`，捕获去转义后的包以 **`51 f4 03`** 开头、总长23B。去tag后为22B delta，flags=0x3f0，包含0x3b0要求的所有块，并多一个 parser已支持的3B块0x40；长度计算为 `2+3+2+3+4+4+4=22`。两侧格式证据相容，没有证据支持“radio hook收到20B分片”假设。本轮没有把两个模拟器接成同一执行，也没有实物UART抓包。

`test_default_reset_reproduces_old_gate_failure_and_hook_repairs_it` 先实际调用 `0x52c0`，确认 setting8=0、原 `0xcd3c(0)=0`，再运行新增hook后的完整 `0x7200` 链，确认 setting8恢复7并再次出现 `51 f4 03`。新hook检查 settings **49=2、7=7、8=7、48=0x18**。这是可定位的旧版host软件缺口；用户是否触发reset仍未知。host agent报告33项通过，本轮只读取对应证据，没有重复运行其全套测试。

## 3. BLE dispatcher 与发送门控

原 `0x187d8` 顺序调用：device manager `1c284` → hooked `1912c` → advertising `1b360` → connection parameters `1b6c0` → HIDS `1baf0` → vendor service `1bba8`。

- 应用连接号：`CONN=0x20002006`，connect 从 event+4 取值。
- HIDS 连接号：`0x2000251c+0x176`，由后面的 HIDS handler 更新。不能只检查其中一个。
- 输入第0项 value/CCCD handles：HIDS+20 / HIDS+24。
- `0x1ba90(HIDS,0,16,report)` 检查原 HIDS 状态、构造 HVX 参数，到 SVC `0xa6`。报告无 Report ID 前缀。
- 加密状态由 SVC83 查询当前连接；不能以曾收到 auth event 推断仍已加密。
- CCCD 每次由 SVCA5 读取实际系统属性；bond 恢复不保证发出一次 WRITE callback。
- 每个新 tick 至多尝试一次；成功且内容未变不重复发送；connect/disconnect 会清输入快照并重新要求 `received & 0x3b0 == 0x3b0`。

**可产生“所有按钮无反应”的已验证软件条件：** 一直只有合法 buttons delta、从未累计齐 triggers/stick/leftpad/rightpad 时，parser 可以持续成功，仍完全没有 HVX。这是条件复现，不是实机原因认定。

### 诊断页判读（当前 `radio_diagnostics`）

| 观察 | 下一定位点 |
|---|---|
| page0 ticks=0、page3 services 增长 | timer 创建/启动、RTC/NVIC/时钟；当前 blocked=9 表示从未 tick |
| services 不增长 | 原 main/service 调度、profile/init 路径；不能据此先归因 BLE |
| page1 rx=0 | LPC→UART→解帧→tag51→hook；先确认 profile 已启用 |
| rx 增长、rejected 增长 | page1 last_length + page4 last_header；low nibble必须4、禁止0xe000、长度必须精确匹配 flags |
| parsed 增长、received 缺0x3b0块 | 真实 builder 首快照或连接后刷新；比较实际 flags，不能仅持续按按钮 |
| page3 blocked=1/2/3 | 未连接 / suspend / boot protocol |
| blocked=4/5 | security 查询错误 / 非SM1、level不足2或密钥长度不足7 |
| blocked=6/7 | CCCD读取错误 / notify位未启用；page3 同时导出错误码和读取值 |
| blocked=8 | 首快照不完整 |
| attempts 增长、sent 不增长 | page2 last_error；检查 HIDS连接号与CONN、TX buffers、CCCD/系统属性；保留最新输入重试 |
| sent 增长但主机没输入 | 继续检查实际16B报告、主机HID解释和实机抓包；模拟SVC成功不能证明空中交付 |

page4 的 suppress 用于识别启动组合键抑制；page5 是映射后的16B报告。blocked/security/CCCD 是最近一次门控采样，可能在同一 tick 的配置读取中尚未更新；需比较连续导出。当前 page2 只导出 CONN，尚未导出 HIDS+0x176；若需要定位双连接号异常，可由主 agent 追加该字段。

## 4. WRITE event 的原始布局

相对**整个 BLE event**：

| 偏移 | 字段 |
|---:|---|
| +0 / +2 / +4 | event id / event length / connection handle，均u16 |
| +6 | write.handle:u16 |
| **+8** | write.op:u8；WRITE_REQ=1、WRITE_CMD=2 |
| +9 | padding |
| +10..27 | context：三个4B `ble_uuid_t`、service handle、value handle、type、padding |
| **+28 / +30 / +32** | offset:u16 / length:u16 / data[] |

`event+24` 是 context 内 value handle 的起点，不是 operation。固定20B请求的事件总长为52B。

**原 SoftDevice 字节证据**（函数 `0x388e`）：

- `0x3990` 设置 event id `0x50`，总长为 payload len+32。
- `0x39a6` 写 connection 到 `[r4,#4]`；`0x39aa` 写 handle 到 `[r4,#6]`。
- `0x39b2/0x39c8` 分别选 op2/op1，汇入 **`0x39b4: strb r0,[r4,#8]`**。
- `0x39b8: strh r0,[r4,#0x1c]` 写 offset；`0x39bc` 写 len 到+0x1e。
- `0x39c2` 将 destination 加0x20，转到原 memcpy `0x1612a`。
- `0x3b30/0x3b32` 写 event id/总长。

新增测试实际执行此序列化器及 memcpy；唯一辅助 stub 是 `0x33cc` attribute-context lookup，提供18B context。它不是完整 ATT/RF 解码测试。输出随后直接交给原 dispatcher，再验证配置服务；context 的 value handle 特意不等于1，避免错误的 op+24 检查侥幸通过。

头文件交叉证据：[固定 commit 的 S110 headers](https://github.com/sandeepmistry/arduino-nRF5/tree/8d91cbb022af4f80de2f2131db275e08c9255321/cores/nRF5/SDK/components/softdevice/s110/headers)。`ble_gatts.h:299..308` 明确 handle、op、context、offset、len、data 顺序；`:213..222` 定义 context；`ble_types.h:184..189` 定义 UUID；`ble_gatts.h:127..128` 定义 op值。原 HIDS `0x1e168/0x1e16a/0x1e16e` 也使用+28/+30/+32。

## 5. 新 GATT 注册时机与容量

原 `0x18394`：SVC60 启用栈 → GAP/DIS/HIDS/Valve服务注册 → `0x182b0` 广告数据初始化 → connection-parameter timer初始化 → `0x18fa0` 地址设置 → 返回。`0x182b0→0x1b23c` 设置广告内容，尚未调用 ADV_START。

首个广告启动请求由后续 `0x18744` service 检查 ready bit/请求标志，调用 `0x18348→0x1b440→0x1b5f8:svc73`。因此在 `0x18394` 返回后紧接着增加新 service，再依次增加两个 characteristic，先于此正常广告启动。`ble_gatts.h:408` 要求 characteristic 只追加到最后注册的 service；当前顺序符合这一约束。

原三处 SVC63：`0x183de`、`0x199d4`、`0x19910`。其 literal `0x18458/0x19a74/0x199bc` 均为 `0x1ee39`，base字节：`f3 e5 31 71 56 38 02 b4 13 43 35 17 00 00 0f 10`。

原 UUID SVC链：`0x1653c→0x12ac→0x12f6→0x2190`，0x63分支 `0x21c0→0x1e46→0x2c62`。

- `0x17ec..0x17f0` 向 `0x2bf0` 传11槽；后者预装一个标准BLE base，留下10个 vendor槽。
- `0x2c22` 查找时比较 bytes0..11、14..15，忽略短UUID bytes12/13。
- `0x2c8a..0x2c90` 对已有base返回原type；不增加表项。`0x2c92..0x2ca6` 仅为新base复制16B并递增计数。
- `0x2c72..0x2c7e` 先检查容量，满表返回4 (`NRF_ERROR_NO_MEM`)；容量检查发生在重复查找前。
- 外部 `ble.h:112` 的十槽上限吻合，但 `:345` 声称重复返回FORBIDDEN的注释与**目标字节实现不符**；不可直接把该注释套到这份固件。

`ble_gatts.h` 的默认 attribute table 为0x700B；原 enable参数置零选择默认。当前集成模型对 GATT注册返回成功，**未执行真实 attribute allocator，也未测出剩余字节数**。新 base + service + 两个20B value的实际注册返回码仍是必须观察的容量证据；不能仅凭UUID余量保证注册成功。

## 6. 模拟边界与未决项

常规链明确 stub 的外部初始化/持久化边界：

| 地址 | 模型与限制 |
|---|---|
| `0x1ea60` | 原事件注册边界返回成功；BLE事件从已知dispatcher直接注入 |
| `0x18b9c` | device-manager/persistence初始化返回成功；没有恢复真实bond记录 |
| `0x18fa0` | 持久地址读取/生成/设置边界返回成功；静态核对其尾部为SVC70 |
| `0x1caa0` | 广告callback的持久化白名单查询返回零地址、零IRK，保证无bond模型可走原重连广告路径 |

最初重连测试因跳过device-manager初始化而在白名单查询返回0x8008，已通过上述显式空白名单模型修正；这不是实机故障发现。原 device-manager dispatcher 虽执行，但未初始化条件会提前返回，不能将其计入真实配对/加密/bond恢复验证。

常规 SVC模型包括GAP、GATT、security、CCCD、NVIC/critical region和radio通知配置；MMIO仅为映射内存。UART注入从原软件RX FIFO开始，未模拟UART物理RX IRQ；TX原 framing入队代码执行，但未交付真实LPC。常规SVC63模型为共享harness的顺序type分配，不证明真实重复base语义；单独的原 `0x2bf0/0x2c62` 测试补足这一点。

完整main的晶振等待、SoftDevice启动/IRQ仲裁、UART电气/时序、真实bond持久化、真实LPC builder输出、attribute table分配、无线发送与Xbox接受度仍未覆盖。后续应以实机诊断导出定位最早未满足的门控，再有针对性补证；本轮没有访问或刷写设备。
