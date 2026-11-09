# nRF51822 补丁合同

目标仅为 SHA-256 `db45055fc5251a5800961337454a76fb7a0ccf30a09414f757df5b20d73b96bb` 的原厂 radio。地址为镜像中的绝对地址；函数指针设置 Thumb bit。主控、radio 必须成套使用。

## 启动与空间

- 原启动 scatter 后的 main literal `0x180cc` 改至 `radio_boot`；先清新增 BSS，再调用原 main `0x1d2f0`。
- 原 SP 与 runtime SP literal 均为 `0x20003e28`，原最后 zero 段为 `[0x200021e4,0x20003e28)`。补丁 BSS 从 `0x20003e28` 开始，上限 `0x20004000`，由 linker ASSERT 限制；不移动原 SP。
- payload 放 `0x20000`，严格止于 `0x3f800` 前。原 `0x19d0c/0x19d30` 以 FICR page size/count 计算最后两页的私有无线绑定存储。
- **硬件合同是 256KB Flash、至少16KB RAM 的 nRF51822。** 尚未从用户手柄读 FICR。128KB 变体不在这个镜像的支持范围。
- 原 app vector 保留 word `0x18010=0x7014` 恰等于原文件长度减 `0x18000`。构建按此长度字段解释更新；这是静态格式推断，尚未验证原更新器对扩展镜像的实际行为。
- 以上布局来自已保存 Ghidra scatter/汇编与原字节复核；没有原厂链接 MAP，也不是实物栈水位证明。

## Profile 交界

LPC 的两条配置回复路径发送 137 字节：`5d | offset=0 | marker=0x58420180 | 独立配置128字节`。radio `0x5d` handler wrapper 在原 `0x189c8` 回调前读取 marker。原 handler 忽略 header 中的 length，使用实际 UART 包长度减9，所以绑定数据仍是原格式。

profile 在 `radio_init` 进入 GATT 前冻结。后续回复不能热切换 GATT。普通启动继续原 Steam profile；新模式的独立配置含独立 BLE bond 和地址。原 `0x18fa0` 从配置偏移 `0x6c` 读7字节 random-static 地址；空配置会重新生成，随后原连接路径写回。因此无需共享 Steam 地址或仅修改设备名。

## HIDS / 身份 / 广播

- Name: `Xbox Wireless Controller`；Appearance `0x03c4`。
- DIS PnP 内部结构有自然对齐 padding，内存8字节：`02 00 5e 04 13 0b 09 05`。序列化后的属性7字节与参考报告一致。
- 283字节 Series 1914 Report Map；Input ID1 长16；Output ID3 长8；Feature数量0。
- HIDS init 参数结构72字节，输入、输出项各8字节；复用原SDK创建GATT及长属性读取。
- 输入/输出读写权限为原固件采用的 Mode1 Level2（加密、无MITM）。不调用原 Steam HIDS feature callback。
- 8字节 report 配置的权限顺序是 **CCCD-write、value-read、value-write**。Output 无CCCD，配置为 `08 00 03 02 00 21 21 00`，不能误将 value-write 清零。
- 完整24字节名称放 scan response；广播放 Appearance 与 HID UUID，避免将全名和其他AD项放在同一31字节广播内。
- 原私有 Steam service 仍由原初始化建立，但新 profile 不广告它。Xbox 输入不走其通知通道。
- 当前 Output 可写但不驱动物理触觉；未实现 Battery Service。不能据此宣称与原装 Xbox 的全部功能等价。

## 输入生命周期

`0x18a0c` 输入调用点在原 Steam18字节分片之前，收到完整的SC delta payload。转换器保留缺失字段，只接受严格长度/flags合法的包。

首次识别Xbox marker时抑制右肩RB、Steam及Y开机键；未释放完的抑制状态跨首次连接初始化保留，全部释放后永久解除，普通重连不会再次吞键。连接重置后，按钮、扳机、摇杆、左右pad字段全部收到之前不发送半帧输入；主控负责提供全量快照。

主循环 `0x1d404` wrapper 先执行原服务，再处理最新输入；原 app_timer 每328 RTC ticks 唤醒一次（约10ms）。定时器 callback 仅增加计数，不在 IRQ 中发送 HID。

发送前检查：

1. 已连接，未 suspend，未切入 boot protocol；
2. `sd_ble_gap_conn_sec_get` 返回 Mode1 Level≥2、有效密钥长度；
3. 读取 Input0 的实际 CCCD，确认 notify 已开。这样兼容绑定恢复时没有新的 CCCD write event 的情况。

只在报告变化或连接/订阅重新建立后发送，GATT value 不含 Report ID。原 `0x1ba90(ctx,index,len,data)` 返回成功才提交已发送快照；TX 缓冲满等错误保留最新状态，下一个 tick 重试。断开时清输入缓存和发送状态。不把“delta 长时间没变化”误判成掉线。

原 UART `0x50` feature response 和 `0x55` 状态在 Xbox 下被消费，防止它们继续以原 vendor report index2发送，导致只有一个Input时越界。

## 修订4：绑定订阅的及时保存

实机配对采集观察到首次输入订阅成功并收到293条报告，关机重连后CCCD=0而Windows保留完成标记。原device manager在auth/disconnect保存系统属性，未处理CCCD WRITE；auth早于Windows订阅，关机可能使radio来不及完成断开保存。详见 [LIVE_DIAGNOSTICS.md](LIVE_DIAGNOSTICS.md)。

`radio_event` 收到输入CCCD的完整WRITE_REQ/WRITE_CMD后标记待保存；主循环在同一已加密、已绑定连接上用SVC `0xaa` 读取真实系统属性。沿用原 `0x1cd04` 格式：48字节上下文，前8字节为flags=3及长度，最多38字节opaque数据，位于独立Xbox绑定配置偏移 `0x38`。通过原 `0x1e448` 取得pstorage句柄，`0x1e600` 暂存并启动原约0.5秒合并计时器，之后经原 `0x18788` UART和LPC日志持久化。同步更新原DM RAM上下文，使本次开机内重连也使用最新订阅。

相同内容不重复暂存，读取/暂存失败约0.5秒后重试，断连清除待保存标记。显式禁用也保存0；不产生CCCD写入、不绕过输入发送门控。恢复仍由原 `0x1ccd4→0x1cc24` 按当前绑定和加密状态完成。旧固件缺失的订阅无法凭空恢复，升级验证时需重新配对一次，让Windows真正订阅并等待保存。

## S110 ABI 来源

SDK9 / S110 v8 的调用号，不能用 nRF52 新SDK替代：

|功能|SVC|
|---|---|
|device_name_set|`0x7c`|
|appearance_set|`0x78`|
|conn_sec_get|`0x83`|
|uuid_vs_add|`0x63`|
|gatts_service_add|`0xa0`|
|gatts_characteristic_add|`0xa2`|
|gatts_value_set|`0xa4`|
|gatts_value_get|`0xa5`|
|gatts_hvx（原SDK helper内部）|`0xa6`|
|gatts_sys_attr_get|`0xaa`|

参考：[Nordic SDK9 GAP serialization](https://docs.nordicsemi.com/r/bundle/nrf5_sdk_v9.0.0/page/ble_serialization_s110_functions_gap.html)、[SDK9 S110 codecs](https://docs.nordicsemi.com/r/bundle/nrf5_sdk_v9.0.0/page/group_ser_conn_s110_codecs.html)。原 `0x1ba90` 的汇编也直接显示 `svc 0xa6` 和参数布局。

新版S110与旧版头文件有 `sd_ble_gap_encrypt` 插入带来的枚举差异；搜索到旧 `ble_gap.h` 不能当成v8调用号依据。

## 本体配置与诊断

`radio_config.c` 在Xbox下注册独立vendor服务，固定20字节请求与读取回复，兼容默认ATT MTU23。配置写入通过独立magic UART RPC交给LPC，只有主控持久化确认后才更新radio映射和返回成功；不覆盖原配置callback。协议见 [CONFIG_PROTOCOL.md](CONFIG_PROTOCOL.md)。

主循环每次调用配置服务，因此诊断请求不依赖输入已收齐、HID已订阅或新输入发生。输入收包/解析/发送计数、当前原始按钮、完整字段mask、定时器、加密/CCCD错误和当前报告可以分层观察。诊断不导出绑定密钥。

## 验证范围

软件验证用于字节定位、ARM调用约定、报告编码及模拟的事件/错误路径。SoftDevice无线调度、真实GATT客户端、原厂升级器、配对持久化及Windows原生XInput必须在设备上验证；静态分析或模拟SVC不能代替这些结论。

2026-11-09，用户确认修订4关机后RB+Steam重连输入已正常；此次实机确认及对应镜像校验值记录于 [LIVE_DIAGNOSTICS.md](LIVE_DIAGNOSTICS.md)。
