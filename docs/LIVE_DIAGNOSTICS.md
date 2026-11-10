# 实机无输入诊断：2026-11-09

## 已观察事实

本轮设备由用户首次添加到 Windows；不能把旧配对/GATT 缓存当作本次故障原因。

- Windows PnP 中存在本工程配置服务 `85300001-2015-4c53-8f44-58426c65434d`、HID 服务与 HID game controller，问题代码为0。
- Windows WinRT 在当前蓝牙连接上完成 **Uncached** 服务发现，状态为 `Success`。配置服务实际可访问，诊断请求/响应通信成功。
- `XInputGetState(0)` 返回0，packet=1、输入中立；其余槽返回1167。
- 诊断身份：XSC2、固件修订号2、协议1。配置已加载，hostStatus=0。
- ticks、serviceCalls 正常递增；接收与解析成功数递增，parseBad=0。输入块掩码为 `0x0ff0`，包含发送要求的全部 `0x03b0` 块。
- 连接号0；加密模式/等级 `0x21`、密钥长度16；无安全查询或CCCD查询错误。
- **固件读取的 CCCD=0，blockReason=7，HVX attempts=0、success=0。**
- 用户打开 `joy.cpl` 测试页并按住A时，原始按钮为 `0x00000080`，映射后的Xbox buttons为 `0x0001`；启动抑制为0。此时CCCD仍为0、发送尝试仍为0。

原始记录保存在工作区 `analysis/xbox-live-diagnostics-20261109.json` 和 `analysis/xbox-live-A-held-20261109.json`（未包含在本仓库中）。诊断通过临时 Windows PowerShell/WinRT 读取器完成，只发送配置协议诊断 opcode `0x10`，未暂存/提交映射、未刷写设备。

## 边界与下一证据

这些观察已证明当前设备上主控输入、UART解析、按键转换、计时、加密和配置通道可工作；当前发送停止在CCCD门控。**尚不能由CCCD=0判定是Windows未写订阅，还是原固件/绑定恢复路径随后清除了订阅。**

Windows 对系统占用的HID特征枚举返回 `AccessDenied`，因此没有通过用户态WinRT读取实际HID描述符，也没有修改HID订阅。

网页的设备选择框未列出手柄，发生在GATT连接之前；这与本机已成功读到配置服务是不同观察。已连接的外设可能停止可发现广播，但尚未采集浏览器扫描证据，不能把该解释当作已确认根因。

现有原指令证据支持HIDS输入value/CCCD句柄字段位于 `HIDS+20/+24`；当前编译产物在 `sd_value_get` 返回后确实从缓冲区读取CCCD。SDK约定与调用参数一致。下一步应记录连接期间的CCCD WRITE事件、SYS_ATTR_MISSING事件、实际输入/CCCD句柄及查询长度，区分“从未订阅”与“订阅随后被清除”，再修改相应路径。

## 修订号3：订阅诊断版

用户同意生成诊断版。新增记录不修改订阅、不绕过输入发送门控，也不改变现有绑定/映射格式。页面0修订号为3，页面0–5的其余字段兼容原网页；诊断opcode仍为 `0x10`，新增页面6、7。所有多字节整数为little-endian，计数从本次开机累计（16位计数可回绕）。

|页|偏移|字段|
|---|---|---|
|6|0 u32|针对当前输入CCCD的有效WRITE事件数|
|6|4 u16|最近一次CCCD写入值|
|6|6 u16|SYS_ATTR_MISSING事件数|
|6|8 u16|原HIDS连接号|
|6|10 / 12 u16|输入value / CCCD实际句柄|
|6|14 u16|最近一次CCCD查询返回长度|
|7|0 / 2 u16|显式启用 / 禁用notify的WRITE次数|
|7|4 / 8 u32|最近CCCD WRITE / SYS_ATTR_MISSING发生时的tick|
|7|12 / 14 u16|成功查询到的notify位0→1 / 1→0次数|

采样转换计数只能观察主循环采样点之间的变化，不保证捕获两个采样点之间的短暂启用/清除；事件计数覆盖WRITE_REQ/WRITE_CMD的完整2字节、offset=0写入。CCCD=0且启用WRITE数>0、禁用WRITE数=0，提示需要继续追踪固件内部系统属性恢复/清除；启用WRITE数=0时继续检查主机订阅流程，不能只据此认定Windows有错。

构建命令：`python tools/build.py --output build/subscription-diagnostic`。当前诊断版已通过130项Python/ARM测试（强制依赖，0 skip）。原先的 `build/` 顶层固件保持原版本；诊断产物在独立子目录。

网页23项测试通过。诊断radio镜像136,436字节，payload5,364字节、永久RAM288字节；SHA-256为 `d56bcdbd771a6677b5c248852b66261a36ebb99a9eb10729010c9be06c952326`。host镜像与修订号2相同。六项产物已复制到更新器的 `updatescripts/fw_images/xbox-diagnostic/` 并重新核对校验清单；对应入口为更新器根目录的 `xbox-diagnostic.bat`。尚未执行诊断版刷写。

Windows原生读取器可直接打开已配对设备，不依赖浏览器扫描发现：

```powershell
powershell.exe -NoProfile -File tools/read_ble_diagnostics.ps1
```

多个同名手柄时使用 `-Address <目标蓝牙地址>`；默认采样2次并在当前目录生成带时间戳的JSON。读取器只发送诊断请求，不保存映射或刷写固件。修订号2读取6页，修订号3读取8页；原网页目前仍显示前6页。

## 诊断版 host 刷写验证失败：2026-11-09

用户直接执行原 `ValveFirmwareUpdateApp.exe` 和诊断 `host.bin` 的绝对路径，日志依次为进入bootloader、`Image checksum: fc1d6395870cd0d5e1c936d852f16cba`、传输、`Verifying...`、`Error updating firmware.`。诊断host与此前第二版host逐字节相同（SHA-256 `4e7fd8228b69d1370260ba5c64f2ea24d0040e12c3d8e4351d0368d15b26ba50`，84736字节）。

对原更新器（SHA-256 `aae5138bf2aec2cdc651b93b68db80ad5f2853441d1cc1137fb60cb3ecada46f`）进行REA静态分析，未执行更新器：

- `0x404200` 算法不是MD5，而是四个32位字的移位/异或签名。Controller vtable指定传输从文件 `0x2000` 开始，校验另跳过 `0x30` 字节，即覆盖 `[0x2030, EOF)`，不足16字节以FF补齐，没有额外固定长度填充。按该算法离线计算现有host，**精确复现用户日志中的校验值**。此前整文件MD5不相等不是文件错误证据。
- `0x4043c0` 在 `Verifying...` 后发送 `0x93` 和16字节签名，再读取状态；只有读成功且状态为0才发送完成命令。`0x4057c0` 将不同失败原因合并显示为 `Error updating firmware.`。因此现有日志不能区分校验/Flash提交失败与ACK读取失败。
- `0x401570` 读取状态，等待opcode `0x94`、状态非2；启用调试后打印 `ack ... retries ...` 或 `ack failed ...`。`0x406e60` 和字符串 `0x40bc88` 确认开关是 **`-d`，且必须放在文件名之前**。
- 现有原bootloader导出代码：`0x13d0` 写入512字节缓冲；`0x6d8` 冲刷末块、调用 `0x1094` Flash硬件签名比较，再改写首应用扇区有效标志。`0x1094` 从 `0x2030` 校验到接收数据结束（16字节向上取整）。现有host长度未触及其Flash上界；这些静态事实不证明本次USB数据完整到达或IAP成功。
- 2026-11-09 07:06:39 UTC通过Windows `HidD_GetFeature` **只读**访问当前 `28DE:1002`，收到版本响应 `83 0f ...`，bootloader版本 `0x55d2926d` 与原镜像声明一致。它不是更新ACK，不能拿其中字节当作失败状态，也不能确定最后查询版本的程序。原始记录在工作区 `analysis/xbox-bootloader-read-20261109.json`，读取工具为 `analysis/read_bootloader_status.py`。
- 随后进程查询未发现Steam/steamwebhelper/更新器运行。没有据此前Steam曾运行就认定它是根因。

下一步由用户在Steam已退出状态下使用 `ValveFirmwareUpdateApp.exe -d <诊断host绝对路径>` 重试并提供ACK输出；助手本轮未执行刷写。尚未修正镜像或声称恢复成功。

REA证据：校验函数 `ev_fd648c2bafe9abd3f90023761acaa8136b759c790927815672da2ad29f992f38`；更新流程 `ev_11658422f6bd120363042507577dfb6fe6dde2ce4f1078e745fd936a30beac25`；ACK读取 `ev_41abde91ae919d70002c069568a86d4bb5ffdbc3eafdec6291338b8de33e5cb7`；命令行 `ev_e257803dabaab4c00ed1472282867fa4ed34b08f4914a61e8ca015671ec08ab2`；`-d`原字节 `ev_9cf4db3a50bf855008095ed07a348cf8a4ee31e4c7445855264fee2a101eb3c9`。完整本轮证据导出至工作区 `analysis/rea_updater_host_verify_20261109.evidence.json`。

## 修订号3实机结果及Windows订阅路径

用户随后确认已刷好并连接，joy.cpl全部按钮无响应。2026-11-09 07:16:36–37 UTC原生读取确认修订号3运行，配置加载成功、hostStatus=0。此次Windows枚举地址为 `EF467620F588`，与此前不同；系统事件记录了该地址成功配对，不能归因于旧地址的缓存。

- 接收/解析成功均10980，parseBad=0、完整输入mask `0x0ff0`。
- ticks从8589到8689，serviceCalls从20927到21057；输入静止期间接收计数不增长本身不是故障。
- 应用/HIDS连接号均0，加密 `0x21`、密钥长度16。CCCD读取成功且长度2。
- 实际输入value句柄 `0x10`、CCCD句柄 `0x11`。CCCD=0，blockReason=7，HVX attempts/success均0。
- **CCCD WRITE计数0、启用/禁用WRITE计数0、采样0→1/1→0次数0、SYS_ATTR_MISSING计数0。** 本次开机没有捕获有效订阅写入或订阅后清除，下一步检查Windows发现/订阅流程；不能仅据此认定Windows有错。
- 离线运行原S110 `0x388e` 的2字节CCCD写入事件序列化，再经原 `0x187d8` 分发进入诊断hook，生成长度34、op+8的事件，页6正确记录WRITE=1、最近值1。排除了诊断代码因这一已验证事件布局而漏计的疑点；它仍不是空中捕获。

记录：工作区 `analysis/xbox-subscription-live-20261109.json`、`analysis/xbox-cccd-serializer-check-20261109.json`。

Windows PnP确认使用 `xinputhid.inf` / `mshidumdf`，版本 `10.0.26100.9549`，ProblemCode=0。原生Win32 GATT元数据查询成功：HID服务14、输入value16/CCCD17/report-reference18、输出value20/reference21、map23、HID information25、control point27，与固件句柄一致。实际HID属性值仍受系统接口限制：打开HID服务接口返回Win32错误31，通过父接口读取缓存/设备值均返回 `0x80070001`。因此本轮只证明服务/句柄/属性存在，没有直接读到实际Report Map或Report Reference内容。工具没有订阅或写CCCD。元数据摘录 `analysis/xbox-windows-hid-inventory-20261109.json`，工具 `analysis/read_windows_gatt.py`。

为定位Windows侧路径，静态分析本机 `Microsoft.Bluetooth.Profiles.HidOverGatt.dll`（SHA-256 `09648a6511eea778b3b72a149ddbc585422bed55ab35ac19769c6f065bbd61ad`），并从微软符号服务器取得匹配GUID `d8dedb1d-c183-4e88-1ff9-e2d62c70feab`、DBI age1的公开PDB（info-stream age3，SHA-256 `62130fdd03d986b1ea8cb6d0cdda2e0856719a37fee9c61a02f2429fa7412343`）。没有修改/加载替换驱动：

- `ValidateAndAddReportCharacteristicToMap` (`0x18001a484`) 检查Report Reference及输入Read/Notify、输出Read/Write/WriteWithoutResponse属性；Notify项还需CCCD。静态流程不能证明本次执行到哪一分支。
- `SubscribeToReportNotifications` (`0x180018ab0`) 对已验证的Notify报告注册回调、读取/设置CCCD；因此后续应记录驱动的实际连接过程，而非强行在外设设置订阅。
- WPP provider `HidOverGattTraceControl` GUID从匹配符号位置 `0x18002af28` 原字节恢复为 `{0107cf95-313a-473e-9078-e73cd932f2fe}`。
- 当前非提升环境执行 `logman ... -ets` 返回拒绝访问，**未启动日志会话**。已提供更新器目录下 `xbox-hogp-trace.bat`，由用户管理员运行、RB+Steam重连并操作后按键停止，生成16MB上限的循环ETL、日志和当前8页固件诊断。该脚本不刷固件、不修改配对。

REA证据：报告验证 `ev_ed76c2a23947bc1b1b6c35ea415052d96b0a31a5104daeb1d2e32850fc40fd19`；订阅流程 `ev_4a75cdc5d81f2b10d34cc559c86a121d0dc289328d43ced2615a51b1c58143ed`；provider GUID `ev_4a6e969e73bfe4d6bbfe49e9b5fe2689d101d95fe07f1d7b31aef43b5479a7b9`。完整证据 `analysis/rea_windows_hogp_20261109.evidence.json`。尚未确认无输入根因或实机修复。

## 已保存连接ETL：Windows在重连时直接跳过订阅

用户完成停止保存后，`analysis/XSC-HOGP-25029-24795.etl` 大小106496字节，SHA-256 `adbe09cc8b89d7f80bb1be2a9a59103924e0703936a1585f2ff9ea3efd2017d6`。采集区间为2026-11-09 07:31:48.9267909–07:33:26.4244145 UTC，约97秒；13个buffer、189条事件，EventsLost/BuffersLost均0。日志包括185条WPP事件及4条系统元数据；DbgIdRSDS与上述微软PDB GUID/age完全一致。

`tracerpt`缺少TMF，只输出“No Format Information found”，但原始参数仍在ETL中。新增离线工具 `analysis/extract_etl_userdata.py` 使用Windows `OpenTraceW/ProcessTrace` 的 `EVENT_RECORD` 回调恢复全部UserData，事件顺序、ID、GUID、PID/TID与XML逐条一致。原始结果 `analysis/XSC-HOGP-25029-24795.raw-events.json`；匹配驱动调用点的解码结果 `analysis/XSC-HOGP-25029-24795.decoded.json`，重放工具 `analysis/decode_hogp_trace.py`。解码标签是汇编对应分支的描述，**不是恢复出的微软原始TMF文本**。

以下GUID使用缩写：A=`bebcb148-69ce-3370-1311-269c2f880c36`（HogpGattAdapter，66条），B=`5322f6b8-d37c-36c8-e594-7632f32ffdc7`（HogpIo，119条）。两组共同前缀为8字节远端地址和2字节静态UTF-16空格；本次所有WPP事件地址均为 `EF467620F588`。

### 实际执行分支

1. 07:32:15.1502504 UTC开始：A/0x41（服务状态回调）→A/0x24（订阅入口）→**A/0x26**（因 `this+0xc8 != 0` 提前返回）→B/0x27（connected=1）→A/0x42。07:32:15.1810126记录connected=0；07:32:15.2012322–2012498再次经过同样的订阅提前返回分支并connected=1。
2. `SubscribeToReportNotifications(force=false)` 的A/0x26分支位于 `0x180018d2e..0x180018d9a`；它只检查驱动对象的“已完成订阅”标记，随后返回。**这不是A/0x2b的“读取CCCD后发现已启用”分支。** 本次未出现逐报告处理0x28、写CCCD 0x2a、读取后跳过0x2b或完成0x2c事件。
3. 4次 `IOCTL_HID_READ_REPORT=0x000b000b` 进入HidReadReport，B/0x22表示已排入读取队列，B/0x26返回 `STATUS_PENDING=0x103`；当前日志没有报告到达/输入完成事件。另4次0x000b0013请求同步成功，与按钮数据无关。
4. 8次 `IOCTL_HID_WRITE_REPORT=0x000b000f` 走完整输出写入路径：A/0x61→0x62→0x65→0x66→0x78→**0x7b**，随后B/0x3e完成状态0。A/0x62参数均为Report ID3、8字节 `0f 00 00 00 00 ff 00 eb`。这证明本次Windows输出报告路径成功，并非整个HID服务不能工作；它不证明输入报告已被正确纳入报告表。

### 同时段固件状态

采集结束后的两次8页诊断时间为07:33:28.2015642及07:33:29.1321552 UTC，修订号3、loaded=true、hostStatus=0。接收/成功解析从5150增长到5216、parseBad=0、完整mask=`0x0ff0`；加密=`0x21`、密钥16、应用/HIDS连接号0。CCCD读取成功、长度2，输入value16/CCCD17；CCCD=0、blockReason=7、HVX attempts/sent=0。页6/7的WRITE、启用、禁用、变化和SYS_ATTR_MISSING计数仍全部0。

**目前确定的是本次重连为何没有重新订阅：Windows已持有内部完成标记，直接跳过订阅；固件实际订阅状态却为0。尚不确定这个标记最初如何形成。** 这段采集没有首次报告发现/验证和首次订阅过程，不能据此断言首次配对无输入由旧配对缓存、CCCD持久化丢失或Windows缺陷导致。原始首次添加时无输入的用户事实仍成立。

离线再次运行原HIDS初始化指令链：输入属性0x1a、Report Reference=`01 01`，输出属性0x0e、Report Reference=`03 02`，Report Map283字节，均符合当前补丁意图。这是原代码在模拟SVC边界上的注册参数，不是实机属性读取；无法替代首次Windows枚举日志。

### 下一次采集针对驱动初始化

仅关机/重连仍会保留同一Windows驱动对象，因此新增 `tools/capture_hogp_initialization.ps1` 和更新器目录的 `xbox-hogp-init-trace.bat`。管理员运行后先开启同一WPP provider，再用 `pnputil /restart-device` 精确重启该手柄的HID服务实例，记录报告发现/验证及初次订阅；用户测试后按Enter停止保存，再读取固件诊断。它保留配对，目的是补齐首次初始化路径的证据，不把重启本身当作修复。

当前只读选择器恰好找到一项 `...1812...EF467620F588...000E`。脚本通过PowerShell语法检查及6个隔离模拟场景：正常、目标歧义、trace启动失败、设备重启失败、trace停止失败、诊断读取失败；验证启动失败不重启设备、重启失败仍停止trace、诊断失败保留已停止日志。助手尚未执行实际设备重启。固件没有因本次ETL结果而改动，实机修复仍未确认。

### 静态证据与反编译边界

本轮完整证据：`analysis/rea_hogp_etl_decode_20261109.evidence.json`，快照为同名 `.snapshot.json`。

- 订阅分支：`ev_4a75cdc5d81f2b10d34cc559c86a121d0dc289328d43ced2615a51b1c58143ed`。
- 服务状态变化：`ev_2b291a0c7c23badff728ddcb3d5c43d91dc7a25dd341a69e9fd052fd8c8abf33`。**此函数伪代码错误消去了可达分支**：汇编 `0x180015c19` 明确把状态输出指针传给间接调用，`0x180015c6c..0x180015cd0` 根据返回的服务状态处理连接转换并调用订阅；以汇编及实际WPP事件为准，不能使用伪代码中的固定断开状态结论。
- WPP共同参数序列：`ev_5cb003098e41483f00b2b97cac4b73231c7864cda78728dc91590db699b76c0d`；输出数据参数序列：`ev_c073384168fa506cdb083e7c022fadc34d391ddd1d89ec785825f3ea34960006`。
- HID读取排队：`ev_a5678f120ef0cd1e20ac8629b2e1a4e52138fc1e48ec550ff6cb06299da3a25a`；IOCTL分发：`ev_19d94d1b2739e9762df9234e46041361935a0c2b06b08361a77d10d0ed428766`。
- 输出报告发起：`ev_0bd755faf9616e30e5eb45ebf1b226d59e56cfb54b5a614b326eb0146f4a748f`；写结果成功分支：`ev_1b1c019fec084a1210f4ea084191a9988c7a2cb1d302b749c5badba533370f4a`；IO完成：`ev_0669d86d6e037e5a72cf215451683a75ebe873a95ab952a0db46741ff661083e`。

## 初始化采集受阻：PnP重启返回3010

用户于2026-11-09 07:50:58 UTC管理员运行初始化采集。`logman create`成功；针对上述HID实例的 `pnputil /restart-device` 返回 **3010（需要重启系统完成操作）**；finally中的 `logman stop` 成功。这不是缺少管理员权限。旧BAT对任何非零结果统一提示管理员运行，现已修正：3010单独提示系统重启，PowerShell保存ETL后以3010退出，不输出通用异常堆栈，也不把设备重启未完成当作可继续的初始化采集。

- `analysis/XSC-HOGP-INIT-20261109-155058-d35ded48.etl` 经同一离线解析器读取，只有4条系统元数据、**0条WPP事件**，EventsLost/BuffersLost均0；该文件没有补齐驱动初始化证据。
- System日志 `Microsoft-Windows-Kernel-PnP` Event225于07:51:04.2025672–2025806 UTC记录了四次针对该HID子设备的移除否决，记录号13119–13122：`dwm.exe` PID1192、`explorer.exe` PID9280、`msedge.exe` PID15416、`GameInputSvc.exe` PID34548。由此确认在线重启受在用设备句柄阻碍；不能把这个采集阻塞当作蓝牙无输入根因。
- 原始参数保存在相邻 `.raw-events.json`；系统否决记录保存在相邻 `.pnp-veto.json`。随后只读PnP查询ProblemCode仍为0。
- 后续步骤：手柄关机，用户保存工作并手动重启Windows；登录后先保持手柄关闭，以管理员运行原 `xbox-hogp-trace.bat`，确认开始记录后再用RB+Steam开启手柄，测试joy.cpl，最后回采集窗口按键停止保存。目标是记录新驱动对象的**首次连接/订阅**；不能保证覆盖可能在手柄关闭时已从Windows缓存完成的全部报告枚举。
- 更新后的初始化采集脚本通过语法检查及7个隔离模拟场景，包括3010时仍停止保存trace、保留3010退出状态、跳过测试等待和固件诊断。未执行强制移除设备、终止占用进程或自动重启系统。

## 重启后的两次采集：记录窗口没有覆盖有效HOGP事件

用户完成Windows重启后报告两次记录、按键仍无响应。两份日志均有启动成功、停止成功和完整8页诊断，不能再当作“尚未停止保存”。离线 `OpenTraceW/ProcessTrace` 结果：

|采集文件（analysis目录）|UTC开始|UTC结束|实际时长|WPP事件|丢失|
|---|---|---|---:|---:|---:|
|`XSC-HOGP-9826-15919.etl`|12:23:03.1680715|12:23:27.4154318|24.2473603秒|0|0|
|`XSC-HOGP-8920-4444.etl`|12:23:47.8675302|12:23:48.7101314|0.8426012秒|0|0|

每份8192字节、仅4条系统元数据。DbgIdRSDS仍匹配之前的PDB；重新计算本机HOGP DLL SHA-256也与先前相同，未发现驱动版本改变使现有WPP映射失效的证据。原始事件已保存为相邻 `.raw-events.json`。

第一份诊断在12:23:32.1766209才读取到ticks=50、rx/parsed=39，随后ticks=160、rx/parsed=113。按新增定时器约10ms/tick估算，该轮radio初始化约在12:23:31.7，晚于第一份ETL结束约4秒；这是根据固件计数的时间推断，不是Windows连接事件。**原脚本先停止ETL再读取诊断，诊断阶段才发生的连接不会被记录。** 第二份实际不足1秒，也没有覆盖到可供分析的驱动分支。

第二份诊断rx/parsed为2385→2494，页5从居中报告变为 `1e1efc22008000800000000000000000`，确认输入转换结果在变化。12:27:36再次原生读取（`analysis/xbox-post-reboot-live-20261109.json`）rx/parsed=10392、parseBad=0、mask=`0x0ff0`；加密=`0x21`、密钥16。三组诊断的CCCD、WRITE/启用/禁用/变化/SYS_ATTR_MISSING及HVX attempts/sent均为0，仍blocked=7；此次重启没有实机修复证据。

补查Windows HID解析器接口：`analysis/read_windows_hid.py` 成功读取游戏控制器子接口的preparsed data，Usage=Gamepad(1/5)、InputReportByteLength=16、ReportID=0、Output/Feature长度0。父设备明确有 `xinputhid` UpperFilter及 `WUDFRd` LowerFilter；因此这些是Windows暴露给应用的HID能力，**不是原始BLE Report Map**，不能凭ID/长度与固件不同认定报告描述损坏。观察摘录 `analysis/xbox-post-reboot-hid-caps-20261109.json`。

为解决采集窗口问题，`xbox-hogp-trace.bat` 现改为调用 `tools/capture_hogp_connection.ps1`：

- 启动成功后明确显示 `RECORDING STARTED`；固定记录90秒，普通按键不会提前停止。
- 用户在这段时间内关机、RB+Steam重连、joy.cpl测试。
- **读取固件诊断时保持ETL开启**，随后finally停止保存。
- 自动运行tracerpt导出XML/摘要，显示 `HOGP WPP events saved`；零事件会明确指出，转换失败则保留原ETL并把事件数标为未知。
- 保存 `.capture.json`，包含开始/结束时间、指定测试时长、诊断/转换退出码和事件数。

已通过语法检查及7个隔离模拟场景：正常、零事件、trace启动失败、等待中断、诊断失败、trace停止失败、转换失败；验证诊断始终处于记录窗口、错误路径停止trace、停止后才解析ETL。新脚本尚待用户实际采集。根因仍未确认，未基于空ETL改动订阅逻辑。

## 自动采集得到67条WPP：仍为重连提前返回

用户反馈 `HOGP WPP events saved: 67`。对应 `analysis/XSC-HOGP-TIMED-20261109-203446-93bd700f.etl`，SHA-256 `04353a2cf7ef7019686ad3439502520e4695bdf1a2886bcf1ea102842a75a876`：

- ETL区间为2026-11-09 12:34:46.4202560–12:36:18.9356598 UTC，92.5154038秒；71条事件中67条WPP（A组25条、B组42条），EventsLost/BuffersLost均0。
- 原始回调结果与XML的71条事件逐条一致；67条WPP均已映射到匹配DLL调用点。相邻 `.raw-events.json` / `.decoded.json` 保存原始参数和分析标签。
- 12:35:12.2448605 UTC开始：A/0x41→0x24→**0x26**→B/0x27（connected=true）→A/0x42。只有一次订阅入口，仍因 `this+0xc8 != 0` 提前返回；没有读取CCCD。
- 没有构造函数A/0x0a、0x0b，也没有报告验证0x32、逐项处理0x28、CCCD写入0x2a、读取后跳过0x2b或完成0x2c。因此本次记录完整覆盖了重连窗口，**仍没有覆盖驱动对象的最初初始化**。
- 2次HID输入读取排队并返回 `STATUS_PENDING=0x103`。3次输出Report ID3、数据 `0f00000000ff00eb`，A/0x7b及B/0x3e状态0表明输出成功。输出成功不证明输入报告表正确。

同窗口内固件诊断为12:36:18.1023227和12:36:18.8521589 UTC：修订号3、loaded=true、hostStatus=0、rx/parsed=5325、parseBad=0、mask=`0x0ff0`、加密=`0x21`、密钥16。CCCD读取成功且长度2、输入value16/CCCD17；CCCD=0、blockReason=7、HVX attempts/sent=0，页6/7订阅相关计数全0。这次读取发生在ETL停止前，采集生命周期符合预期；无输入尚未修复。

### 内部完成标记的静态依据

补充检查 `HogpGattAdapter` 构造函数 `0x18001096c`：`0x18001099c` 将R12清零，`0x180010aa7` 执行 `MOV byte ptr [RDI + 0xc8], R12B`。因此该标记的构造初值为0，不能把重连时的1解释为构造默认值。构造函数记录A/0x0a的位置在字段初始化之后、设备/服务设置之前；A/0x0b在这些设置返回之后。

已知订阅函数 `0x180018ab0` 在遍历完成时于 `0x1800192d8` 设置标记1，随后记录0x2c。汇编 `0x180018ea5..0x180018eb2` 表明空报告表也可直接到达该完成位置；非空表中没有Notify项也可遍历结束。因此“完成标记=1”本身不能证明曾成功写过输入CCCD。**当前实机的输入报告是否通过首次验证、首次订阅经过哪条路径仍未知。** 这不是对全部可能写标记位置的穷举，也不是首次执行过程的观测。

构造函数证据 `ev_209f531822ac7b1ed12c0fc8ef672081bc0766df49c9d9375d210ef1699f42ac`；订阅函数复用 `ev_4a75cdc5d81f2b10d34cc559c86a121d0dc289328d43ced2615a51b1c58143ed`。23项完整证据已导出 `analysis/rea_hogp_constructor_20261109.evidence.json`，REA已关闭并保存同名 `.snapshot.json`。

### 下一采集：在记录期间重新配对，复现首次初始化

新增更新器入口 `xbox-hogp-pairing-trace.bat`，复用 `tools/capture_hogp_connection.ps1 -Pairing`，默认自动记录300秒。先看到 `RECORDING STARTED`，再关手柄、在Windows蓝牙设置中移除对应的Xbox Wireless Controller，等待移除完成，用 **RB+Y+Steam** 进入Xbox BLE配对并重新添加，然后测试joy.cpl并保持连接至 `FINISHED`。操作顺序确保移除和重新添加位于记录窗口内。

此次重新配对的目的为**重现最初报告发现/验证和首次订阅并取证**；原先首次添加即无输入的事实不变，没有新增旧缓存为根因的证据。固件源码的绑定重置保留有效本地地址，映射记录与绑定数据独立保留。脚本只提供手动操作提示；诊断仍在记录期间读取，最终保存 `.etl`、`.events.xml`、`.json`、`.capture.json`和日志，后者新增mode字段。普通重连模式默认仍为90秒，显式 `-Seconds` 优先。

PowerShell语法通过；离线生命周期检查覆盖普通90秒、配对300秒、显式配对30秒三种模式，各7个场景全部通过，另验证移除/配对提示在记录开始后且诊断仍在记录期间。实际配对采集待用户执行；下一份ETL需核对构造/验证/首次订阅事件，而非仅凭事件总数认定已覆盖初始化。

## 配对成功、关机重连失效：5242条WPP补齐初始化证据

用户明确观察：RB+Y+Steam配对后按钮正常；关机，再以RB+Steam开机后按钮无响应。`analysis/XSC-HOGP-PAIRING-20261109-204646-d18a17dd.etl`（SHA-256 `9048b2750eae7d1e5c9cfddf495e2fe386d12d388bb7005078a817d52ab1c065`）包含5242条WPP及4条元数据，UTC区间12:46:47.0195708–12:51:49.6055142，302.5859434秒，丢失0。原始回调与XML逐条一致。

本轮Windows对象地址为 **EF61BE2E978D**，配对和随后的普通重连是同一地址；它与之前EF467620F588不同。`bond_store.c`保留地址记录的行为不保证原配对流程必定复用该地址，本次不以旧地址缓存解释故障。

### 已观察时间线

|UTC时间|事件/结论|
|---|---|
|12:47:02.5471533–02.5816443|A/0x0a、0x0b：新HogpGattAdapter构造|
|12:47:02.5825816–02.5856956|两次报告验证，Report Reference分别`01 01`、`03 02`；两次A/0x3a证明已加入报告表|
|12:47:02.5861170|A/0x1b原始ASCII字节还原为283字节Map，与当前固件map逐字节一致（SHA-256 `a681e2cb8b5d9466f258e2f07f0fcc518ec438231af1b61b125c213d2c62e165`）|
|12:47:02.5871213–02.6398976|A/0x24→0x28（声明句柄15）→**0x2a（CCCD句柄17）**→0x78/0x7b（写成功）→0x2c（设置内部完成标记）|
|12:47:02.6400577–12:47:33.9599742|**293次** A/0x44输入通知，全部Report ID1、长度16，共245种不同payload|
|12:47:38.3521931|B/0x27 connected=false|
|12:47:41.3805394–41.3806915|服务状态回调→A/0x24→**0x26提前返回**→connected=true；此后输入通知为0|

12:51:48.6890195与12:51:49.5141827的诊断属于重启后的radio：修订3，rx/parsed=6433、parseBad=0、mask=`0x0ff0`，加密=`0x21`、密钥16；CCCD=0、blocked=7、HVX attempts/sent=0，订阅WRITE和变化计数0。开机计数重置解释了为什么Windows记录中先前有订阅而当前诊断写入数为0。

原始/解码/摘录分别保存为相邻 `.raw-events.json`、`.decoded.json`、`.findings.json`。新增输入通知证据 `ev_83b777d4c4383f759ae12770e1602eefcc09b62094b785c0a7e3827de1c26baa`（`OnReportValueChanged=0x1800157fc`），WPP序列化证据 `ev_7657e26ff145620385ecb0c14adeeb6450370fe83090e6daebe7b1079fbf4179`（`0x18001c758`）证明A/0x44包含地址、ID、characteristic attribute handle和payload字符串。其余1913条未映射事件保留原始字节，不假装已解释全部事件。REA完整25项证据 `analysis/rea_hogp_pairing_20261109.evidence.json`，已关闭并保存同名快照。

### 订阅持久化路径及原指令复现

这次已经证明输入报告发现、验证和首次订阅能工作；新复现故障位于关机后的订阅保存/恢复链。原radio（SHA-256 `db45055fc5251a5800961337454a76fb7a0ccf30a09414f757df5b20d73b96bb`）静态/模拟依据：

- `0x187d8`先分发device manager `0x1c284`；该处理器在auth成功和符合条件的disconnect时调用 `0x1c140` 保存上下文，未为CCCD WRITE执行保存。
- `0x1cd04` 用SVC `0xaa` 读取系统属性，flags=3，最大38字节；48字节上下文写到128字节绑定配置的 `0x38..0x67`。`0x1ccd4`加载该上下文，`0x1cc24`在正确连接/安全状态下通过SVC `0xa9`恢复。
- 配对auth发生在Windows首次订阅之前，因此auth时保存的CCCD仍为0。关机可能切断后续disconnect处理或其延迟写入；仅依赖断开时保存不可靠。
- `0x1e600`暂存更新并启动0x4000 RTC tick（约0.5秒）的合并计时器；`0x1bec0→0x18788`随后把dirty slice经UART交给LPC。原公开store/update wrapper `0x1e5a4/0x1e5d0`丢弃暂存错误码，补丁直接使用有返回状态的暂存函数。
- 新增 `tests/test_radio_bond.py`执行原DM初始化/连接/安全事件、原S110 WRITE序列化、分发和原加载/恢复：旧时序在auth快照后写CCCD=1，没有更新持久配置；冷启动原恢复得到0。测试仅在S110边界提供opaque模拟blob，不能当作真实SoftDevice内部格式或空中捕获。

逐函数原字节及Thumb汇编已保存 `analysis/xbox-bond-persistence-20261109.static.json`。未直接读取该实机的Flash绑定内容，因此具体关机时丢失在哪条指令仍未观测；以上控制流复现和Windows/固件状态一致，支持修复保存时机。

### 修订4修复与交付

收到有效输入CCCD WRITE后，在主循环为**当前已加密且已绑定的同一连接**抓取真实系统属性，更新原pstorage缓存及DM上下文，并沿用原UART→LPC独立Xbox日志保存。既保存启用也保存显式禁用，相同内容不重复暂存；失败退避重试，断连清除待保存请求。没有写入伪造CCCD或绕过发送门控。

诊断修订号升为4，增加页8：u32尝试数、u32暂存更新数、u32最近错误、u16系统属性长度、u8待保存、u8DM连接flags。**暂存更新数不等于LPC提交ACK**；字段不导出密钥或系统属性内容。PowerShell读取器现按版本读取9页，语法检查通过。

验证：完整137项Python/ARM测试通过（强制依赖、0 skip），网页23项通过。新增7项订阅回归覆盖原故障、冷启动恢复并发送、显式禁用/去重、读取失败退避、连接/加密/绑定约束及断连取消、未订阅不被自动开启，以及**实际LPC host_config_write→双扇区日志→冷启动读回→原radio恢复**。最后一项仅替代UART交付和ROM Flash硬件，执行真实host/bond_store代码并核对映射保留。

构建目录 `build/reconnect-fix/`；radio镜像136900字节、payload5828字节、RAM308/472字节，SHA-256 `5cfd1329b7ed46a2823945e7d3715aea120f07dbbaa869b04ee55e186c5d29fc`。host与修订3逐字节相同。6项构建产物已复制到更新器 `updatescripts/fw_images/xbox-reconnect/`，全部校验通过。

入口为更新器 `xbox-reconnect.bat`，执行host→等待4秒→`-R`→SoftDevice+radio，任何失败停止。用户刷写后重新配对一次建立真实订阅，确认按钮正常并等待至少5秒，再关机、RB+Steam重连，重复验证两轮。旧固件没有保存的订阅需由这次真实客户端写入补齐。助手未执行刷写；交付时实机验证待完成，后续结果见下节。早先“首次添加即无输入”的历史记录没有被本轮成功配对观察改写。

### 修订4实机确认

2026-11-09，用户在收到上述刷写及关机重连验证步骤后回复：**“已正常”**。据此将修订4的“关机后RB+Steam重连无输入”问题标记为**用户实机验证通过**。对应交付为 `xbox-reconnect.bat` / `updatescripts/fw_images/xbox-reconnect/`，radio SHA-256 `5cfd1329b7ed46a2823945e7d3715aea120f07dbbaa869b04ee55e186c5d29fc`。

本次确认依据为用户操作反馈；未新增ETL或固件诊断读数。后续日常重连使用RB+Steam，重新配对使用RB+Y+Steam。

## 修订5：双向切换后配对失败（2026-11-10）

用户报告Xbox配对后Steam可发现但配对失败，反向同样失败；已确认Steam使用关机后的Y+Steam、不按RB。核对发现两种模式虽分开绑定和地址，原DM仍在local identity指针为NULL时分发同一个S110默认IRK。

修订5为Xbox在广播前派生并设置独立IRK。完整143项Python/ARM测试、28项网页测试通过；交付入口`xbox-identity.bat`，radio SHA-256为`5f6f68b9a990abb6e1491f2a6f237993dbbdc18a82c5982803de2453a0b028d2`。具体原指令证据、匹配SDK9 ABI和迁移步骤见 [BLE_IDENTITY.md](BLE_IDENTITY.md)。

本次没有实际失败的SMP/Windows配对错误记录；系统是否因共享IRK合并设备仍是待验证解释。未执行刷写，修订5实机双向配对尚待用户确认。
