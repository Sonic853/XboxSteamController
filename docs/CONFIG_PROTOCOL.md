# 本体配置通道 v1（固件 revision 2）

仅 Xbox profile 注册以下独立 GATT 服务：

|用途|UUID|属性|
|---|---|---|
|服务|`85300001-2015-4c53-8f44-58426c65434d`|Primary|
|请求|`85300002-2015-4c53-8f44-58426c65434d`|Write Request，20字节|
|回复|`85300003-2015-4c53-8f44-58426c65434d`|Read，20字节|

属性值在 S110 栈内，固定20字节，无CCCD。读写要求 Mode1 Level2 加密。广播仍为HID与完整Xbox名称，网页以设备名选择、`optionalServices` 请求访问自定义服务。HID服务不经 Web Bluetooth 访问。

## BLE帧

请求 `[opcode, sequence, argument, 0, payload16]`；回复 `[opcode, sequence, status, argument, payload16]`。客户端必须串行化请求，写后轮询回复，匹配操作码、sequence与argument。status=4表示未完成，不能再次提交写操作。

status：0成功，1参数错误，2并发冲突，3忙，4等待主控持久化，5超时/持久化结果未知，6尚未加载主控映射，7主控存储错误。提交回复payload为当前CRC32、主控原始status、8字节0。超时必须重新读取，不能认为没有写入。

|opcode|argument|payload|
|---|---|---|
|1 读映射|0|回复mapping前16字节|
|1 读映射|1|回复mapping后8字节、CRC32、主控status|
|2 暂存|0|请求expectedCRC32、mapping前12字节|
|2 暂存|1|请求mapping后12字节、4字节0|
|3 提交|0|请求拟写入mappingCRC32、expectedCRC32、8字节0；等待Flash提交回复|
|0x10 诊断|0..5|见下表|

映射24字节定义见 `mapping.h` / `INPUT_MAPPING.md`。CRC采用 IEEE CRC32（初值/终值异或FFFFFFFF、多项式EDB88320），也是乐观并发token。分页读需校验完整CRC；不一致只重试读取。提交再次核对完整暂存内容CRC及expectedCRC，避免多客户端交错暂存形成混合配置。暂存未提交不改变当前映射，断连丢弃未提交的暂存。已发往LPC的提交继续等待结果，断连不会撤销Flash操作。

## 诊断payload（各16字节，小端）

|页|字段|
|---|---|
|0|u32 `0x32435358`（XSC2），u16固件2，u8协议1，u8flags（loaded/pending/staged），u32 ticks，u32主控status|
|1|u32输入包数，u32解析成功，u32解析失败，u16最后长度，u16累计字段mask|
|2|u32发送尝试，u32成功，u32最后HVX错误，u16连接句柄，u8安全模式/等级，u8密钥长度|
|3|u32安全查询错误，u32CCCD查询错误，u16CCCD，u8阻塞原因，u8profile状态，u32服务调用数|
|4|u32原始按钮，u32启动抑制，u16最后包头，6字节0|
|5|当前16字节Xbox输入报告，不含Report ID|

阻塞原因：0就绪，1断开，2挂起，3boot protocol，4安全查询失败，5未满足加密，6CCCD查询失败，7未订阅，8缺输入字段，9尚无timer tick。页3的profile flags低4位依次为Xbox、initialized、suspended、boot protocol。计数为开机累计32位值，回绕属于正常行为；各页不是同一时刻的原子快照。没有导出绑定密钥。

## radio ↔ LPC UART

magic offset=`0x58434d31`：

- 读：`[5d, magic:u32, 24:u32]`（9B）。
- 写：`[5e, magic:u32, 28:u32, expectedCRC:u32, mapping24]`（37B）。
- 回：`[5d, magic:u32, status:u32, mapping24, currentCRC:u32]`（37B）。

radio直接调用原UART sender `0x16a46`，不替换原配置callback。LPC只在延后到主循环的Flash操作完成后回复写结果；忙或入队失败明确返回3。绑定日志保留区+144写XCM1 magic，+148放mapping，继续使用整条256字节记录CRC与双扇区轮换。旧记录无mapping则使用默认值，更新绑定/重新配对保留映射。

本配置协议与Receiver的USB WebHID协议独立。真实浏览器/蓝牙/主控IAP往返仍须实物验证。
