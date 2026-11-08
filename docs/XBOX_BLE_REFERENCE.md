# Xbox BLE 参考配置与证据边界

研究日期：2026-11-08。范围：公开描述符、公开 BLE 外设/主机实现、已发表的设备抓取和 Windows 测试记录。未连接设备、未刷机、未运行固件、未打开 REA native session。

## 1. 最佳实现目标

**首选 Xbox Series X|S / model 1914 的 BLE HOGP 配置：USB VID `045e`、PID `0b13`、283 字节 Report Map、16 字节输入、8 字节输出。**

理由：

1. 真实设备资料 [S1] 与 xpadneo 的设备描述符/ATT 抓取 [S2] 相互印证；并非自行设计的普通 Gamepad map。
2. 可许可复用的 ESP32 外设实现 [S3] 使用完全相同的 283 字节 map。
3. 该实现的 Series 配置有 Windows 11 23H2 的明确 `interface: XInput` 实测记录 [S7]。旧 One S 配置在同一次测试中仅呈现为 HID，故不选旧 `02fd` 为首个 Windows 目标。
4. 所有实时报告都能放入默认 ATT MTU 23；不依赖 BLE 5、2M PHY 或长通知。

**项目可以称“Xbox Series BLE 协议兼容配置/实验性固件补丁”；本研究不能证明 Steam Controller 补丁已被 Windows 作为 XInput 接收。** 标准 HID Game Pad Usage、微软 VID/PID、设备名称本身都不是最终兼容性证明。

## 2. 固定来源、许可与内容身份

以下链接均固定 commit，issue/comment 则固定记录 ID。浏览器/搜索服务未用于设备测试。

| 编号 | 来源与固定版本 | 用途 / 许可 |
|---|---|---|
| S1 | [DJm00n/ControllersInfo](https://github.com/DJm00n/ControllersInfo/tree/bfc11b8f71df040388e4285d16bd6a7338a4e06a)，commit `bfc11b8f71df040388e4285d16bd6a7338a4e06a` | [1914 BLE 原始 descriptor.bin](https://github.com/DJm00n/ControllersInfo/blob/bfc11b8f71df040388e4285d16bd6a7338a4e06a/xboxone/xboxone_model_1914_bluetoothle_hid_report_descriptor.bin)；另有 1708 firmware 5.13/5.17 和 1914 firmware 5.17，同一 descriptor git blob `a3698b5254a7dbf743b93f11c408637ef341c08c`。仓库未发现顶层许可；作为交叉验证来源，不复制其注释/代码。 |
| S2 | [atar-axis/xpadneo](https://github.com/atar-axis/xpadneo/tree/39625746fff63c997eeb53fe3a6fc1a3de08702e)，commit `39625746fff63c997eeb53fe3a6fc1a3de08702e` | [docs/descriptors/xbxs.md](https://github.com/atar-axis/xpadneo/blob/39625746fff63c997eeb53fe3a6fc1a3de08702e/docs/descriptors/xbxs.md)，GPL-3.0-or-later，含 `0005:045E:0B13` descriptor 和 ATT notification。标题写“USB Descriptor”，但设备总线 `0005` 和 ATT 日志表明这里是 Bluetooth HID；不要据标题当作 USB XInput/GIP 协议。 |
| S3 | [Mystfit/ESP32-BLE-CompositeHID](https://github.com/Mystfit/ESP32-BLE-CompositeHID/tree/06d93eab499181afaa3e26f96ecee67233c01303)，commit `06d93eab499181afaa3e26f96ecee67233c01303` | [XboxDescriptors.h](https://github.com/Mystfit/ESP32-BLE-CompositeHID/blob/06d93eab499181afaa3e26f96ecee67233c01303/XboxDescriptors.h)、[XboxGamepadConfigurations.cpp](https://github.com/Mystfit/ESP32-BLE-CompositeHID/blob/06d93eab499181afaa3e26f96ecee67233c01303/XboxGamepadConfigurations.cpp)、[XboxGamepadDevice.h](https://github.com/Mystfit/ESP32-BLE-CompositeHID/blob/06d93eab499181afaa3e26f96ecee67233c01303/XboxGamepadDevice.h)、[XboxGamepadDevice.cpp](https://github.com/Mystfit/ESP32-BLE-CompositeHID/blob/06d93eab499181afaa3e26f96ecee67233c01303/XboxGamepadDevice.cpp)、[BleCompositeHID.cpp](https://github.com/Mystfit/ESP32-BLE-CompositeHID/blob/06d93eab499181afaa3e26f96ecee67233c01303/BleCompositeHID.cpp)。[license.txt](https://github.com/Mystfit/ESP32-BLE-CompositeHID/blob/06d93eab499181afaa3e26f96ecee67233c01303/license.txt) 是 MIT；GitHub API 的 NOASSERTION 不覆盖许可文件正文。 |
| S4 | [h2zero/NimBLE-Arduino 2.1.2](https://github.com/h2zero/NimBLE-Arduino/tree/c2b382be4a78c6a9388a3c65c357dac479b6310e)，commit `c2b382be4a78c6a9388a3c65c357dac479b6310e` | [src/NimBLEHIDDevice.cpp](https://github.com/h2zero/NimBLE-Arduino/blob/c2b382be4a78c6a9388a3c65c357dac479b6310e/src/NimBLEHIDDevice.cpp)，文件头 Apache-2.0。S3 所述依赖版本；提供实际 PnP 小端编码、HIDS/BAS/Report Reference、加密属性。 |
| S5 | [ricardoquesada/bluepad32](https://github.com/ricardoquesada/bluepad32/tree/80d6314e46f871719fbb4b721a2cc02227c4f369)，commit `80d6314e46f871719fbb4b721a2cc02227c4f369` | [src/components/bluepad32/parser/uni_hid_parser_xboxone.c](https://github.com/ricardoquesada/bluepad32/blob/80d6314e46f871719fbb4b721a2cc02227c4f369/src/components/bluepad32/parser/uni_hid_parser_xboxone.c)，Apache-2.0。实际 Xbox 主机解析器；v5 是 BLE；验证 Guide、Share、输出马达次序及 BLE 不携带 Report ID。 |
| S6 | [ArduinoBLE #276 comment 1330008322](https://github.com/arduino-libraries/ArduinoBLE/issues/276#issuecomment-1330008322)，KurtE，2022-11-29 | 真实升级 BLE 的 Xbox One，固件 `5.15.3168.0`，PnP `02 5e 04 20 0b 15 05`，是 PID `0b20`，**不是 1914/0b13 的完整 GATT 抓取**。证明相关真实 Xbox BLE 的服务形态、Appearance、名称、BAS 和 Report References。帖子未单独声明许可，仅链接及转述事实。 |
| S7 | [Mystfit #2 comment 2114532444](https://github.com/Mystfit/ESP32-BLE-CompositeHID/issues/2#issuecomment-2114532444)，2024-05-16 | Windows 11 23H2 build `22631.3593`，Series 配置 Unity device description 为 `interface: XInput`；同次 OneS 为 `interface: HID`。另见 [使用者确认](https://github.com/Mystfit/ESP32-BLE-CompositeHID/issues/2#issuecomment-2116530987)。公开第三方运行记录，不是本项目测试。 |
| S8 | [Mystfit fix a0d4a02](https://github.com/Mystfit/ESP32-BLE-CompositeHID/commit/a0d4a02b796f461fccb92b803df7873379a93f46)，2025-03-05 | 将可发送状态从 onConnect 移至 onAuthenticationComplete，修复 Windows 重连后无输入；[issue #28 结论](https://github.com/Mystfit/ESP32-BLE-CompositeHID/issues/28#issuecomment-2702758898)。MIT 仓库代码。 |
| S9 | xpadneo 同 S2 commit | [xpadneo.h](https://github.com/atar-axis/xpadneo/blob/39625746fff63c997eeb53fe3a6fc1a3de08702e/hid-xpadneo/src/xpadneo/xpadneo.h)、[rumble.c](https://github.com/atar-axis/xpadneo/blob/39625746fff63c997eeb53fe3a6fc1a3de08702e/hid-xpadneo/src/xpadneo/rumble.c)，GPL-2.0-only；[xb1s_battery_event.md](https://github.com/atar-axis/xpadneo/blob/39625746fff63c997eeb53fe3a6fc1a3de08702e/docs/reports/xb1s_battery_event.md)，GPL-3.0-or-later。交叉验证协议事实；未复制驱动源码。 |

本地 [reference/xbox-ble/series-x-report-map.hex](reference/xbox-ble/series-x-report-map.hex) 来自 S3 的 `XboxOneS_1914_HIDDescriptor`，去掉注释并把两个 Report ID 宏展开为 `01` / `03`。S3 的该变量名虽含 OneS，实际是 1914/Series 配置。原 MIT 许可保存在 [LICENSE.Mystfit.txt](reference/xbox-ble/LICENSE.Mystfit.txt)，分发派生 descriptor 时一并保留。

2026-11-08 对固定 URL 做逐字节核验：**本地 map = S3 展开数组 = S1 原始二进制 = S2 第一个 hex dump**。

| 内容 | 字节数 | SHA-256 |
|---|---:|---|
| 解码后的 report-map 字节 / S1 `.bin` | 283 | `a681e2cb8b5d9466f258e2f07f0fcc518ec438231af1b61b125c213d2c62e165` |
| S3 原始 `XboxDescriptors.h` 文件 | 40220 | `9130f551f1f2f822825a3e8d8d17c993371c4014773d20d4dc8b3a7caf205fb0` |
| S2 原始 `docs/descriptors/xbxs.md` 文件 | 10454 | `a429d1acd9e23b47f37b9308ee52899900cb0bd198547aa9e4a5f42bfb731aa3` |

map hash 针对 283 个二进制字节，不是 `.hex` 文本的换行/空格。另已在本地逐项解析 HID short items，确认 collection 平衡、只有 input/1=128 bit 和 output/3=64 bit，且下节文档字节与 `.hex` 文件相同；这是静态格式验证，不是设备运行测试。

## 3. 完整 Report Map

```text
05 01 09 05 a1 01 85 01 09 01 a1 00 09 30 09 31 15 00 27 ff
ff 00 00 95 02 75 10 81 02 c0 09 01 a1 00 09 32 09 35 15 00
27 ff ff 00 00 95 02 75 10 81 02 c0 05 02 09 c5 15 00 26 ff
03 95 01 75 0a 81 02 15 00 25 00 75 06 95 01 81 03 05 02 09
c4 15 00 26 ff 03 95 01 75 0a 81 02 15 00 25 00 75 06 95 01
81 03 05 01 09 39 15 01 25 08 35 00 46 3b 01 66 14 00 75 04
95 01 81 42 75 04 95 01 15 00 25 00 35 00 45 00 65 00 81 03
05 09 19 01 29 0f 15 00 25 01 75 01 95 0f 81 02 15 00 25 00
75 01 95 01 81 03 05 0c 0a b2 00 15 00 25 01 95 01 75 01 81
02 15 00 25 00 75 07 95 01 81 03 05 0f 09 21 85 03 a1 02 09
97 15 00 25 01 75 04 95 01 91 02 15 00 25 00 75 04 95 01 91
03 09 70 15 00 25 64 75 08 95 04 91 02 09 50 66 01 10 55 0e
15 00 26 ff 00 75 08 95 01 91 02 09 a7 15 00 26 ff 00 75 08
95 01 91 02 65 00 55 00 09 7c 15 00 26 ff 00 75 08 95 01 91
02 c0 c0
```

顶层：Generic Desktop (`01`) / Game Pad (`05`) / Application。只有以下两个报告，无 Feature report：

| ID | 类型 | 有效载荷 | 内容 |
|---|---|---:|---|
| `01` | Input | 128 bit = 16 byte | 四轴、两扳机、hat、15 个按钮位、Share |
| `03` | Output | 64 bit = 8 byte | 马达选择、四路强度、时间/重复参数 |

报告 ID 保留在 Report Map 和 Report Reference (`2908`) 中，**不放进 `2a4d` characteristic 的值**。ATT input value 16 字节、output value 8 字节；USB/HID API 工具显示的 17/9 字节可能是 host 添加了 Report ID。S2 ATT 日志和 S5 `hids_host_send_write_report(... &ff.enable_actuators, sizeof(ff)-2)` 直接支持此区别。

### 3.1 Input `01`，16 字节，小端

| 字节偏移 | 位/编码 | 含义 |
|---:|---|---|
| 0–1 | u16 LE，0..65535 | 左摇杆 X，usage `01:30` |
| 2–3 | u16 LE，0..65535 | 左摇杆 Y，usage `01:31` |
| 4–5 | u16 LE，0..65535 | 右摇杆 X，usage `01:32` (Z) |
| 6–7 | u16 LE，0..65535 | 右摇杆 Y，usage `01:35` (Rz) |
| 8–9 | 低 10 bit 0..1023；高 6 bit 0 | 左扳机 Brake，usage `02:c5` |
| 10–11 | 低 10 bit 0..1023；高 6 bit 0 | 右扳机 Accelerator，usage `02:c4` |
| 12 | 低 4 bit；高 4 bit 0 | D-pad：0 neutral，1 N，2 NE，3 E，4 SE，5 S，6 SW，7 W，8 NW |
| 13–14 | 15 个按钮 bit；最高 bit 0 | 下表；不要用连续 A/B/X/Y 排列 |
| 15 | bit0；其余 0 | Share，Consumer `0c:00b2` (Record) |

`27 ff ff 00 00` 的数值是 **65535**。S2 的解析注释及一些网上解析文本误写成 65534，以字节为准。四轴中立值为 `0x8000`；源是有符号 16 位时，用宽整数加 32768 再转 u16。Y 方向需由 Steam Controller 实际坐标定义决定，不由 HID Usage 自动翻转。

按钮字段为 `value[13] | (value[14] << 8)`：

| 控件 | usage Button | mask |
|---|---:|---:|
| A | 1 | `0001` |
| B | 2 | `0002` |
| X | 4 | `0008` |
| Y | 5 | `0010` |
| LB | 7 | `0040` |
| RB | 8 | `0080` |
| View / Back / Select | 11 | `0400` |
| Menu / Start | 12 | `0800` |
| Xbox / Guide / Home | 13 | `1000` |
| Left stick click | 14 | `2000` |
| Right stick click | 15 | `4000` |

未使用的有效按钮位 `0004/0020/0100/0200` 保持 0，padding `8000` 也保持 0。S3 与 S5 的 v5 解析器一致。不要把背键直接塞进这些位并宣称 XInput 会暴露额外按钮；首版背键应按用户布局映射到已有控件，开机模式选择单独锁存。

中立值：`00 80 00 80 00 80 00 80 00 00 00 00 00 00 00 00`。

仅 Guide 按下：`00 80 00 80 00 80 00 80 00 00 00 00 00 00 10 00`。

### 3.2 Guide 与旧 One S 布局不能混用

本 Series map **没有 Report ID 2**。Guide 是 input `01` 的 Button 13；按下/松开都应发送主报告。

旧 One S Bluetooth 描述符存在多个版本：早期 Windows-mode 使用独立 ID `02`，Generic Desktop System Main Menu (`01:85`)，1 bit + 7 padding；另有 v4.8 的 Consumer AC Home (`0c:0223`)。旧版主报告的轴 usage、按钮数量、Back 位置也不同。S3 的 `XBOX_EXTRA_INPUT_REPORT_ID` 宏不表示 Series 必须创建 ID2 characteristic；其 `_extra_input` 初始化事实上被注释掉了。

### 3.3 Output `03`，8 字节

| 偏移 | 含义 | 编码 |
|---:|---|---|
| 0 | DC Enable Actuators | 低四位 mask；高四位 0 |
| 1 | Left trigger magnitude | 0..100 |
| 2 | Right trigger magnitude | 0..100 |
| 3 | **Strong / 低频主马达** | 0..100 |
| 4 | **Weak / 高频主马达** | 0..100 |
| 5 | Duration / sustain | u8，descriptor 单位 10 ms |
| 6 | Start Delay / pulse gap | u8，继承 10 ms 单位，见下述边界 |
| 7 | Loop count | u8，无单位 |

mask：bit0=`01` weak，bit1=`02` strong，bit2=`04` right trigger，bit3=`08` left trigger；`0f` 全部。

**已识别的上游差异：S3 `XboxGamepadOutputReportData` 给偏移 3/4 的 weak/strong 名称反了。** S5 `xboxone_ff_report` 与 S9 `xpadneo_rumble_data` 都是 **strong 然后 weak**，且马达 mask 同上。代码生成采用 S5/S9 的次序，不机械复制 S3 字段名。

S5 将偏移 6 称为 `start_delay_10ms`；S9 则称 `pulse_release_10ms`。descriptor 标记为 Start Delay，不能单靠 descriptor 证明真实 Xbox 各固件的重放时序。严格区分“能接受正确 8 字节命令”与“完全复现四马达时序”。初版至少应校验长度、逐字节读取、限幅强度、按 mask 更新目标马达、处理 stop 和断连归零；未映射的扳机马达通道不能冒称已实现。

全停示例（作为设计建议）：`0f 00 00 00 00 00 00 00`。只让 strong 达到 100、持续参数 10：`02 00 00 64 00 0a 00 00`。这些是由协议结构构造的向量，不是本研究的设备实测。

### 3.4 Battery

Series 的 283 字节 map **没有 ID4 Battery HID report**。使用独立 Battery Service `180f` / Battery Level `2a19`，一个字节百分比 `0..100`，Read + Notify，CCCD `2902`。S4 实现如此；真实 Xbox One BLE 抓取 S6 也显示 `2a19 = 32`（hex，50%）。BAS 与输入 report 通道分别通知。

不要把旧 One S 的 HID ID4 直接当百分比。S9 的旧 ID4 一字节定义为 `O R E C M M L L`：bit7 online，bit6 reserved，bit5 unknown/error，bit4 charging，bits3:2 power mode（0 USB、1 普通电池、2 Play'n Charge），bits1:0 capacity（critical/low/medium/full）。这是另一种协议；S3 把 ID4 宏命名为 `EXTRA_OUTPUT` 也有误导，旧 descriptor 的对应 main item 实际是 **Input**。

Windows UI / `XInputGetBatteryInformation` 怎样映射 BAS，及其在补丁上的可用性，仍需单独验证。

## 4. 身份、广播、GATT

### 4.1 建议固定身份

| 项目 | 初始兼容配置 | 证据级别 |
|---|---|---|
| GAP 名称 `2a00` | `Xbox Wireless Controller`，UTF-8/ASCII 24 字节，不带 NUL | S5 精确名称匹配和 S6 真实广告名称；不是 XInput 充分条件 |
| Appearance `2a01` | `0x03c4`，值字节 `c4 03`（HID Gamepad） | S6 原始 characteristic 值；该帖上方 `Appearance: 0x0` 是其客户端摘要，不能覆盖原始值 |
| Manufacturer `2a29` | `Microsoft`（目标兼容身份） | S6 真机值；S3 可以配置为其他文字，证明文本名称不是唯一依据 |
| PnP `2a50` | **`02 5e 04 13 0b 09 05`** | S3 Series 配置 + S4 序列化：USB VID Source 2、VID `045e`、PID `0b13`、version `0509`。这里 version 的直接来源是外设实现，未获取同型号的完整 PnP 空口抓取 |
| HID Information `2a4a` | `11 01 00 01` | S3 `setHidInfo(0,1)` + S4 编码；bcdHID 1.11，country 0，flags 1。是实现值，不冒称真机 dump |

`045e` 是 USB vendor ID，因此 Source 必须是 `02`，不是 Bluetooth SIG company ID source `01`。PnP 的所有 u16 字段低字节在前；不要照抄老 NimBLE 调用前的字节交换 workaround 再交换一次。`0509` 是 PnP Product Version 字段，不是整个固件字符串，也不是 firmware 补丁版本。

S3 给出的串号 `3039373130303637313034303231` 不应被误作每台设备必须共享的 magic value。若实现 Serial Number characteristic，宜使用持久的设备唯一字符串；“此字段可否任意改变而不影响 Windows”尚无严格测试。本地命名/串号不能替代协议身份。

传统 31 字节广告的一个可实现布局（设计建议）：

```text
ADV:      02 01 06 03 03 12 18 03 19 c4 03
SCAN_RSP: 19 09 58 62 6f 78 20 57 69 72 65 6c 65 73 73
          20 43 6f 6e 74 72 6f 6c 6c 65 72
```

ADV 11 字节：Flags general-discoverable + BR/EDR unsupported、完整 16-bit service UUID `1812`、Appearance。SCAN_RSP 26 字节：完整名称。不能把 Flags + HID UUID + Appearance + 24 字符全名强塞进 31 字节；否则 name 或 service 可能被截掉。这是新实现建议，不是 Xbox 广播包逐字节捕获。

### 4.2 服务/特征契约

`2800/2803` 声明由栈生成；不需复制真机的 attribute handle 数值。

| Service | Characteristic / descriptor | 属性及内容 |
|---|---|---|
| `1800` GAP | `2a00`, `2a01` | Read：名称、Appearance |
| `1800` GAP | `2a04`（可选实现） | Preferred connection parameters；S6 真机值 `06 00 06 00 00 00 2c 01`：7.5 ms min/max、latency 0、3 s supervision timeout |
| `1801` GATT | 栈提供 | 保持数据库一致；如跨模式改变服务，处理缓存/Service Changed 或独立持久身份 |
| `180a` DIS | `2a50` | Read：7 字节 PnP |
| `180a` DIS | `2a29` | Read：Manufacturer；固件/串号字符串是可选兼容信息 |
| `180f` BAS | `2a19` + `2902` | Read + Notify：1 字节百分比；CCCD 使能值 `01 00` |
| `1812` HIDS | `2a4a` | Read：4 字节 HID Information |
| `1812` HIDS | `2a4b` | Read/Read Blob：完整 283 字节 map |
| `1812` HIDS | `2a4c` | Write Without Response：HID Control Point，`00` Suspend / `01` Exit Suspend |
| `1812` HIDS | 第一项 `2a4d` + `2908` + `2902` | Read + Notify；Report Reference **`01 01`**（ID1, Input），值 16 字节 |
| `1812` HIDS | 第二项 `2a4d` + `2908` | Read + Write + Write Without Response；Report Reference **`03 02`**（ID3, Output），值 8 字节 |

表中两项 Report 的属性在 S6 中分别为 `0x12` 和 `0x0e`，与 S4 一致。Report Reference 不应被设计为 host 可自由改写的协议配置。

Protocol Mode `2a4e`：S4 通用 HID helper 创建 Read + Write Without Response、默认 `01` (Report Mode)；S6 的 Xbox BLE 抓取没有该特征，且 gamepad 没有 Keyboard/Mouse Boot report。它不是证明 Xbox 身份的必需 magic characteristic。若采用 S4 形态可以提供它，但不得凭此虚构 Boot Gamepad 协议。

S6 还列出私有服务 `00000001-5f60-4c4f-9c83-a7953298d40d` 和三个 characteristic (`...0002` Read、`...0003` Read、`...0004` Write)。内容与用途未确定。S3 可用外设实现没有创建它，说明它不是该已报告 Windows 成功配置的必要条件；也不能因此宣称已经复制 Xbox 的全部 GATT 行为。

## 5. 安全、连接、MTU 与重连

### 5.1 可用于旧 BLE 硬件的基线

S3 使用 `setSecurityAuth(true, false, false)`：**bonding 开、MITM 关、LE Secure Connections 关**。因此已找到的实现不以 SC/BLE 4.2 或 BLE 5 为前提。无显示/键盘的手柄应选择 NoInputNoOutput / Just Works，持久化配对密钥并恢复加密；这个 IO capability 是实现设计建议。

S4 对 Input Report/Report Reference 要求 encrypted read，对 Output Report 要求 encrypted read/write。Report Map、PnP、HID Info、BAS 在这版 helper 里是普通 read 属性。不能仅因能完成 GATT discovery，就把链路标记为可发送输入；BLE “connected”、已配对、当前链路已加密、host 已订阅 notification 是不同状态。

发送条件建议为：**连接有效 + 安全过程完成且链路加密 + 当前主机已启用 input CCCD + 未 suspend**。断连清除连接态/临时订阅状态；bond/CCCD 是否由栈恢复必须逐项验证，不能每次重启抹掉 bond。S8 的实际 Windows 修复就是将发送准备状态从 onConnect 移到 onAuthenticationComplete；不能把回调出现直接视为无条件加密成功。

S3 最新代码还偏好 2M PHY，这只是 ESP32 优化设置，不是 283-byte map 或 Windows 匹配的需求。旧无线 MCU 能运行 HOGP/SMP 加密/持久化 bond 即可形成合理候选；本研究没有证明原 Steam Controller 栈的具体容量或 API。

### 5.2 MTU 与缓冲

- 默认 ATT MTU **23**：Notify 和普通 Write 的 value 最大 20 字节；input 16、output 8、battery 1 都可直接发送，不需要应用层分包。
- 283 字节是 **Report Map attribute 总长度**，不是单个输入报告长度，也不是所需 MTU。MTU 23 下用 Read / Read Blob，每次最多 22 字节，offset 0、22、44……264（最后 19 字节）取完；需要正确支持长属性读取和合法 offset。
- 无需把 MTU 强制为 286/517；不能用 Steam 自定义 BLE framing/20-byte 分片规则包装 Xbox 报告。
- 静态数据库需容纳 283-byte map、两个独立 `2a4d`、两个 Report Reference、输入与电池 CCCD；每个 value 的 length/max_length 必须对应实际报告，而非全部沿用旧 Steam report 长度。
- 连接间隔可先请求 7.5–15 ms，实际由 host 接受值决定。S6 原设备 preferred 为 7.5 ms；不宣称一定得到 133/1000 Hz。
- 首次准备完成先送一个中立快照；之后按输入更新发送。通知 backpressure 应重试最新完整状态，不能把“未成功排队”当作已交付。

## 6. Windows XInput 证据的精确边界

已知：

1. S7 的 Windows 11 23H2 build 22631.3593 记录中，Series 配置的 Unity description 是 `interface: XInput`，capabilities 有 userIndex=0、type=1、subType=1、左右 motor=65535；相同环境 OneS 配置为普通 HID。这比“joy.cpl 中能看到按钮”或“Steam 能识别”为 Xbox 的证据更强。
2. 使用者随后确认删除旧配对并重新配对后能工作；维护者解释 descriptor/VID/PID 会被缓存。[对应讨论](https://github.com/Mystfit/ESP32-BLE-CompositeHID/issues/2#issuecomment-2118498302)。
3. 维护者另称该配置加载 `Bluetooth LE XINPUT device` 和 HID-over-GATT 驱动：[comment 2700597556](https://github.com/Mystfit/ESP32-BLE-CompositeHID/issues/32#issuecomment-2700597556)。这是维护者报告，不是本研究提取的 Windows INF 或驱动反编译结果。

未知/不能推出：

- 未获得微软驱动所有匹配条件的源码/INF 完整规则，不能证明“只要 VID/PID 即可”；也未确定 Product Version、HID Information flags、descriptor 内容分别是否参与选择。
- 第三方 ESP32 成功不证明本项目官方固件补丁正确，也不证明所有 Windows 10/11 build、适配器、缓存状态都可用。
- Steam Input 可以把普通 HID 变成虚拟 XInput；浏览器 Gamepad API / joy.cpl / 名称为 Xbox 均不能单独证明系统原生 XInput。
- `XInputGetState` 的标准结构不包含 Guide/Share；Guide 通知正确与某个普通 XInput 测试程序能读 Guide 是两回事。Guide/Share 可在系统/其他输入 API 层另行观察。
- 未研究 Xbox 主机无线认证；这里目标是 PC 上的 BLE/HOGP/XInput，不是 Xbox 专有无线或 USB GIP。

今后补丁运行验收至少需要记录：Windows build、蓝牙适配器/驱动版本、配对后的硬件 ID/实际驱动绑定、关闭 Steam Input 等转换层后的 `XInputGetState` 成功与轴/扳机/按钮结果、`XInputSetState` 导致的 8-byte output、断电重连成功。**这些是待做验证，本任务没有执行。**

## 7. 给固件补丁/生成器的交接

1. 固定上述 map 字节及 hash，静态断言 283；输入序列化严格 16 字节、输出解析严格 8 字节。显式写小端字节，避免 ARM C bitfield/未对齐 packed struct 产生 ABI 差异。
2. 模式选择应在开始广播、建立 GATT 数据库前锁存。“右肩RB+Steam”选择 Xbox 后，输入再转换到此布局；启动手势释放前抑制其游戏输入，不能无意留下第一帧 Guide 长按。
3. 官方 Steam 模式与 Xbox 模式的 GATT/报告长度不同。若继续共用相同 BLE 地址，Windows 缓存与 bond/CCCD 行为是必须解决的实现问题；不能靠更换显示名称清除缓存。
4. 切换 PnP/名称/Report Map 不够：必须同时切换服务、Report References、input framing、output 接收、BAS 和安全/订阅状态机。原 Steam 服务的 characteristic UUID 不能替代标准 HIDS。
5. Guide 使用 `buttons |= 0x1000`，不另造 Report 2；Battery 用 BAS，不另造 Report 4；Share 可以置 0。左右触发器保持独立 10 bit，不提前合并成 DirectInput 单轴。
6. 输出 strong/weak 顺序按第 3.3 节；Steam Controller 两个触觉执行器如何映射四个 Xbox 通道是本地功能设计，不能由 descriptor 推断。未完成物理映射时，只能说明支持接收命令。
7. 无线 MCU 须能处理完整 283-byte 长属性读、标准通知及加密配对；主 MCU 须在此模式产出所需输入并传递模式状态。**本研究不建立双 MCU 地址、补丁入口、校验算法或签名可修改性。**
8. GitHub Actions 可以验证 map hash、report 位宽、构建产物可重现性；CI 构建通过不应自动标记 Windows/XInput 或刷写可用。

## 8. 结论状态

| 问题 | 状态 |
|---|---|
| 最佳公开目标及完整 map | 已回答；283-byte Series BLE，三个来源逐字节一致 |
| Input / Guide / Share / Output 格式 | 已回答；马达字段的上游冲突已指出，精确重复时序仍需硬件对照 |
| Battery 与旧 ID4 差异 | 已回答；Series 用 BAS |
| PnP / Appearance / name / GATT | 可实现基线已回答；Series PnP version 与 HID Info 来自实现，完整同型号 GATT 抓取仍缺 |
| 安全与 MTU | 可实现基线已回答；默认 MTU23 足够，长属性读取、bond/CCCD/加密重连不可省略 |
| Windows 原生 XInput | 有第三方 Series ESP32 实测；本项目与系统匹配完整规则仍未验证 |
| Steam Controller 双 MCU 官方固件补丁实际可运行 | 本子任务未验证；需主任务的固件证据与后续授权设备验证 |
