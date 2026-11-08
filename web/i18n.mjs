// Keys are shared by static markup, dynamic statuses and activity entries.
export const messages = {
  title: ["SC2015 · Xbox BLE 配置", "SC2015 · Xbox BLE configuration"],
  local: ["本地蓝牙 · 无后台", "Local Bluetooth · No backend"],
  headline: ["你的手柄，你的布局。", "Your controller. Your layout."],
  lead: ["通过蓝牙连接手柄本体，调整 Xbox 按键与触控板，查看输入和链路诊断。", "Connect directly over Bluetooth to customize Xbox controls and trackpads, and inspect input and link diagnostics."],
  connection: ["蓝牙连接", "Bluetooth connection"], connect: ["连接 / 授权手柄", "Connect / authorize"], disconnect: ["断开网页", "Disconnect page"],
  compatibility: ["需要新版成套本体固件的 XboxBLE 模式。使用 Chrome / Edge，通过 HTTPS 或 localhost 打开。", "Requires XboxBLE mode in the updated, matched controller firmware set. Open in Chrome / Edge over HTTPS or localhost."],
  startup: ["开始使用", "Getting started"],
  startupNormal: ["按住右肩 RB + Steam 开机，进入 XboxBLE 并恢复连接。", "Hold the right shoulder RB + Steam at power-on to enter XboxBLE and reconnect."],
  startupPair: ["开机进入 XboxBLE 配对。选择 Xbox Wireless Controller，连接后松开启动组合键。", "Hold at power-on for XboxBLE pairing. Choose Xbox Wireless Controller and release the startup buttons after connecting."],
  serviceHelp: ["配置服务未广播；网页按设备名称选择并请求额外服务权限。同名设备需装有本工程配套固件。", "The configuration service is not advertised. This page selects by name and requests optional service access. The selected device must run this project's matched firmware."],
  mappingTitle: ["Xbox BLE 按键布局", "Xbox BLE button layout"],
  mappingHelp: ["左摇杆保持左摇杆，右板触摸模拟右摇杆，松开回中。左板默认按压后输出十字键；左背 → R3，右背 → L3。允许多个源按键映射到同一目标。", "The stick controls the left stick; right-pad touch controls the right stick and centers on release. The left pad requires a click for D-pad input by default. Left grip → R3, right grip → L3. Multiple sources may share a target."],
  mappingOffline: ["可以先编辑布局草稿。连接后自动读取设备配置作为保存基准，并保留你已编辑的草稿。", "Edit a layout draft before connecting. Connecting reads a validated device baseline for saving and preserves your edited draft."],
  faceLayout: ["ABXY 排列预设", "ABXY layout preset"],
  layoutXbox: ["Xbox 标准排列", "Standard Xbox layout"], layoutSwapped: ["交换 A/B 与 X/Y", "Swap A/B and X/Y"], layoutCustom: ["自定义排列", "Custom layout"],
  layoutPresetHelp: ["预设只修改四个面键。下方可分别调整所有实体按键的 Xbox 输出，保存后生效。", "Presets change only the four face buttons. Remap each physical button below; changes take effect after saving."],
  layoutPreview: ["布局预览 · 实体按键 → Xbox 输出", "Layout preview · physical button → Xbox output"],
  bindingHelp: ["每项标签为手柄实体按键，下拉框选择要输出的 Xbox 按键；选择“无”可取消该按键映射。", "Each label identifies a physical controller button. Choose its Xbox output below, or None to unbind it."],
  groupFace: ["面键 ABXY", "Face buttons · ABXY"], groupShoulders: ["肩键与扳机到底", "Shoulders & trigger clicks"],
  groupSystem: ["菜单与 Steam 键", "Menu & Steam buttons"], groupExtra: ["背键与按压", "Grips & clicks"],
  offlineSave: ["连接并成功读取设备配置后，才可保存到手柄。", "Connect and successfully read the device configuration to enable saving."],
  draftKept: ["已读取设备保存基准，保留了本地布局草稿；点击保存后生效。", "Device baseline loaded; your local layout draft was preserved. Click Save to apply it."],
  padSettings: ["摇杆与方向区域", "Sticks & directional zones"], stickDeadzone: ["左摇杆死区（0–16000）", "Left stick deadzone (0–16000)"],
  padDeadzone: ["右板摇杆死区（0–16000）", "Right pad deadzone (0–16000)"], dpadThreshold: ["左板十字键阈值（256–30000）", "Left pad D-pad threshold (256–30000)"],
  diagonalAngle: ["左板斜向区域角度", "Left pad diagonal angle"], angleHelp: ["0–75°，每次调整5°。越小越不易误触斜向；0°关闭斜向，45°八方向等宽。点击保存后生效。", "0–75° in 5° steps. Smaller angles reduce accidental diagonals. 0° disables diagonals; 45° gives equal eight-way sectors. Click Save to apply."],
  angleValue: ["{angle}°（每个直向 {cardinal}°）", "{angle}° (each cardinal zone {cardinal}°)"], angleOff: ["0°（关闭斜向）", "0° (diagonals off)"],
  invertX: ["反转右触控板 X", "Invert right pad X"], invertY: ["反转右触控板 Y", "Invert right pad Y"], requireClick: ["左板按压后才触发十字键", "Require left-pad click for D-pad input"],
  readMapping: ["重新读取 / 替换表单", "Read / replace draft"], defaults: ["填入默认配置", "Fill defaults"], save: ["保存到手柄", "Save to controller"],
  saveHelp: ["填入默认值只修改表单。保存等待真实持久化确认，并重新读取核对；发生超时或断连时请重新读取，勿把失败当作未写入。", "Defaults only change the form. Saving waits for a persistence ACK, then reads back to verify. After a timeout or disconnect, re-read to check; a failed operation does not prove nothing was written."],
  diagnosticTitle: ["链路与按键诊断", "Link & input diagnostics"], autoRefresh: ["每 1 秒安全刷新", "Safely refresh every second"],
  refreshDiag: ["读取诊断", "Read diagnostics"], exportDiag: ["下载诊断 JSON", "Download diagnostics JSON"],
  diagnosticHelp: ["保存时暂停刷新。六页按顺序采样，并非同一时刻的冻结快照；数据不含绑定密钥。报告表示固件当前输入，不证明系统或游戏已接收。", "Refresh pauses during saves. Six pages are sampled sequentially, not as an atomic snapshot. No bonding keys are included. Reports show firmware input, not proof of delivery to the OS or game."],
  reportTitle: ["当前 Xbox 报告 · 16 字节，无 Report ID", "Current Xbox report · 16 bytes, no Report ID"], noSample: ["尚无采样", "No sample yet"],
  hat: ["十字键帽", "D-pad hat"], neutral: ["中立", "Neutral"], allDiagnostics: ["全部诊断字段", "All diagnostic fields"], field: ["字段", "Field"], value: ["值", "Value"],
  activity: ["操作记录", "Activity"], clearLog: ["清空显示", "Clear log"], troubleshoot: ["使用与排查", "Usage & troubleshooting"],
  help1: ["未连接也可编辑布局。连接时会读取设备配置，并保留已编辑的草稿；“重新读取 / 替换表单”会用设备配置替换草稿。保存冲突后请重新读取再编辑。", "Edit layouts offline. Connecting reads the device configuration and keeps an edited draft. Read / replace draft replaces it with device settings. Re-read after a save conflict."],
  help2: ["“已持久化”只在收到匹配事务的成功 ACK 后显示；“回读一致”还需要完整配置再次校验。未知结果不会自动重发提交。", "“Persisted” requires a matching successful transaction ACK. “Verified” also requires a matching validated readback. Unknown outcomes never trigger an automatic commit retry."],
  help3: ["设备列表为空时，确认手柄处于 XboxBLE 配对/可发现状态、蓝牙已启用、浏览器有权限。服务不可用时检查成套固件版本。", "If the chooser is empty, check XboxBLE pairing/discoverability, Bluetooth and browser permissions. If the service is unavailable, check the matched firmware version."],
  help4: ["需要核对游戏输入时，可断开网页以释放本页 GATT 连接。诊断的连接、加密、订阅和 HVX 计数应分别查看。", "Disconnect the page to release its GATT connection when checking game input. Inspect connection, encryption, subscription and HVX counters separately."],
  webVersion: ["网页 1.1 / 协议 1", "Web 1.1 / protocol 1"], environment: ["Web Bluetooth 可用", "Web Bluetooth available"],
  disconnected: ["尚未连接 / 已断开", "Not connected / disconnected"], connected: ["Xbox Wireless Controller · GATT 已连接", "Xbox Wireless Controller · GATT connected"], working: ["正在操作…", "Working…"],
  idle: ["等待读取", "Awaiting read"], loaded: ["已读取", "Loaded"], dirty: ["尚未保存", "Unsaved changes"], defaultsFilled: ["默认值待保存", "Defaults ready to save"],
  preview: ["默认布局预览", "Default layout preview"],
  previewDetail: ["当前为默认布局预览，可直接修改；连接后可读取并保存手柄配置。", "Showing the default layout for editing. Connect to read and save controller settings."],
  staging: ["保存进行中 · 暂存", "Saving · staging"], saving: ["保存进行中 · 等待持久化 ACK", "Saving · awaiting persistence ACK"], persisted: ["已持久化 · 正在回读", "Persisted · reading back"],
  verified: ["已持久化 · 回读一致", "Persisted · readback verified"], conflict: ["保存冲突", "Save conflict"], failed: ["保存失败", "Save failed"], unknown: ["保存结果未知", "Save outcome unknown"],
  persistedUnverified: ["已收到持久化 ACK · 回读未确认", "Persistence ACK received · readback unverified"],
  loadedDetail: ["已加载校验通过的设备配置；修改后点击保存。", "Validated device configuration loaded. Click Save after editing."],
  dirtyDetail: ["表单已修改，尚未保存到手柄。", "The form has changed. Changes have not been saved to the controller."],
  defaultsFilledDetail: ["已填入默认映射；只有点击保存才会写入。", "Defaults filled. Only clicking Save writes the mapping."],
  idleDetail: ["请连接并读取配置。", "Connect and read the configuration."],
  savingDetail: ["请保持连接；提交只发送一次，网页正在轮询实际结果。", "Keep the connection open. Commit is sent once; the page is polling its actual outcome."],
  verifiedDetail: ["手柄返回持久化成功 ACK，完整配置回读及 CRC 校验一致。", "The controller acknowledged persistence; the full configuration and CRC match on readback."],
  verifyAgain: ["请重新读取配置核对；必要时重新连接。此结果不能证明未写入，也不会自动重发提交。", "Re-read the configuration to check; reconnect if necessary. This result does not prove nothing was written. Commit will not be retried automatically."],
  persistedDetail: ["已收到持久化成功 ACK，但当前配置回读未确认。请重新读取核对。", "A persistence success ACK was received, but the current configuration has not been verified. Re-read to check."],
  token: ["最近读取 token {token} · hostStatus {hostStatus}", "Last-read token {token} · hostStatus {hostStatus}"],
  updated: ["采样 {time}{stale}", "Sampled {time}{stale}"], stale: [" · 离线历史采样", " · offline historical sample"],
  metricRX: ["接收 / 解析成功", "RX / parsed OK"], metricBad: ["解析失败", "Parse failures"], metricHVX: ["HVX 成功 / 尝试", "HVX success / attempts"], metricBlock: ["发送条件", "Send readiness"],
  metricLast: ["最近长度 {length} · received {received}", "Last length {length} · received {received}"], metricResult: ["最近结果 {result}", "Last result {result}"],
  rawButtons: ["原始 buttons {buttons} · 启动抑制 {suppress} · header {header} · Xbox buttons {xbox}", "Raw buttons {buttons} · startup suppress {suppress} · header {header} · Xbox buttons {xbox}"],
  exported: ["已下载最近诊断采样 JSON（不含绑定密钥）。", "Downloaded the latest diagnostic sample JSON (no bonding keys)."], canceled: ["未选择设备，连接已取消。", "No device selected. Connection cancelled."],
  error: ["操作失败：{detail}", "Operation failed: {detail}"], errorDetail: ["错误：{detail}", "Error: {detail}"],
  unsupported: ["当前浏览器不支持 Web Bluetooth，请使用 Chrome / Edge。", "Web Bluetooth is unavailable. Use Chrome / Edge."],
  insecure: ["请通过 HTTPS 或 localhost 打开页面。", "Open this page over HTTPS or localhost."],
  frame: ["响应长度或帧格式无效。", "Invalid response length or frame format."], mapping: ["配置格式或数值无效，请检查整数范围、映射目标和角度。", "Invalid mapping format or values. Check integer ranges, targets and angle."],
  crc: ["分页配置 CRC 校验失败，未取得一致快照。", "Paged configuration CRC failed; no consistent snapshot was obtained."],
  timeout: ["本地事务超时，连接已释放。", "Local transaction timed out; the connection was released."],
  readFirst: ["请先重新读取配置，再编辑保存。", "Re-read the configuration before editing and saving."],
  characteristic: ["配置特征不支持所需的读写接口。", "Configuration characteristics lack the required read/write methods."],
  identity: ["诊断标识不匹配，需要 XSC2、修订号 2 或更新、协议 1。", "Diagnostic identity mismatch. Requires XSC2, revision 2 or later, protocol 1."], diagnostic: ["诊断页无效。", "Invalid diagnostic pages."],
  commitAck: ["提交 ACK 的 token 或保留字段无效，持久化结果不能确认。", "Commit ACK token or reserved fields are invalid; persistence cannot be confirmed."],
  readback: ["保存后的配置回读不一致，可能有其他客户端修改。", "Post-save readback differs; another client may have changed the configuration."],
  statusUnknown: ["设备返回未知状态。", "Device returned an unknown status."],
  status1: ["设备拒绝：参数无效。", "Device rejected invalid parameters."], status2: ["配置 token 冲突，请重新读取核对。", "Configuration token conflict. Re-read to check."],
  status3: ["设备正忙。", "Device is busy."], status5: ["设备持久化超时，结果未知。", "Device persistence timed out; outcome unknown."],
  status6: ["设备配置尚未加载。", "Device configuration is not loaded."], status7: ["设备存储错误。", "Device storage error."],
};
export const sources = {
  "zh-CN": ["A", "B", "X", "Y", "左肩 LB", "右肩 RB", "View", "Menu", "Steam", "左背键", "右背键", "左摇杆按压", "左触控板按压", "右触控板按压", "左扳机压到底", "右扳机压到底"],
  en: ["A", "B", "X", "Y", "Left shoulder LB", "Right shoulder RB", "View", "Menu", "Steam", "Left grip", "Right grip", "Stick click", "Left pad click", "Right pad click", "LT full pull", "RT full pull"],
};
export const targets = {
  "zh-CN": ["无", "A", "B", "X", "Y", "LB", "RB", "View", "Menu", "Guide", "左摇杆按压 L3", "右摇杆按压 R3", "十字键 ↑", "十字键 ↓", "十字键 ←", "十字键 →", "LT", "RT"],
  en: ["None", "A", "B", "X", "Y", "LB", "RB", "View", "Menu", "Guide", "Left stick click L3", "Right stick click R3", "D-pad ↑", "D-pad ↓", "D-pad ←", "D-pad →", "LT", "RT"],
};
export const blockReasons = {
  "zh-CN": ["就绪", "未连接", "挂起", "Boot 协议", "安全错误", "未加密", "CCCD 错误", "未订阅", "输入不完整", "无 tick（待核实）"],
  en: ["Ready", "Disconnected", "Suspended", "Boot protocol", "Security error", "Unencrypted", "CCCD error", "Unsubscribed", "Incomplete input", "No tick (unconfirmed)"],
};
let language = "zh-CN";
export const getLanguage = () => language;
export function setLanguage(value, storage) {
  language = value === "en" ? "en" : "zh-CN";
  try { storage?.setItem("sc2015-xbox-language", language); } catch { /* Private browsing. */ }
}
export function initialLanguage(browserLanguage, storage) {
  try { const saved = storage?.getItem("sc2015-xbox-language"); if (["en", "zh-CN"].includes(saved)) return saved; } catch { /* Storage disabled. */ }
  return browserLanguage?.toLowerCase().startsWith("zh") ? "zh-CN" : "en";
}
export function t(key, params = {}) {
  const value = messages[key]?.[language === "en" ? 1 : 0] ?? key;
  return value.replace(/\{(\w+)\}/g, (_, name) => String(params[name] ?? ""));
}
export function localize(root) {
  for (const node of root.querySelectorAll("[data-i18n]")) node.textContent = t(node.dataset.i18n);
}
