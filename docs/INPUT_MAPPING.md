# Steam Controller BLE delta → Xbox Series BLE input

实现：`src/input.c/.h`、`src/mapping.c/.h`。Xbox 输出布局采用 [XBOX_BLE_REFERENCE.md](XBOX_BLE_REFERENCE.md) 中的真实 283-byte Series Report Map。

## API 与内存

```c
void mapping_default(uint8_t mapping[24]);
int mapping_valid(const uint8_t mapping[24]);
uint32_t mapping_token(const uint8_t mapping[24]);

void input_reset(struct xbox_input *state);
void input_suppress_startup(struct xbox_input *state, uint32_t buttons);
int input_update(struct xbox_input *state, const uint8_t *packet, size_t len);
void input_report(const struct xbox_input *state, uint8_t report[16]);
void input_report_mapped(const struct xbox_input *state, uint8_t report[16],
                         const uint8_t mapping[24]);
```

- Cortex-M0 上 `sizeof(struct xbox_input) == 24`，以 `== 24` 静态断言固定。映射记录另占 24 字节，由调用方持有。
- 无动态分配、计时器、浮点、libm 或 libc 链接。轴缩放使用最多 15 步整数长除法，无硬件除法或 libgcc 依赖；使用 `abi.h` 的整数读写和项目 `memset/memcpy`。
- `mapping_default()` 写满 24 字节默认记录。`mapping_valid()` 返回 1/0，仅检查内容，不修改记录；NULL 返回 0。三个 mapping API 均无长度参数，非 NULL buffer 必须实际具有 24 字节，可不对齐。
- `mapping_token()` 是完整 24 字节的 **CRC32/IEEE**：反射多项式 `0xedb88320`，初值与最终 XOR 均为 `0xffffffff`，等价于 Python `zlib.crc32(record)`。它不做记录校验，输入须为非 NULL；返回 u32，传输时由调用方按协议编码。
- `input_reset()` 清空缓存、`received` 和启动抑制。它**不自动吞键**，可用于连接/断连重置。
- `input_update()` 返回 **1=有效包已接收，0=拒绝且完整状态不变**。1 不代表报告内容变化：仅 IMU 的包或无字段包也可返回 1。
- `input_report_mapped()` 读取当前配置，写满且仅写 **16 字节**，不修改 state/mapping；输出 buffer 不得与输入重叠。NULL 或非法配置回退默认。radio 使用此接口；配置接收/持久化前仍应调用 `mapping_valid()` 检查有效性。
- `input_report()` 是使用默认配置的兼容包装。两种报告接口都生成 HOGP Report characteristic value，**没有 Report ID 前缀**。
- `state->received` 累积有效包中出现的 chunk flags。`(received & SC_CHUNKS_INPUT) == SC_CHUNKS_INPUT` 表示至少收到过 buttons/triggers/stick/leftpad/rightpad 五类输入字段；不表示这些字段来自同一时刻。

## Delta 包契约

`packet` 指向**已经去除所有 UART/BLE 分片包装**的 delta 内容，前两个字节为小端 header。低 nibble 必须是 `4`。允许的 flags 为 `0x1ff0`，`0xe000` 中任何位出现都拒绝。

字段按以下顺序紧密排列，无对齐、无尾部填充：

| flag | 数据字节数 | 处理 |
|---:|---:|---|
| `0010` | 3 | 小端 24-bit buttons |
| `0020` | 2 | 左、右 trigger 原始 u8 |
| `0040` | 3 | extra，消费长度，不产生 Xbox 输入 |
| `0080` | 4 | stick：signed little-endian i16 X、Y |
| `0100` | 4 | left pad：signed little-endian i16 X、Y |
| `0200` | 4 | right pad：signed little-endian i16 X、Y |
| `0400` | 6 | accelerometer，消费长度，无 Xbox 映射 |
| `0800` | 6 | gyro，消费长度，无 Xbox 映射 |
| `1000` | 8 | quaternion，消费长度，无 Xbox 映射 |

先计算**整包精确长度**，再更新状态。缺字节、额外尾字节、未知 flag、错误 type、空指针都返回 0；包括抑制掩码和 received 在内的原状态完全保留。最长合法包为 42 字节。`04 00` 是合法空 delta，保留状态。

缺失字段保留之前的缓存。buttons chunk 是完整的 24-bit 按钮快照，不是对按钮做 XOR。

### 首帧必须由主控/连接层强制全量

**仅靠第一次 delta 无法恢复此前未发送的字段。** reset 后未知字段暂为中立；不能以“没有字段”等价于“物理控件已释放”。主控应在进入 Xbox 模式时强制发送 buttons/triggers/stick/leftpad/rightpad 的全量快照，连接层在重置缓存后也须请求/触发全量。相关 flags 为 `SC_CHUNKS_INPUT = 0x03b0`。

若无线 MCU reset 了输入缓存，而主 MCU 仍基于原先的 delta 缓存省略静止字段，会出现错误中立或丢失按住状态。`received` 只能帮助检测不完整，不能凭空重建缺失值。

本实现没有“250 ms 无更新就全释放”的规则：静止 delta 本来就可能不发送。连接断开、suspend、重启、输出队列处理由 radio 层负责。

## 24-byte 映射记录

| offset | 长度 | 内容 / 有效范围 |
|---:|---:|---|
| 0 | 1 | version，必须为 `1` |
| 1 | 1 | flags：bit0 invertRX；bit1 invertRY；bit2 requireLeftPadClick；bit3 **必须为 0**；bits4–7 斜向角度索引 |
| 2–17 | 16 | 每个 source 的 target ID，`0..17` |
| 18 | 2 | little-endian stickDeadzone，`0..16000` |
| 20 | 2 | little-endian padDeadzone，`0..16000` |
| 22 | 2 | little-endian dpadThreshold，`256..30000` |

角度索引 `0..15` 对应 `[45,5,10,15,20,25,30,35,40,0,50,55,60,65,70,75]` 度，所有索引都有效。与 Receiver 的基础映射布局一致；此处 bit3 为保留位，不能设置 Receiver 的 persistMode 标志。

source 顺序为 **A、B、X、Y、LB、RB、View、Menu、Steam、左背、右背、stickclick、leftclick、rightclick、LT 底、RT 底**。

target 编码：`0=无, 1=A, 2=B, 3=X, 4=Y, 5=LB, 6=RB, 7=Back, 8=Start, 9=Guide, 10=L3, 11=R3, 12=上, 13=下, 14=左, 15=右, 16=LT, 17=RT`。

默认 bindings：`[1,2,3,4,5,6,7,8,9,11,10,10,0,11,16,17]`；deadzone 为 **3000 / 3500**，方向阈值 **8000**，须按左板，斜向宽度 **30°**，flags 为 `0x64`。完整记录：

```text
01 64 01 02 03 04 05 06 07 08 09 0b 0a 0a 00 0b 10 11 b8 0b ac 0d 40 1f
```

## 默认映射与可配置输出

以下 SC 位按本任务指定的原始按钮字段编码；Xbox mask 是 16-byte report 的 offset 13–14 小端按钮字段。

| Steam Controller | SC mask | Xbox |
|---|---:|---|
| A | `000080` | A，`0001` |
| B | `000020` | B，`0002` |
| X | `000040` | X，`0008` |
| Y | `000010` | Y，`0010` |
| LB | `000008` | LB，`0040` |
| RB | `000004` | RB，`0080` |
| View | `001000` | View，`0400` |
| Steam | `002000` | Guide，`1000` |
| Menu | `004000` | Menu，`0800` |
| 左背 | `008000` | R3，`4000` |
| 右背 | `010000` | L3，`2000` |
| 左板按 | `020000` | 默认无独立 target；允许左触摸坐标产生方向 |
| 右板按 | `040000` | R3，`4000` |
| 左触摸 | `080000` | 与左板按共同启用 left pad → D-pad |
| 右触摸 | `100000` | 启用 right pad → right stick |
| 摇杆按 | `400000` | L3，`2000` |
| LT 底 | `000002` | LT 全按，1023 |
| RT 底 | `000001` | RT 全按，1023 |

右背与摇杆按为 OR，左背与右板按为 OR；其中一个松开不会覆盖另一个仍按住的输入。每个可绑定 source 均可映射至任意 target，包括方向和扳机；相同目标作 OR。模拟扳机持续独立输出，任何绑定 LT/RT 的按键会把对应值提升至 1023，松开立即恢复缓存的模拟值。底键重映射不会移除模拟扳机本身。

其他 SC 按钮位忽略。Xbox Share 始终为 0，所有保留位/padding 为 0。

### 轴、扳机、触摸

- SC stick → Xbox left stick；SC right pad → Xbox right stick，都是绝对位置映射。
- 使用 Receiver 基础逐轴死区算法：先把原值夹至 `[-32767,32767]`，`abs(value) <= deadzone` 精确归零；否则输出幅值 `floor((abs(value)-deadzone)*32767/(32767-deadzone))`，保留符号。死区外线性拉伸到满量程，未采用径向摇杆死区。
- 对缩放结果 `v`，Xbox X 为 `32768+v`，SC 正 Y 向上，Xbox Y 为 `32768-v`。范围 `1..65535`，中心精确 `0x8000`；原始 `-32768` 与 `-32767` 同样映射至端点，反转也不溢出。
- flags bit0/bit1 分别反转右板 X/Y；右 Y 反转是相对默认 SC→Xbox 的 Y 翻转。左摇杆不受这些标志影响。
- 左右扳机各自 `u8 → u10`：`(value << 2) | (value >> 6)`，`0→0`、`255→1023`，单调，距理想连续缩放小于 1 个量化单位；高 6 bit 为 0。
- **右触摸位松开时，下一份仅含 buttons 的 delta 就立即使 Xbox right stick 回到 `8000,8000`**，不需要等待坐标更新。
- pad 原始坐标仍然缓存；重新触摸而没有新 pad chunk 时，会按 delta“缺字段保留”的规则恢复该坐标，而不是凭空生成新位置。
- 左板以 touch 位门控，默认还要求 left click；释放 click 或 touch 的仅 buttons delta 即可撤去板上的方向。清除 flags bit2 后只要求 touch。单独 left click 不当成触摸；它自己的 binding 始终独立生效。

### 左板 D-pad

参考 `Receiver/SCReceiver/input_mode.cpp` 的基础方向算法，使用 **径向阈值**：`abs(X)^2 + abs(Y)^2 >= dpadThreshold^2` 时启用，包含边界。原始坐标绝对值、平方和均为 uint32，`(-32768,-32768)` 不溢出。方向阈值独立于摇杆/右板死区及角度宽度。

斜向角度表示以 45° 方向为中心的扇区宽度，默认 30°：每个象限偏角 30°–60° 为斜向，靠近水平/垂直轴为单方向。45° 设置使八个方向等宽；0° 禁止斜向，取绝对值较大的轴，等值选择水平。

固件用 `tan((90-angle)/2)*10000` 的整数表判断两轴，边界使用 `>=`；默认 30° 的比值为 5774，因此 `(10000,5773)` 为右，`(10000,5774)` 为右上。无 atan、sin、sqrt 或 libm。方向与按键重映射先 OR，再逐轴消除同时存在的上/下、左/右，最后编码 hat；例如板上右上与 D-down 按键同时生效，最终仅右。

hat：`0=neutral, 1=N, 2=NE, 3=E, 4=SE, 5=S, 6=SW, 7=W, 8=NW`。

## 启动手势抑制：显式、一次性

```c
input_reset(&input);
/* 仅首次启动模式选择后的输入会话；后续重连不自动执行这一句。 */
input_suppress_startup(&input, SC_BUTTON_RB | SC_BUTTON_STEAM);
/* 若此次启动模式还使用 Y，调用时再 OR SC_BUTTON_Y。 */
```

合同：

1. `input_suppress_startup()` 必须在相关的 `input_reset()` **之后**调用；reset 会清空抑制状态。
2. radio 若在启动后、首次连接时又调用 reset，应在自身保存 pending-boot mask，并在第一次连接的 reset 后一次性设置。不要在每次重连时重新 arm。
3. `SC_BUTTON_STARTUP = RB | STEAM | Y`。仅允许这些 source 进入抑制 mask；其他传入位被滤掉，**旧手势的左背不会被抑制**。未选择 Y 的启动不会吞 Y。
4. 在 remapping **之前**屏蔽指定 SC 源按钮，不屏蔽整个 report。即使 RB/Steam/Y 被改为 LT 或方向，也会正确抑制；映射至同一目标的其他 source 仍可正常输出。模拟扳机/轴也保持独立。
5. 指定键中只要还有任何一个按住，整个启动 mask 保持有效。只有收到 buttons chunk，且 `buttons & suppress == 0`，才永久解除该次抑制；无须其他无关键也松开。
6. 无 buttons chunk 的 delta 不会解除 latch；错误包不会解除 latch。
7. 解除后，后续正常按 RB/Steam/Y 直接上报。普通 `input_reset()` 后也没有隐式抑制。

“断开后是否继续同一次尚未结束的启动手势”属于 radio 生命周期策略；输入模块不会在 reset 后暗中保留旧连接状态。

## Cortex-M0 执行测试

测试使用 `.tools/gcc/bin/arm-none-eabi-gcc`（或 PATH 中同名工具）将实际 `src/input.c`、`src/mapping.c` 和 `src/runtime.c` 编译成 freestanding Cortex-M0 Thumb ELF，链接时 `-nostdlib`，编译开启 `-Wall -Wextra -Werror`。然后由 Unicorn **Cortex-M0 CPU 模型**执行生成的机器码，未用 Python/宿主 C 重写实现来代替被测代码。

依赖步骤完成后可运行：

```text
python -m pip install --target .tools/python unicorn==2.1.4
python -m unittest discover -s tests -p test_input.py -v
```

也兼容项目的 `python -m unittest discover -s tests -v`。依赖可全局安装；helper 会优先使用 `.tools/python`。缺少编译器/模拟器时测试失败并说明依赖，不静默 skip。`tests/arm_runner.py` 是可复用 helper，临时编译目录在 `.tools/arm-test-*`，结束后清理。

已执行通过 **29 项测试**，包含：

- 全部 512 种 chunk 组合，含被忽略的 extra/IMU 字段；精确 offset 和长度；每种组合截短/追加数据都拒绝。
- 完整包每一个截断位置、非法 type/flags/null、靠近未映射内存的单字节 header；错误后完整 24-byte state 不变。
- 24 字节默认配置、全部 256 种 flags、每个绑定槽的合法/非法目标、三个数值范围边界、NULL/非法配置回退。
- CRC 与 Python `zlib.crc32` 交叉校验，覆盖有效/无效内容、全零/全 FF、完整 192 个单 bit 修改；配置在未对齐地址和 RAM 末尾的精确访问。
- 四轴端点、右 X/Y 四种反转组合、死区 `0/1/3000/3500/16000` 的边界/线性/单调性、全部 256 个模拟 trigger 值。
- 全部 **16×18** source→target 组合、24 个原始按钮位、L3/R3 OR、底键重映射和模拟扳机独立性。
- 8 向 D-pad、径向阈值边界和 INT16_MIN、16 种斜向角度的四象限双侧边界、0° 等值规则、相反方向抵消、click/touch 即时门控。
- 缺字段缓存与首帧不完整检测；RB/Steam/Y 启动抑制的分步松开、Y 可选、左背不被吞、抑制先于重映射、普通 reset/reconnect 不隐式 arm。
- report 前后哨兵、state 边界哨兵、ARM 栈指针与 callee-saved register ABI。

这些结果来自真实 Cortex-M0 编译产物的模拟执行；硬件坐标校准、radio 集成与 Windows 枚举由对应集成验证覆盖。

## 电池范围

本模块只接收 delta type 4；type 5 必须拒绝且不修改输入。当前没有核对 SC BLE type 5 / UART `0x55` 的确切电池字段，不将整包某个猜测字节解释为 BAS 百分比。Xbox BAS 接口格式见参考文档，其数据来源需要另行验证。
