# Radio ABI 独立审计

日期：2026-11-08。结论：已发现并修正一个实际权限布局错误；修正后 **20 项独立 ARM 执行测试通过，0 skip**。

## 证据与复现

- 原始文件：`.tools/base/vcf_wired_controller_d0g_5a0e3f348_radio.bin`。
- SHA-256：`db45055fc5251a5800961337454a76fb7a0ccf30a09414f757df5b20d73b96bb`。
- 静态证据复用仓库外研究目录 `D:/Projects/sc2015/analysis/radio_analysis/` 的 `ble_assembly.txt`、`ble_decompiled.c`；关键结论由 `.tools/gcc/bin/arm-none-eabi-objdump` 的 Thumb 指令交叉核对，不依赖 Ghidra 对 SVC 的伪参数恢复。
- 动态证据：`tests/test_radio.py`、`tests/radio_harness.py`。独立编译当前 `src/radio.c`、`src/input.c`、`src/runtime.c`，使用实际 `src/radio.ld`；Unicorn **2.1.4 / Cortex-M0 / Thumb / M-class** 执行。每次加载前检查原始文件完整 SHA-256。
- harness 不导入 builder；原始 SDK 的 HIDS、DIS、advertising、HID write 分发和发送函数实际执行。硬件、SoftDevice SVC、选定原应用入口由显式 stub 替代。检查 SP 恢复与 r4–r11 保留。

本次复现（PowerShell）：

```powershell
$env:XSC_REQUIRE_EMULATION = '1'
python -m unittest discover -s tests -p test_radio.py -v
```

结果：`Ran 20 tests ... OK`。本审计未运行其他 agent 的完整测试集。

`.github/workflows/firmware.yml` 的 `All tests including ARM execution` 阶段已设置 `XSC_REQUIRE_EMULATION: '1'`，在准备依赖、安装 Unicorn 和编译之后运行，缺少依赖不能以 skip 通过。普通本地源码检查未设此变量时，仅缺依赖可 skip；hash 不符、编译失败、ABI 断言失败始终报错。

## 已修正：Report 配置权限顺序

原来的 output 配置 `08 00 03 02 21 21 00 00` 会注册一个 **write_perm=0** 的 output value，无法加密写入；并非测试期望误写。

8 字节 report 配置的实际布局：

| 偏移 | 含义 |
|---:|---|
| +0, +1 | 最大报告长度，LE uint16 |
| +2 | Report ID |
| +3 | Report Type：1=input，2=output |
| +4 | CCCD write permission |
| +5 | value read permission |
| +6 | value write permission |
| +7 | read authorization 标志 |

独立指令依据：

- `0x1d098` 与 `0x1e2a0` 使用 `index << 3`，证明 stride=8。
- `0x1d0d2..0x1d0e2`、`0x1e2c2..0x1e2d2` 把 `config+4` 放入第 5 参数，调用 `0x1e66c`。
- `0x1e678` 从入口栈读取该参数；`0x1e68c..0x1e68e` 将其 `[0]` 写入 CCCD 的 write permission，CCCD read 固定为 `0x11`。
- `0x1e6ce..0x1e6d4` 把其 `[1]`、`[2]` 分别写入 value 的 read、write permission。
- 原初始化 output 配置也是 `01 00 02 02 00 21 21 00`。

主 agent 修正为 **`08 00 03 02 00 21 21 00`** 后，执行原 `0x1b960`、`0x1e288`、`0x1e66c` 到 SVC `0xa2`，实际观察到：

- output `0x2a4d`：max_len=8，properties=`0x0e`，value read/write=`21 21`，无 CCCD；`0x2908` Report Reference=`03 02`。
- input `0x2a4d`：max_len=16，properties=`0x1a`，value read/write=`21 21`，CCCD read/write=`11 21`；Report Reference=`01 01`。
- `0x21` 表示 security mode 1 / level 2，即要求加密、无需 MITM，不是开放写入的 `0x11`。

这些权限断言保留在测试中，未为迁就原实现而降低期望。

## ABI 核验表

| 项目 | 观测与结论 | 证据地址 / 测试 |
|---|---|---|
| 原 RAM / SP | app vector 与启动 literal 都为 `0x20003e28`。scatter 第二项从 `0x200021e4` 清零 `0x1c44`，恰好结束于该地址；栈向下增长。patch 在 `[0x20003e28,0x20004000)` 的 472B 范围内，位于原零区与栈上方 | vector `0x18000`、literal `0x180d0`、scatter `0x1efcc`；`test_original_scatter_stack_and_hook_table_bindings` |
| boot handoff | `0x180c8` 从 `0x180cc` 加载原 `0x1d2f1` 然后 BX。改 literal 后由 `radio_boot` 清 patch BSS、reset input，再进入原 main | `test_boot_clears_only_reserved_bss_before_original_main`，BSS 后方 guard 不变 |
| 13 hooks | 所有 JSON 原 literal、BL target、长度字与 SHA-bound 原文件相符，所有目标符号存在；advertising BL 是 **`0x18300`** | `config/radio-patches.json`，独立解码 BL；`0x18304` 是调用后的检查 |
| profile marker | 只接受 len=137、tag=5d、index=0、marker=`0x58420180`；init 后不能切换 GATT profile | `test_profile_marker_is_exact_and_frozen_before_init` |
| marker 对原回调的影响 | 原 config callback 读取 index，data=`packet+9`，len=`packet_length-9`，不把 marker 当复制长度 | 原 `0x189c8..0x189ea` 实际执行；test-only callback 观察到 `(0, packet+9, 128)` |
| HIDS init | 72B；+0 handler，+4 原 error handler；+8/+9 boot 能力；+10 input count / +12 ptr；+16 output count / +20 ptr；+24 feature count / +28 ptr；+32 map ptr / +36 uint16 length；+48 HID version / +50 country / +51 flags | 原 `0x18e48` 清零 0x48B，`0x1b960` 及其 callees；测试执行原注册全路径，72B 后 guard 保持 |
| HIDS GATT | map=283B；input ID1/16B，output ID3/8B；feature count=0；无 boot keyboard/mouse 特征 | `test_hids_72_byte_init_and_original_gatt_registration`，捕获原 SVC `a0/a2/a3` |
| DIS PnP | init 的 **+56** 是 PnP 指针；内存结构8B：source、padding、VID、PID、version；线格式7B，没有 padding | `0x1b8b6` 读取 +0x38；`0x1e388..0x1e3a2` 读取 +0/+2/+4/+6。实际 wire=`02 5e 04 13 0b 09 05` |
| 指针对齐 | PnP、input/output config、UUID 为静态持久存储；需自然对齐供原 SDK 的 LDRH/LDR 使用 | 当前配置显式 aligned(4)，测试检查 PnP 与 report config 指针 |
| advertising init | 两个结构各56B；+0 name_type，+2 include_appearance，+3 flags；**+16 uint16 complete_uuid_count / +20 UUID ptr** | 原 `0x182b4..0x182c0`，`0x1b28e..0x1b294`，`0x19c38..0x19c4c`；guard + 实际原编码器测试 |
| advertising AAPCS | r0=advert、r1=scan、r2=modes、r3=callback，第5参数 error_callback 位于入口 `[sp]` | 原 `0x1b240` 在 push 16B 后读取 `[sp,#16]`；compiled wrapper 传 5 个不同值验证 |
| advertising wire | 广播11B：`03 19 c4 03 02 01 05 03 03 12 18`；scan26B：`19 09` + 24B `Xbox Wireless Controller`。二者分别小于31B | **实际执行**原 `0x1b23c`、`0x1b1c6`、`0x19ab4`，在 SVC `0x72` 捕获，不仅是手工长度相加 |
| input send ABI | `0x1ba90(ctx,index,len,data)`，r2 是长度而不是 data；index0；恰好16B、无 report ID prefix | `0x1ba92` 保存 r2，`0x1baca` 保存 r3 为 data；实际原函数执行到 SVC `0xa6` |
| CCCD handle | 输入第0项 value handle 为 HIDS+20，CCCD 为 **HIDS+24**；input count=HIDS+18，连接 handle=HIDS+0x176 | 原 `0x1d0c4..0x1d0d4`、`0x1e712..0x1e716`、`0x1e234..0x1e238`；原注册返回 handles 与 ready() 查询一致 |
| HIDS callback | 0=suspend，1=exit suspend；5=boot protocol，6=report protocol | 执行原 `0x1e17c` 控制写分发到 patch callback，再检查发送停止/恢复；`0x1e1a4..0x1e1ea` |
| timer ABI | create=`(&timer_pointer,1,tick)`，start=`(timer,328,NULL)`；callback 第3参数有效，state 指针持久 | 原 `0x16ce8` 解引用 ID 指针、`0x16cf8..0x16cfa` 写 mode/callback；compiled-call test |

## S110 SVC ABI

对照的外部头文件是 `sandeepmistry/arduino-nRF5` 的 S110 目录，固定 commit：
[`8d91cbb022af4f80de2f2131db275e08c9255321`](https://github.com/sandeepmistry/arduino-nRF5/tree/8d91cbb022af4f80de2f2131db275e08c9255321/cores/nRF5/SDK/components/softdevice/s110/headers)。用于 ABI 声明交叉核验，不依赖搜索结果中的 S132/S115 接口。

- `ble_ranges.h:66,72`：GAP base=`0x70`，GATTS base=`0xa0`。
- `ble_gap.h:57..76,1248`：`CONN_SEC_GET=0x70+19=0x83`；参数 r0=`uint16 conn_handle`、r1=`ble_gap_conn_sec_t*`。返回结构2B，第0B低4位 sm、高4位 lv，第1B encryption key size。
- `ble_gatts.h:61..66,489`：`VALUE_GET=0xa0+5=0xa5`；参数 r0=conn、r1=attr handle、r2=`ble_gatts_value_t*`。结构8B：`uint16 len`、`uint16 offset`、4B value pointer。
- `s110.h` 使用 naked leaf `svc; bx lr`，compiled execution 在 SVC 边界观察参数，未依靠 C 内联 asm 的偶然寄存器分配。
- 原 SDK `0x1ba90` 自己构造 HVX 参数并调用 `0xa6`；测试截获且检查 handle/type/offset/length/data，模拟短写后原函数确实返回 data-size error，patch 在下一 tick 重试。

上述是头文件声明、ARM 参数传递、原 SDK 调用结构的互相佐证；测试没有运行 SoftDevice 的真实无线协议栈。

## 状态、门控与回归覆盖

- Steam 路径：input/feature/status/config 参数及 buffer 原样转交；DIS/HIDS/advertising 的返回码保留；第5 advertising 参数保留；无 Xbox SVC。
- Xbox 路径：Steam feature/status 不再调用不存在的 Steam report index；主循环的原 service 始终运行。
- 启动 suppression：LeftBack/Steam/可选Y 首次 marker 锁存；非启动键 A 仍能报告；未全部释放前经历 connect/disconnect 仍抑制；全部释放后重复 marker、重连都不重新抑制。
- 首帧门控：必须累计 `received & 0x03b0 == 0x03b0`。buttons、triggers、stick、leftpad 到齐但缺 rightpad 时不发；最后一项到达后可发；每次 connect/disconnect 重新要求完整状态。
- 未连接、未加密、非 SM1、过短密钥、security SVC error、CCCD=0、仅 indication 位、value-get error 均不发 HVX。
- bonded CCCD/encryption 恢复无需伪造 write/auth callback；读取真实 CCCD 后重发相同状态。
- suspend/boot protocol 禁发；原 SDK 生成 exit/report 回调后恢复发送。
- 每 tick 最多尝试一次；成功且内容未变不重复发送。no-buffer/busy/SoftDevice error 和原 SDK 短写均保留待发状态，下一 tick 发送**最新**状态，不排队发送过期快照。
- connect/disconnect 清 input/cache/suspend/boot 状态，保留尚未完成的启动 suppression；未重新收齐 chunk 前无旧状态发送。

## 覆盖边界

本审计确认的是版本绑定的调用 ABI、配置元数据、编码结果与模拟错误下的状态机。全部20项通过后未发现其余阻塞性 ABI 错误。

真实 Windows/Xbox 驱动接受度、空中加密与配对时序、SoftDevice 资源容量、无线重连、实际 timer/IRQ 调度、硬件功耗、最终双 MCU 镜像 hook 的整合执行仍由主构建/设备验证负责。本 harness 为原应用连接处理、main/init/service 入口提供 stub，因此不把模拟 connect/disconnect 等同于实机重连证据。
