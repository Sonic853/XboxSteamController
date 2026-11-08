# XboxBLE 独立配置网页

入口：`web/index.html`。纯静态 HTML/CSS/ES modules，无构建步骤、第三方依赖或应用后端。视觉及基础交互参考 Receiver 网页 22.0；配置直接通过 **Web Bluetooth** 访问手柄本体。

## 使用

1. 使用支持 Web Bluetooth 的 Chrome / Edge，通过 HTTPS 静态站点或 `http://localhost` 打开 `web/`。直接双击 `file://` 不是支持的入口。可部署整个 `web/` 目录，目录内所有模块必须一起发布。
2. 手柄需装有**新版成套本体固件**，进入 **XboxBLE**。按住**右肩 RB + Steam** 开机进入 XboxBLE 并恢复连接；**RB + Y + Steam** 开机进入 XboxBLE 配对，连接后松开启动组合键。
3. “Xbox BLE 按键布局”始终可见，可离线编辑默认布局。ABXY预设支持Xbox标准排列、交换A/B与X/Y；预设只改变四个面键。16个实体按键按面键、肩键/扳机、菜单键、背键/按压分组，每个均可自定义Xbox输出，并实时预览面键排列。
4. 点击“连接 / 授权手柄”，选择 `Xbox Wireless Controller`。网页读取并校验设备配置作为保存基准；未编辑时显示设备布局，已编辑时保留本地草稿。点击“保存到手柄”提交。预设和“填入默认配置”只填写表单，不进行设备I/O；保存完成要求真实持久化ACK及一致回读。
5. 冲突、失败、超时、断连后，按提示**重新读取核对**；连接已释放时先重新连接。读取操作会替换当前表单草稿。没有自动提交重试。

界面可切换中文 / English，仅语言偏好写入浏览器本地存储；存储受限不影响使用。设备配置、诊断留在页面内存或用户主动下载的 JSON 中，无上传接口。

### 左板斜向区域角度

在“Xbox BLE 按键布局 → 摇杆与方向区域”中调整，与 `Receiver/web` 一致：**0–75°、步长5°、默认30°**。滑动时实时显示“角度°（每个直向90−角度°）”；0°关闭斜向，45°使八方向等宽。该值与按键布局一起保存到手柄，重新连接后可读回；离线修改只是草稿。固件使用相同16项角度索引编码。

### 连接与权限

配置服务**不在广告中广播**，因此设备选择必须使用名称过滤，并把服务加入 `optionalServices`：

```js
navigator.bluetooth.requestDevice({
  filters: [{ name: "Xbox Wireless Controller" }],
  optionalServices: ["85300001-2015-4c53-8f44-58426c65434d"],
});
```

| 用途 | UUID |
|---|---|
| 服务 | `85300001-2015-4c53-8f44-58426c65434d` |
| Request | `85300002-2015-4c53-8f44-58426c65434d` |
| Response | `85300003-2015-4c53-8f44-58426c65434d` |

不按服务 UUID 扫描，不调用 WebHID，不提供 Receiver / 输入模式选择器。同名的普通 Xbox 手柄没有本工程私有服务。服务发现失败时核对固件和浏览器权限；设备列表为空时核对 XboxBLE 配对/可发现状态。

## GitHub Pages 部署

部署工作流位于 `.github/workflows/pages.yml`，上传目录为 `web/`。将 `XboxSteamController` 文件夹内的内容放在 GitHub 仓库根目录；仓库需支持并启用 Pages。

1. 在 **Settings → Pages → Build and deployment → Source** 中选择 **GitHub Actions**。
2. 首次部署可在 **Actions → Deploy controller configuration page → Run workflow** 手动运行。
3. `main` / `master` 分支推送涉及 `web/**`、`tests/test_web.mjs` 或该工作流文件的更新时，会自动部署。使用其他发布分支时，需要同步修改工作流中的 `on.push.branches`。
4. 工作流检查入口脚本语法并执行网页协议测试，全部通过后上传静态页面并部署。成功后的地址显示在部署任务的 `github-pages` 环境及仓库 Pages 设置中，通常为 `https://<owner>.github.io/<repo>/`。

页面采用相对资源路径，可在仓库子路径下运行；GitHub Pages 提供 HTTPS，满足 Web Bluetooth 的安全上下文要求。使用支持 Web Bluetooth 的 Chrome / Edge 打开在线页面，再连接手柄。实际发布以 GitHub Actions 部署成功记录为准。

## 配置协议 1 / 固件修订号 2+

所有数字采用 little-endian，所有 request/response **固定 20 字节**，无 Report ID。

```text
request  = [opcode:u8, seq:u8, arg:u8, 0, payload:16]
response = [opcode:u8, seq:u8, status:u8, arg:u8, payload:16]
```

- Request 仅用 `writeValueWithResponse()`。写入方法完成不代表业务处理或持久化完成。
- 随后对 Response 调用 `readValue()` 轮询。仅接受 **opcode、seq、arg 全部相同**且 `status != 4` 的响应。写后可以先读到旧 response，不能仅凭 opcode 判断。
- seq 为递增 8-bit 值（255 后回到 0），连接时随机初始化；不使用通知订阅。
- 本页只有一个 GATT 事务队列。完整分页读取、两页暂存/提交/回读、六页诊断分别独占队列，彼此不交错。
- 普通读请求的本地预算为 **5 秒/命令**，覆盖 request 写入及 response 轮询。暂存两页和 commit 合用 **12 秒**预算；成功 ACK 后回读使用普通读预算。响应未就绪时每约 50 ms 再读。
- 超时关闭 channel 并释放 GATT 连接，拒绝排队工作；浏览器底层 Promise 无法取消，迟到结果不能复活 channel。断开立即中止活动等待与后续队列。
- 不重发 stage 或 commit。只在获取配置快照时允许重复**只读请求**。

### Status

| 值 | 名称 | 页面处理 |
|---:|---|---|
| 0 | ok | 根据对应操作校验载荷 |
| 1 | invalid | 设备拒绝参数；保存失败，重新读取核对 |
| 2 | conflict | token / 暂存内容冲突，重新读取后编辑 |
| 3 | busy | 设备忙；停止本次保存，重新读取核对 |
| 4 | pending | 继续读 Response，不当作成功 |
| 5 | timeout | **持久化结果未知**，重新读取核对 |
| 6 | notloaded | 配置尚未加载，重新读取核对 |
| 7 | storage_error | 存储错误；保留并显示 `payload + 4` 的 u32 hostStatus，重新读取核对 |

未知状态、无效长度、异常 ACK 都拒绝接受。保存时的传输错误、断连、本地超时不能被解释为“没有写入”，也不能解释为保存成功。对设备明确失败/冲突的提示仍要求重新读取。

### 读取（opcode 1）

请求 payload 全零。

| arg | 响应 payload |
|---:|---|
| 0 | `mapping[0..15]` |
| 1 | `mapping[16..23] + tokenCRC32:u32 + hostStatus:u32` |

合并 24 字节后，先校验 token 是否等于完整 mapping 的标准 CRC32（反射多项式 `0xedb88320`，init/final XOR 均为 `0xffffffff`），再校验格式及数值范围。CRC 不一致可能是分页间数据变化，最多尝试三组完整只读快照；仍不一致不发布表单或保存基准。CRC 校验通过不替代格式校验。

### 暂存（opcode 2）

| arg | 请求 payload |
|---:|---|
| 0 | `expectedCRC32:u32 + mapping[0..11]` |
| 1 | `mapping[12..23] + zeros:4` |

`expectedCRC32` 是编辑前成功读取的 token，不根据当前草稿重新计算。每一页都必须等到匹配的 `status 0` ACK 后才发送下一页；第二页尾部四字节必须为零。

### 提交（opcode 3，arg 0）

**提交 payload 不是全零**：

```text
mappingCRC32:u32 + expectedCRC32:u32 + zeros:8
```

- `mappingCRC32` = 拟写入的完整 24 字节 mapping 的 CRC32。
- `expectedCRC32` = 读取时的当前 token，与 stage0 一致。
- 固件提交前比较暂存完整 CRC 与前者、暂存 expected token 与后者，阻止两个网页/进程交错 stage 后误提交混合数据。不匹配返回 `status 2`，不写 Flash。网页本地队列只能串行本页，所以这两项跨客户端保护必须保留。
- Commit **只调用一次**，之后只读 Response，直到匹配该事务的非 pending 结果。
- 成功响应 payload：`tokenCRC32:u32 + hostStatus:u32 + zeros:8`。校验 token 等于拟写入 CRC、尾部全零后，才进入“已持久化 · 正在回读”。
- 随后重新读取并校验整份 mapping，逐字节比较一致，才进入“已持久化 · 回读一致”。
- 已收到有效成功 ACK，但后续回读失败/改变：单独显示“已收到持久化 ACK · 回读未确认”，不能伪装成全部成功或完全未写。
- 任何保存失败都会丢弃可写基准，必须明确重新读取后才能再保存。已排队的第二次保存不能偷偷采用更新后的 token。

## 24 字节 mapping

| 偏移 | 长度 | 字段 |
|---:|---:|---|
| 0 | 1 | version = 1 |
| 1 | 1 | flags：bit0 invertRX、bit1 invertRY、bit2 leftClickRequired；**bit3 必须为 0**；高四位 angle index |
| 2 | 16 | 每个源按键的 target 0..17 |
| 18 | 2 | stickDeadzone，0..16000 |
| 20 | 2 | padDeadzone，0..16000 |
| 22 | 2 | dpadThreshold，256..30000 |

源顺序：`A, B, X, Y, LB, RB, View, Menu, Steam, 左背, 右背, stick click, left pad click, right pad click, LT底, RT底`。

target 顺序：`无, A, B, X, Y, LB, RB, View, Menu, Guide, L3, R3, D-pad上, 下, 左, 右, LT, RT`。

角度 index 表：`[45,5,10,15,20,25,30,35,40,0,50,55,60,65,70,75]`；页面使用 0..75°、步长 5° 的滑块，编码按表查找，不直接把角度除以 5 当 index。

默认 targets：`[1,2,3,4,5,6,7,8,9,11,10,10,0,11,16,17]`。默认 DZ=3000/3500、threshold=8000、flags=`0x64`（30° index 6 + 左板按压门控）。**左背默认 R3、右背默认 L3**；左板按压本身默认不另映射按钮。多个源可以映射到相同目标。

## 诊断（opcode 0x10，arg 0..5）

请求 payload 全零，每页响应 payload 16 字节：

| arg | 字段（按偏移顺序） |
|---:|---|
| 0 | identity:u32=`0x32435358` (`XSC2`), firmwareRev:u16=2, protocol:u8=1, configFlags:u8, ticks:u32, hostStatus:u32 |
| 1 | rx:u32, parseOK:u32, parseBad:u32, lastLength:u16, received:u16 |
| 2 | hvxAttempts:u32, hvxSuccess:u32, lastHVX:u32, conn:u16, securityModeLevel:u8, keySize:u8 |
| 3 | secError:u32, cccdError:u32, cccd:u16, blockReason:u8, profileFlags:u8, serviceCalls:u32 |
| 4 | buttons:u32, suppress:u32, lastHeader:u16, reserved:6 |
| 5 | 当前 Xbox 16 字节报告，无 Report ID |

configFlags：loaded=1、pending=2、staged=4。profileFlags：profile=1、initialized=2、suspended=4、bootProtocol=8。页面校验 XSC2、固件修订号至少 2、协议为 1。

blockReason：0 ready、1 disconnected、2 suspended、3 boot protocol、4 security error、5 unencrypted、6 CCCD error、7 unsubscribed、8 incomplete input、9 no tick（该名称仍标注待核实）；未知值显示原始数值。

报告解码：0/2/4/6 偏移为四个 u16 轴（中立 32768），8/10 为两个 10-bit 扳机（容器 u16），12 低四位 hat（0 中立，1..8 从上开始顺时针），13–14 为按钮位图，15 bit0 为 Share。按钮 A/B/X/Y mask 分别是 `0001/0002/0008/0010`，不能按连续四位解析。

页面同时显示：接收/解析/HVX 计数、发送阻塞原因、全部协议字段、Xbox 按钮指示、轴/扳机条、hat、原始 16 字节报告、原始 buttons / suppress 掩码。`keySize` 只是加密密钥**长度**，不是密钥内容。

手动或勾选每 1 秒刷新。使用递归计时器，当前刷新结束后才安排下一次；保存/其他操作时暂停，隐藏页面时暂停，断开或离开页面时清理计时器。已开始的诊断与保存仍经过同一事务队列。关闭刷新不能中途打断已发送事务，但不会产生下一轮。

JSON 导出仅包含解析后的六页诊断、采样起止时间、导出时间和当前连接标记；**不含绑定密钥**。断开后可导出最近历史采样，页面明确标注离线。六页不是同一时刻冻结快照，不以报告或 HVX success 证明系统/游戏已经收到输入。

## 文件及验证

- `web/protocol.mjs`：编码、CRC、串行 GATT 通道、配置保存状态机、诊断解析。
- `web/manager.mjs`：按名称发现、服务访问、可停止的诊断轮询。
- `web/app.mjs`：表单、状态、诊断展示、JSON 导出、连接生命周期。
- `web/i18n.mjs`：中英文文本和语言偏好。
- `web/index.html` / `web/style.css`：响应式深色页面，沿用 22.0 的卡片/绿色强调视觉。
- `tests/test_web.mjs`：无需依赖，用真实 20-byte 协议 mock 设备，覆盖 CRC/格式、旧响应、分页撕裂、状态错误、pending、单次 commit、双 CRC guard、交错 stage、回读不一致、并发队列、超时/断开和轮询计时器清理。

运行：

```text
node --test tests/test_web.mjs
```

这些是软件协议与生命周期测试，不是硬件连通性、实际持久化或 Windows/XInput 验证。浏览器视觉/交互检查及硬件验证由主任务单独进行。
