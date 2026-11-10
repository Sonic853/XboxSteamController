# 修订5：Steam / Xbox BLE 身份隔离

日期：2026-11-10。用户报告两个方向均可复现：一种模式配对后，另一种模式可发现但配对失败。Steam测试使用关机后的 **Y＋Steam，不按RB**。

## 已确定的隔离缺口

原radio SHA-256：`db45055fc5251a5800961337454a76fb7a0ccf30a09414f757df5b20d73b96bb`。复用工作区 `analysis/radio_analysis/softdevice_decompiled.c`、`ble_decompiled.c`，并以固定原镜像Thumb汇编和ARM执行核对：

|原地址|观察|
|---|---|
|`0x1a9e` / `0xb158`|从FICR ER/IR加载默认根；根全FF时存在随机生成分支|
|`0xabfc` / `0x7cd4`|从IR派生默认IRK到 `0x20000b40`，用 `0x20000b50` 标记已生成|
|`0x1c284`|原DM的SEC_PARAMS_REQUEST（事件`0x13`）回复参数为`0d 07 10 03 02`；peripheral请求分发EncKey和IdKey，local `p_id_key=NULL`，peer identity使用独立接收缓冲|
|`0xbb86`|local IdKey指针为NULL时，使用当前全局IRK及 `0x20000a87` 身份地址；NULL不表示关闭身份分发|
|`0xa1f6`|设置random-static地址会更新身份地址，但不会替换IRK|

因此修订4的独立Flash绑定槽和独立静态地址，仍不足以隔离分发给主机的IRK。新增原指令测试已复现：即使地址不同，NULL own-id路径仍返回同一个默认IRK。

**尚未观察本次失败的SMP/Windows配对日志，不能把“Windows因相同IRK合并两种模式”写成已确认的实机根因。** 修订5修正已证实的身份隔离缺口，实际双向配对结果待用户验证。

## 实现

Xbox的 `radio_init` 在原BLE初始化完成后、原延迟广播开始前：

1. `sd_ble_opt_get(BLE_GAP_OPT_PRIVACY)` 读取当前默认IRK和地址刷新间隔。
2. 调用 `sd_ecb_block_encrypt`，计算 `AES-128(default_IRK, "XSC2015 Xbox IRK")`。标签恰为16个ASCII字节；密钥来自当前手柄，标签不是共享固定密钥。
3. 用 `sd_ble_opt_set` 设置派生的Xbox IRK，并保留原刷新间隔。该调用同步复制密钥，临时48字节ECB缓冲随后清零。

每次Xbox冷启动执行一次，普通连接和重新配对都采用相同派生规则。Steam冷启动沿用原默认身份。稳定性依赖原芯片默认根；尚未读取实机FICR，不能为原根全FF的异常设备证明跨启动稳定性。

调用失败进入原错误处理，避免带着另一份身份继续初始化。新增逻辑不分配永久RAM，绑定存储格式及订阅保存/恢复链沿用现有实现。诊断页0固件修订号为5，协议仍为1。

## S110 8.0.0 ABI交叉验证

匹配来源：`lab11/nrf5x-base` commit `cfca4ebfb77b05c4b22a929970d908aff69ceeb7` 的 [SDK9 S110目录](https://github.com/lab11/nrf5x-base/tree/cfca4ebfb77b05c4b22a929970d908aff69ceeb7/sdk/nrf51_sdk_9.0.0/components/softdevice/s110)，其中 `doc/s110_nrf51_8.0.0_readme.txt` 标明版本8.0.0。

- `ble.h`：opt_set=`0x68`、opt_get=`0x69`。
- `ble_gap.h`：privacy选项=`0x23`；结构为32位IRK指针、u16 interval、2字节padding，共8字节。SET的NULL恢复默认IRK；GET的NULL仅表示不读出IRK。
- `nrf_soc.h`：ECB SVC=`0x4d`，R0指向48字节 `key[16] | cleartext[16] | ciphertext[16]`。补丁显式四字节对齐。不能套用较旧S110或新nRF52枚举。
- 原镜像BLE dispatcher `0x2190` 实际路由到set=`0x1f3c`、get=`0x1fee`；原SoC dispatcher `0x14e3c` 的`0x4d`路由到ECB驱动 `0x150c2`。
- 原privacy setter `0xaa56→0xb960` 同步复制16字节；getter `0xa0d2→0xb97a` 读出当前IRK。原地址模式为NONE/random-static，不会因设置privacy选项改为AUTO。

头文件对local NULL identity的语义见 [sec_params_reply](https://github.com/lab11/nrf5x-base/blob/cfca4ebfb77b05c4b22a929970d908aff69ceeb7/sdk/nrf51_sdk_9.0.0/components/softdevice/s110/headers/ble_gap.h#L1154-L1177)。头文件没有承诺默认根的生成方式；上面的FICR结论来自原镜像。

## 软件验证及产物

完整 **143项Python/ARM测试通过，0 skip**；网页 **28项通过**。新增 `tests/test_radio_identity.py` 的6项验证：

- 原DM实际请求默认local identity；原S110在地址不同情况下仍分发相同默认IRK。
- 通过原BLE/SoC dispatch table确认三个SVC编号、选项布局、同步复制及临时缓冲生命周期。
- 两组人工设备密钥、两种启动顺序、重复冷启动的分发结果；Steam原身份与Xbox派生身份分别稳定。
- 普通/重新配对初始化顺序，身份设置在第一次广播前完成。
- 三个新增API失败时，初始化停止且不发起广播。

这些测试执行原option handler、ECB寄存器驱动和own-id选择代码。AES硬件使用独立已知答案边界（例如key=`00..0f`时派生值=`68bf4573019416e8f1197465299aae0b`）；RF、完整SMP交换和Windows设备关联不在模拟覆盖范围内。原修订4的LPC日志、订阅冷启动恢复及输入回归也全部通过。

构建：`python tools/build.py --output build/identity-r5`。

|产物|大小|payload|永久RAM|SHA-256|
|---|---:|---:|---:|---|
|host.bin|84736|2816|16|`4e7fd8228b69d1370260ba5c64f2ea24d0040e12c3d8e4351d0368d15b26ba50`|
|radio.bin|137028|5956|308/472|`5f6f68b9a990abb6e1491f2a6f237993dbbdc18a82c5982803de2453a0b028d2`|

## 用户实机验证

本地更新器入口：`Steam Controller FW Update Tool/xbox-identity.bat`；配套文件在 `updatescripts/fw_images/xbox-identity/`。由用户执行刷写。

1. 刷入修订5后关机，在Windows蓝牙设置中移除这个手柄原有的 **Xbox Wireless Controller和SteamController** 配对项，等待完成。这是从共用IRK的旧身份迁移的一次性清理。
2. **RB＋Y＋Steam** 配对Xbox，测试输入并保持连接至少5秒。
3. 关机，**Y＋Steam（不按RB）** 配对Steam，保留刚才的Xbox配对项。
4. 关机后分别用 **RB＋Steam** 和 **B＋Steam** 重连两种模式，确认都能输入。
5. 再验证逆序：移除本手柄的两项配对，先Steam配对，再Xbox配对；随后分别重连。

验收需记录哪一步成功/失败；若仍失败，保留当时的两项设备名称及地址，继续采集配对错误。软件测试通过不等于实机双向配对通过。
