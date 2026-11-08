# XboxSteamController

为 **2015 版 Steam Controller 本体**增加 Xbox Series BLE 输入模式的实验性固件补丁。基于固定版本的原厂 LPC 主控、nRF51822 radio 和 S110 固件，生成成套的 `host.bin` / `radio.bin`。

Xbox身份采用 `045e:0b13`、283字节 Series Report Map。上一版用户已实测配对、重连成功，但没有输入；本次加入输入修复、可配置映射和分层诊断，当前版本仍需重新实测 Windows 输入。

当前双MCU构建成功，**128项固件测试＋23项网页协议测试通过**。产物和软件验证边界见 [VALIDATION.md](docs/VALIDATION.md)。

## 开机操作

|组合|行为|
|---|---|
|先按住右肩键 RB，再按 Steam 开机|本次启动进入 Xbox BLE|
|右肩键 RB＋Y＋Steam 开机|Xbox BLE 配对|
|B＋Steam|原 Steam BLE 恢复连接|
|Y＋Steam|原 Steam BLE 配对|
|A / X＋Steam|原私有2.4GHz恢复 / 配对|

Xbox profile 不写入原 transport 配置；下次未按 RB 启动仍按原固件选择规则。Xbox 使用独立绑定存储和 BLE 地址。开机组合键在释放前不作为游戏输入发送。

输入映射见 [INPUT_MAPPING.md](docs/INPUT_MAPPING.md)。默认实体摇杆→左摇杆、右触控板→右摇杆、左触控板**按压**→方向键、**左背→R3、右背→L3**。Xbox震动输出不驱动物理触觉，未提供电池服务。

## 本体配置网页

1. 刷入当前成套 `host.bin` / `radio.bin`，用 **RB＋Y＋Steam** 配对。升级后 GATT 服务有变化；若系统仍缓存旧服务，请删除旧 Xbox 蓝牙设备再配对。
2. 在项目目录运行 `python -m http.server 8530 --bind 127.0.0.1 --directory web`。
3. 用 Chrome / Edge 打开 <http://localhost:8530>，点击连接并选择手柄。
4. 读取映射，编辑按键、死区、左板按压和方向角度，点击保存。只有收到主控 Flash 提交回复并读回一致后才显示保存完成。
5. 若仍无输入，打开实时诊断，操作几个按键和两块触控板，再导出诊断 JSON。

### GitHub Pages 在线配置页

1. 将 `XboxSteamController` 文件夹的内容作为仓库根目录上传，包含 `.github/workflows/pages.yml`。
2. 在 GitHub **Settings → Pages → Build and deployment → Source** 选择 **GitHub Actions**。
3. 打开 **Actions → Deploy controller configuration page → Run workflow** 完成首次部署。此后，`main` / `master` 分支的网页、网页测试或 Pages 工作流更新会自动触发部署。
4. 工作流先检查语法并运行网页协议测试，通过后发布 `web/`。点击部署任务的 `github-pages` 环境链接即可打开 HTTPS 配置页，无需本地服务器。

详细说明见 [WEB_CONFIG.md](docs/WEB_CONFIG.md#github-pages-部署)。

## GitHub Actions 生成固件

1. 将 **`XboxSteamController` 文件夹的内容作为独立 GitHub 仓库根目录**上传，包含 `.github/workflows/firmware.yml`。
2. 打开 **Actions → Build SC2015 firmware → Run workflow**。
3. 选择 `ubuntu-22.04`（默认）或 `windows-2022`。官方包镜像URL留空即可。
4. 构建、ARM模拟测试通过后，下载 `SC2015-firmware-…` 附件。

附件内容：

|文件|用途|
|---|---|
|`host.bin`|LPC主控补丁镜像|
|`radio.bin`|nRF radio完整镜像，含未改动的原S110前缀|
|`host-payload.elf` / `radio-payload.elf`|补丁符号与分析产物|
|`manifest.json`|基线/产物哈希、逐点补丁、Flash/RAM范围、编译信息|
|`SHA256SUMS`|上述五个文件的SHA-256|

工作流仅生成附件，不刷设备或发布 Release。两个 BIN 是同一补丁协议的一套产物；不能把其中一个与未修改的另一 MCU 固件混用后期待 Xbox 模式工作。

## 本地构建（Windows PowerShell）

需要 Python 3.12+。下载的固件和 GCC 均按固定 SHA-256 校验。

```powershell
python tools/dependencies.py
python -m pip install --only-binary=:all: --no-deps --target .tools/python unicorn==2.1.4
python tools/build.py --base-dir .tools/base --toolchain-bin .tools/gcc/bin
$env:PYTHONPATH = ".tools/python"
$env:XSC_REQUIRE_ARM_TESTS = "1"
$env:XSC_REQUIRE_EMULATION = "1"
python -m unittest discover -s tests -v
node --test tests/test_web.mjs
```

产物位于 `build/`。工具不修改 `.tools/base/` 中的原文件；错误版本、错误调用点字节、存储越界或意外ELF段都会使构建失败。

## 实现资料

- [构建依赖与 Actions](docs/BUILD_ACTION.md)
- [Xbox BLE 协议、来源与许可](docs/XBOX_BLE_REFERENCE.md)
- [LPC启动、配置及输入交界研究](docs/LPC_PATCH_RESEARCH.md)
- [主控实现与独立绑定存储](docs/HOST_IMPLEMENTATION.md)
- [无线端实现与内存合同](docs/RADIO_IMPLEMENTATION.md)
- [无线ABI审计](docs/RADIO_ABI_REVIEW.md)
- [无输入链路排查](docs/NO_INPUT_RESEARCH.md)
- [配置通道协议](docs/CONFIG_PROTOCOL.md)

硬件目标为原2015手柄的128KB LPC主控、256KB Flash/16KB RAM nRF51822。扩展镜像的原厂更新器行为、真实无线时序和平台兼容性还需要设备验证；软件测试记录不代表实机通过。
