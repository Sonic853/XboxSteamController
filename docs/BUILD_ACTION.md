# GitHub Actions 构建

仓库根目录为 `XboxSteamController/`，其中直接包含 `.github/`、`tools/`、
`tests/`、`config/` 和固件补丁源码。构建依赖只有 Python 3.12+ 标准库与
GNU Arm Embedded 9-2019-q4-major（GCC 9.2.1）。ARM 执行测试额外使用
`unicorn==2.1.4`，安装到 `.tools/python/`；无需 Arduino 或预装 ARM SDK。网页协议测试使用 Node.js 22，自带测试运行器，无 npm 依赖。

## 手动构建

1. 在 GitHub 仓库的 **Actions → Build SC2015 firmware → Run workflow** 中选择分支。
2. 选择 `ubuntu-22.04` 或 `windows-2022`；默认 Ubuntu x86-64。
3. `firmware_zip_url` 留空使用官方地址。需要镜像时填写同一个 ZIP 的 HTTPS URL；
   ZIP 和三个 BIN 的固定 SHA-256 仍然必须全部匹配，输入无法覆盖哈希。
4. 成功后下载 `SC2015-firmware-<runner>-<commit>` 附件。

工作流仅由 `workflow_dispatch` 触发，依次 checkout、setup-python/setup-node、网页协议测试、下载并校验依赖、
安装固定版本 Unicorn、构建双 MCU，然后运行 `python -m unittest discover -s tests -v`。
测试步骤设置 `PYTHONPATH=.tools/python`、`XSC_REQUIRE_ARM_TESTS=1` 和
`XSC_REQUIRE_EMULATION=1`，使 ARM 构建/模拟所需依赖缺失时失败。全体测试成功后上传六个文件：

```text
host.bin
radio.bin
host-payload.elf
radio-payload.elf
manifest.json
SHA256SUMS
```

`host.bin` 是 LPC 镜像，`radio.bin` 是 nRF 镜像；ELF 用于检查补丁载荷。
`SHA256SUMS` 包含另五个文件的 SHA-256，不包含自身。
`manifest.json` 记录原镜像、源文件/config、编译器、payload 与 RAM 范围、每处 hook 的
before/after/symbol、输出哈希和推断项，并明确 `experimental: true`、`hardware_tested: false`。
Actions 仅生成文件，不运行 Valve 更新器或执行设备操作。

## 本地构建

在仓库根目录运行；Windows PowerShell 与 Linux 使用相同参数：

```sh
python tools/dependencies.py
python -m pip install --disable-pip-version-check --only-binary=:all: --no-deps --target .tools/python unicorn==2.1.4
python tools/build.py --base-dir .tools/base --toolchain-bin .tools/gcc/bin
python -m unittest discover -s tests -v
node --test tests/test_web.mjs
```

依赖脚本自动识别 Linux/Windows x86-64，生成：

```text
.tools/downloads/  # 原始下载归档，每次使用前校验 SHA-256
.tools/base/       # 仅三个已校验的 BLE BIN
.tools/gcc/bin/    # arm-none-eabi-gcc、objcopy、objdump 等
.tools/python/    # Unicorn 模拟器；测试自动加入导入路径
build/            # build.py 的输出
```

每次准备工具链均从验证后的归档重新解包，因此下载归档可离线复用，已解包目录
被修改后也不会作为可信缓存直接复用。下载不完整或校验失败会终止；若下载缓存损坏，
删除报错的 `.tools/downloads/` 文件后重试。`.tools/`、`build/` 不进入 Git。

本工作区已有原件和 GCC，可完全离线使用：

```powershell
python tools/dependencies.py --skip-toolchain --base-dir "../Steam Controller FW Update Tool/updatescripts/fw_images/ble"
python tools/build.py --base-dir .tools/base --toolchain-bin "../Receiver/.tools/arduino-data/packages/adafruit/tools/arm-none-eabi-gcc/9-2019q4/bin"
```

也可以把已有 BLE 目录直接传给 `build.py --base-dir`。这些相邻目录只是本地复用选项，
GitHub Actions 从仓库根目录独立下载依赖。

`--output PATH` 可以指定另一构建目录，例如 `--output .tools/local-build`。构建会在
独立临时目录编译并验证两颗 MCU，成功后发布六个文件；输入错误或编译失败不会替换
原产物。`tests/test_build.py` 的真实集成构建也使用独立临时目录，不覆盖 `build/`。

## 构建合同与 config schema

固定源文件：

- host：`src/host.c`、`src/bond_store.c`、`src/mapping.c`、`src/runtime.c`，链接脚本 `src/host.ld`。
- radio：`src/radio.c`、`src/radio_config.c`、`src/input.c`、`src/mapping.c`、`src/runtime.c`，链接脚本 `src/radio.ld`。
- 两颗 MCU 都使用 Cortex-M0 Thumb；编译参数如下，显式链接 GCC 自带 `libgcc`
  算术辅助代码，不链接 libc 或启动代码，不启用 `--gc-sections`。

```text
-mcpu=cortex-m0 -mthumb -Os -ffreestanding -fno-builtin
-fno-unwind-tables -fno-asynchronous-unwind-tables
-ffunction-sections -fdata-sections -Wall -Wextra -Werror -nostdlib
-T src/<name>.ld -Wl,--build-id=none -lgcc
```

链接脚本须将代码和只读常量合并到 `.text`，地址匹配 config 的 `payload.origin`。
构建器以 `nm --extern-only --defined-only` 解析 hook 的导出符号，`objdump -h` 检查
段地址/长度/属性，`objcopy --only-section=.text` 提取载荷。非空 RAM 段必须 NOLOAD、
位于声明的 RAM 区间；BSS 初始化由补丁运行时负责。存在额外初始化段时构建失败。

`config/host-patches.json` 与 `config/radio-patches.json` 共用 schema_version 1。
字段示例（hook 地址/名称以下仅说明语法，实际使用仓库的已核对 config）：

```json
{
  "schema_version": 1,
  "name": "host",
  "base": {
    "filename": "vcf_wired_controller_d0g_5b0f21bd.bin",
    "sha256": "2c42adb211c9f209dae5ac223ed451229d0aa08b139acfbd56f36b0d8add6fc4"
  },
  "payload": {"origin": "0x14000", "limit": "0x1e000"},
  "ram": {"origin": "0x10001fd0", "size": 16},
  "protected_ranges": [
    {"start": "0x2010", "end": "0x2014", "purpose": "Original zero word"}
  ],
  "hooks": [
    {"kind": "bl", "address": "0x616c", "expected_target": "0x64b4",
     "symbol": "host_selector", "purpose": "Wrap original selector"},
    {"kind": "literal", "address": "0xd778", "expected": "0xd8bd",
     "symbol": "host_config_write", "thumb": true, "purpose": "Replace callback"},
    {"kind": "exact", "address": "0x2000", "before": "e01f0010",
     "after": "d01f0010", "purpose": "Reserve scratch RAM"}
  ]
}
```

- 地址接受整数或 `0x…` 字符串，范围左闭右开；Flash 地址等于 BIN 偏移。
- `bl` 验证原 BL 指令和目标后，编码新符号的 Thumb BL；代码地址不带 bit0。
- `literal` 比较原 LE32，再写入符号地址；`thumb: true` 设置函数指针 bit0。
- `exact` 比较完整原字节后等长替换。禁止负偏移、越界、不等长或重叠修改。
- `protected_ranges` 只能增加保护，不能取消脚本内固定的 bootloader/向量/存储区保护。

radio 必须包含唯一动态长度 hook：

```json
{"kind":"image_end_minus","address":"0x18010","expected":"0x7014",
 "base":"0x18000","purpose":"Update inferred application length"}
```

原值 `0x7014` 恰好等于原 radio 长度 `0x1f014` 减应用起点 `0x18000`。
构建器再次验证此等式，再写入最终 BIN 长度减 `0x18000`；manifest 标记其含义为
`inferred`，并不声称执行验证。host `0x2010` 原值为零，不采用该长度推断。

### 固定边界

| 项目 | 代码范围 | RAM 保留范围 | 不写入的存储区 |
| --- | --- | --- | --- |
| LPC host | `[0x14000,0x1e000)` | 当前 config 为 `[0x10001fd0,0x10001fe0)` | `[0x1e000,0x20000)` 独立绑定日志 |
| nRF radio | `[0x20000,0x3f800)` | `[0x20003e28,0x20004000)`，472 字节 | `[0x3f800,0x40000)` 最后两个 1 KiB 页 |

三个基线均严格按 SHA-256 校验，并验证整个 S110 BIN 等于 radio 原前缀。
host bootloader `[0,0x2000)` 和 radio `[0,0x180c0)` 保持原样，后者仅放行上述
`0x18010` 动态长度字段。所有旧镜像字节变更必须落在声明的 hook 内。
原文件末尾至载荷起点填 `FF`；最终 BIN 仅补 `FF` 至四字节对齐，不扩展到整页或绑定存储区。
radio 以 nRF51822 256 KiB Flash / 16 KiB RAM 为构建假设，尚未读取实际设备 FICR；
manifest 明确记录该状态。模拟器测试也不等于设备无线行为验证。

## 原厂 ZIP 的来源与核对

2026-11-08 实际从以下官方来源追溯链接并下载校验：

- [Valve 员工 austinp_valve 的公告/置顶答案](https://steamcommunity.com/app/353370/discussions/1/6516193260178656983/#c3801649759589303015)
  明确提供版本化的 `Steam_Controller_FW_Update_Tool_5b0f21bd.zip`；
  早期无版本号 ZIP 不作为本构建的输入。
- [Steamworks 官方 Steam Controller (2015) 文档](https://partner.steamgames.com/doc/features/steam_controller/device/steam_controller)
  的 **BLE FW Update** 链接目前指向：
  <https://shared.fastly.steamstatic.com/community_assets/images/steamworks_docs/english/Steam_Controller_FW_Update_Tool_5b0f21bd.zip>

ZIP 长度 **369050** 字节，SHA-256：

```text
36bf77d770a81b99f43c4b78f825f44c02e732630cb00229828bdfb29332c1c1
```

ZIP 内 `Steam Controller FW Update Tool/updatescripts/fw_images/ble/` 中的三个文件
与提供的本地原件逐字节一致：

| 文件 | 字节数 | SHA-256 |
| --- | ---: | --- |
| `vcf_wired_controller_d0g_5b0f21bd.bin` | 77840 | `2c42adb211c9f209dae5ac223ed451229d0aa08b139acfbd56f36b0d8add6fc4` |
| `vcf_wired_controller_d0g_5a0e3f348_radio.bin` | 126996 | `db45055fc5251a5800961337454a76fb7a0ccf30a09414f757df5b20d73b96bb` |
| `s110_nrf51_8.0.0_softdevice.bin` | 92440 | `707f0a518f5228039fd49d1a3a7b5fd0b477fd6b07928d07974555f394d3e23c` |

脚本只读取并输出以上三个成员，先全部验证再写入 `.tools/base/`。

## 编译器锁定

下载 URL 来自 [Adafruit 工具链索引](https://adafruit.github.io/arduino-board-index/package_adafruit_index.json)
的 `arm-none-eabi-gcc` / `9-2019q4` 项。仅使用其中的独立 GCC 压缩包。
实际下载后计算以下 SHA-256，并核对其 MD5 与已安装环境的索引一致：

| 平台 | 下载链接 | 字节数 | SHA-256 |
| --- | --- | ---: | --- |
| Linux x86-64 | [gcc-arm-none-eabi-9-2019-q4-major-x86_64-linux.tar.bz2](https://github.com/adafruit/arduino-board-index/releases/download/build-tools/gcc-arm-none-eabi-9-2019-q4-major-x86_64-linux.tar.bz2) | 116802378 | `bcd840f839d5bf49279638e9f67890b2ef3a7c9c7a9b25271e83ec4ff41d177a` |
| Windows x86-64（运行 win32 编译器） | [gcc-arm-none-eabi-9-2019-q4-major-win32.zip](https://github.com/adafruit/arduino-board-index/releases/download/build-tools/gcc-arm-none-eabi-9-2019-q4-major-win32.zip) | 153520070 | `54e0879b78937cf74a548f9fd242cdfd703bec9fe135d2bb4f56acf5c05d6d61` |

索引 MD5：Linux `fe0029de4f4ec43cf7008944e34ff8cc`，Windows
`9d60cbb0e358ab6a9d3c9e5dc3624dd2`。构建脚本实际强制使用的是上表 SHA-256。
Windows 包内 `bin/arm-none-eabi-gcc.exe` 与本地已安装的 GCC 逐字节一致，SHA-256 为
`06cda9fce64eb287a2ad3f5384606c7de556cd0ff795b92915b17e4ac489545f`。

以上是下载内容及本地一致性验证，不代表已经运行远端 Actions 或验证设备行为。
