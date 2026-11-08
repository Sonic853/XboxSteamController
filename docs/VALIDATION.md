# 本地构建与软件验证

日期：2026-11-09。Windows x86-64；GCC 9.2.1，Cortex-M0；Unicorn 2.1.4。

## 当前产物

`python tools/build.py --base-dir .tools/base --toolchain-bin .tools/gcc/bin` 成功。

|产物|镜像大小|新增代码/常量|新增永久RAM|声明补丁|
|---|---:|---:|---:|---:|
|`build/host.bin`|84,736 B|2,816 B|16 B|14|
|`build/radio.bin`|136,024 B|4,952 B|260 B（预留472 B）|13|

```text
4e7fd8228b69d1370260ba5c64f2ea24d0040e12c3d8e4351d0368d15b26ba50  host.bin
15546bb657a89b723f29af280bfeaf2cac7c38743e5d477d2fcf732d639990cf  radio.bin
```

已再次核对manifest全部源文件/config指纹及五项产物哈希。六个附件包括两份BIN、两份ELF、`manifest.json` 与 `SHA256SUMS`；这是更新后的成套镜像。

## 测试

设置 `PYTHONPATH=.tools/python`、`XSC_REQUIRE_ARM_TESTS=1`、`XSC_REQUIRE_EMULATION=1`，执行 `python -m unittest discover -s tests -q`：**128项通过，0 skip**。

|测试组|数量|主要覆盖|
|---|---:|---|
|构建|14|真实双MCU编译、逐hook字节、Flash/RAM/ELF边界、原S110完整保留、校验清单|
|依赖|13|固定哈希、损坏缓存/下载、归档处理|
|LPC主控|33|RB物理索引及新旧组合、完整原输入/UART链、raw设置reset复现与修复、独立绑定、映射持久化/CAS/掉电/忙及提交后ACK|
|镜像编辑|3|原BL字节、跳转边界、完整性|
|输入编码|29|512种delta组合、16×18映射、左右背键、左板按压、角度、死区、CRC和启动抑制|
|nRF基础ABI|20|原SDK HIDS/DIS/广播、加密/CCCD门控、背压重试、重连首快照|
|nRF配置|7|20B GATT权限、UART独立callback、持久化ACK、交错暂存guard、冲突/错误/超时和诊断|
|nRF原指令链|9|原timer/SWI/RTC、UART解帧、事件分发与发送、原SoftDevice WRITE序列化器及UUID分配器|

`node --test tests/test_web.mjs`：**23项通过**。覆盖真实20字节网页协议、CRC分页一致性、保存与回读、双CRC提交guard、错误/未知结果、超时/断连、事务串行化和轮询清理。

浏览器在 `http://localhost:8530` 实际加载页面，检查中英文切换、布局、默认映射、保存/回读、填默认不发写请求及断连禁用状态。使用明确标注的**模拟BLE对象**，没有调用实体手柄；控制台无错误、无水平溢出。结束后已移除模拟对象并恢复未连接页面。

### 网页 1.1 布局与角度配置

- `node --check web/app.mjs` 通过；`node --test tests/test_web.mjs` 再次 **23项通过**，保存/回读用例包含75°角度。
- 浏览器确认离线布局始终可编辑，16项默认映射正确；ABXY交换预设仅修改四个面键，逐项编辑能切换到自定义排列并同步预览。
- 左板角度范围0–75°、步长5°、默认30°，与 `Receiver/web` 一致；中文/英文下0°、30°、45°、75°显示及滑块无障碍标签/数值正确。
- 使用临时模拟BLE对象检查：连接保留已编辑草稿，保存75°与自定义映射只发送一次commit并一致回读；填默认不发请求，手动读取替换草稿，断连后可继续编辑但禁用保存，无草稿时重连加载设备配置。
- 桌面及约390px宽的同源页面检查中英文布局，无水平溢出；浏览器控制台无错误。验证后刷新页面，移除模拟对象，恢复默认布局预览。此项为网页与模拟协议验证，实物BLE保存仍待硬件验证。

## 无输入问题与实物边界

- 已条件复现并修复：原主控settings reset将setting8清零，关闭raw输入。新增构建前恢复hook，经完整原输入链回归验证。尚不能证明用户设备恰好触发此reset或其为唯一原因。
- 新配置服务的WRITE事件op偏移错误也已修正为event+8，并由原SoftDevice序列化指令验证；它与上一版没有配置服务的无输入故障不同。
- 用户已经报告上一版实际可配对、可重连但无输入。这不等于当前版本已完成实物验收。
- IAP、外设、部分SoftDevice调用仍使用显式模型。真实Flash时序、attribute table分配、浏览器BLE往返、空中交付、Windows原生XInput以及功耗仍待硬件验证。
- 本轮没有刷写设备或运行远端Actions。工作流已加入网页测试与独立Pages部署；工作目录尚非Git仓库。

下一次实测：刷入当前两份BIN，删除系统旧Xbox蓝牙记录并用 **RB＋Y＋Steam** 配对。若仍无输入，用本体网页操作按键/触控板后导出诊断JSON，按 [NO_INPUT_RESEARCH.md](NO_INPUT_RESEARCH.md) 定位最早未满足的门控。
