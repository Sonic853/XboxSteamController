"""Behavioral tests against the actual compiled ARM implementation."""

import math
import struct
import unittest
import zlib

from arm_runner import ArmBuild, ArmMachine, ROOT


# Independent wire constants: tests intentionally do not parse input.h.
BUTTONS, TRIGGERS, EXTRA, STICK, LEFTPAD, RIGHTPAD, ACCEL, GYRO, QUAT = (
    0x10, 0x20, 0x40, 0x80, 0x100, 0x200, 0x400, 0x800, 0x1000
)
LEFT_TOUCH, RIGHT_TOUCH = 0x080000, 0x100000
LEFT_BACK, RIGHT_BACK, STEAM, Y = 0x008000, 0x010000, 0x002000, 0x10
LEFT_CLICK, RIGHT_CLICK, STICK_CLICK, RB = 0x020000, 0x040000, 0x400000, 4
SOURCE_BITS = [7, 5, 6, 4, 3, 2, 12, 14, 13, 15, 16, 22, 17, 18, 1, 0]
TARGET_MASKS = [0, 1, 2, 8, 16, 64, 128, 1024, 2048, 4096, 8192, 16384]
ANGLES = [45, 5, 10, 15, 20, 25, 30, 35, 40, 0, 50, 55, 60, 65, 70, 75]
DEFAULT_MAPPING = bytes([1, 0x64, 1, 2, 3, 4, 5, 6, 7, 8, 9, 11, 10, 10, 0, 11, 16, 17]) + struct.pack("<3H", 3000, 3500, 8000)
NEUTRAL = bytes.fromhex("00 80 00 80 00 80 00 80 00 00 00 00 00 00 00 00")


def buttons(value):
    return value.to_bytes(3, "little")


def axes(x, y):
    return struct.pack("<hh", x, y)


def delta(chunks):
    flags = sum(chunks)
    return struct.pack("<H", flags | 4) + b"".join(chunks[flag] for flag in sorted(chunks))


def configuration(*, flags=0x64, bindings=None, stick=3000, pad=3500, threshold=8000):
    result = bytearray(DEFAULT_MAPPING)
    result[1] = flags
    if bindings is not None:
        result[2:18] = bytes(bindings)
    struct.pack_into("<3H", result, 18, stick, pad, threshold)
    return result


def scaled_axis(raw, deadzone, invert=False):
    # Mathematical specification, using Python exact arithmetic (not ARM division).
    magnitude = max(0, min(abs(raw), 32767) - deadzone) * 32767 // (32767 - deadzone)
    return 32768 + (-magnitude if (raw < 0) != invert else magnitude)


class InputTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.build = ArmBuild(
            [ROOT / "src" / "input.c", ROOT / "src" / "mapping.c", ROOT / "src" / "runtime.c"],
            probe='#include "input.h"\n'
                  'unsigned input_test_size(void) { return sizeof(struct xbox_input); }\n'
                  'unsigned input_test_received(const struct xbox_input *s) { return s->received; }\n',
        )

    @classmethod
    def tearDownClass(cls):
        cls.build.close()

    def setUp(self):
        self.arm = ArmMachine(self.build)
        self.state = self.arm.RAM + 0x100
        self.packet = self.arm.RAM + 0x400
        self.output = self.arm.RAM + 0x800
        self.config = self.arm.RAM + 0xC01  # Deliberately unaligned wire record.
        self.size = self.arm.call("input_test_size")
        self.assertEqual(self.size, 24)
        self.arm.write(self.state - 8, b"\xa5" * (self.size + 16))
        self.arm.call("input_reset", self.state)
        self.assert_state_guards()

    def assert_state_guards(self):
        self.assertEqual(self.arm.read(self.state - 8, 8), b"\xa5" * 8)
        self.assertEqual(self.arm.read(self.state + self.size, 8), b"\xa5" * 8)

    def snapshot(self):
        return self.arm.read(self.state, self.size)

    def update(self, chunks):
        return self.update_raw(delta(chunks))

    def update_raw(self, packet):
        self.arm.write(self.packet, packet)
        result = self.arm.call("input_update", self.state, self.packet, len(packet))
        self.assert_state_guards()
        return result

    def report(self, mapping=None):
        before = self.snapshot()
        self.arm.write(self.output - 8, b"\xa5" * 32)
        if mapping is None:
            self.arm.call("input_report", self.state, self.output)
        else:
            self.arm.write(self.config - 8, b"\xa5" * 40)
            self.arm.write(self.config, mapping)
            self.arm.call("input_report_mapped", self.state, self.output, self.config)
            self.assertEqual(self.arm.read(self.config - 8, 40), b"\xa5" * 8 + bytes(mapping) + b"\xa5" * 8)
        self.assertEqual(self.arm.read(self.output - 8, 8), b"\xa5" * 8)
        self.assertEqual(self.arm.read(self.output + 16, 8), b"\xa5" * 8)
        self.assertEqual(self.snapshot(), before, "report generation must not mutate cached deltas")
        return self.arm.read(self.output, 16)

    def mapped_buttons(self, mapping=None):
        return struct.unpack_from("<H", self.report(mapping), 13)[0]

    def test_reset_and_empty_delta(self):
        self.assertEqual(self.size, 24)
        self.assertEqual(self.report(), NEUTRAL)
        before = self.snapshot()
        self.assertEqual(self.update({}), 1)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.arm.call("input_test_received", self.state), 0)

    def test_all_fields_offsets_and_reserved_bits(self):
        raw = (0x80 | Y | 8 | 4 | 0x1000 | STEAM | 0x4000 | LEFT_BACK |
               RIGHT_BACK | RIGHT_CLICK | STICK_CLICK | LEFT_CLICK | LEFT_TOUCH | RIGHT_TOUCH)
        self.assertEqual(self.update({
            BUTTONS: buttons(raw), TRIGGERS: bytes([17, 225]), EXTRA: b"\xff\x55\xaa",
            STICK: axes(-32768, 32767), LEFTPAD: axes(-20000, 20000),
            RIGHTPAD: axes(12345, -32768), ACCEL: b"\xf1" * 6,
            GYRO: b"\xa2" * 6, QUAT: b"\x73" * 8,
        }), 1)
        self.assertEqual(self.report(), bytes.fromhex(
            "01 00 01 00 ae a6 ff ff 44 00 87 03 08 d1 7c 00"
        ))
        self.assertEqual(self.arm.call("input_test_received", self.state), 0x1FF0)

    def test_each_button_and_unused_bits(self):
        mapping = {
            0x80: 0x0001, 0x20: 0x0002, 0x40: 0x0008, 0x10: 0x0010,
            8: 0x0040, 4: 0x0080, 0x1000: 0x0400, 0x4000: 0x0800,
            STEAM: 0x1000, LEFT_BACK: 0x4000, RIGHT_BACK: 0x2000,
            STICK_CLICK: 0x2000, RIGHT_CLICK: 0x4000,
        }
        for bit in range(24):
            with self.subTest(raw_bit=bit):
                raw = 1 << bit
                self.assertEqual(self.update({BUTTONS: buttons(raw)}), 1)
                expected = bytearray(NEUTRAL)
                struct.pack_into("<H", expected, 13, mapping.get(raw, 0))
                if raw == 2:
                    struct.pack_into("<H", expected, 8, 1023)
                if raw == 1:
                    struct.pack_into("<H", expected, 10, 1023)
                self.assertEqual(self.report(), bytes(expected))

    def test_click_aliases_are_or_not_overwrites(self):
        for sources, expected in [(RIGHT_BACK | STICK_CLICK, 0x2000),
                                  (LEFT_BACK | RIGHT_CLICK, 0x4000)]:
            with self.subTest(sources=sources):
                first = sources & -sources
                for raw in [sources, first, sources ^ first, 0]:
                    self.update({BUTTONS: buttons(raw)})
                    self.assertEqual(self.mapped_buttons(), expected if raw else 0)

    def test_axis_extremes_y_and_optional_right_inversion(self):
        values = [-32768, -32767, -1, 0, 1, 32766, 32767]
        inverted = [65535, 65535, 32769, 32768, 32767, 2, 1]
        self.update({BUTTONS: buttons(RIGHT_TOUCH)})
        for value, expected_y in zip(values, inverted):
            with self.subTest(axis=value):
                self.update({STICK: axes(value, value), RIGHTPAD: axes(value, value)})
                expected_x = max(value, -32767) + 32768
                for inversion in range(4):
                    config = configuration(flags=0x64 | inversion, stick=0, pad=0)
                    self.assertEqual(struct.unpack_from("<4H", self.report(config)),
                                     (expected_x, expected_y,
                                      expected_y if inversion & 1 else expected_x,
                                      expected_x if inversion & 2 else expected_y))

    def test_trigger_full_range_monotonic_and_ten_bit(self):
        previous = -1
        for value in range(256):
            self.update({TRIGGERS: bytes([value, 255 - value])})
            left, right = struct.unpack_from("<HH", self.report(), 8)
            self.assertGreater(left, previous)
            self.assertLessEqual(left, 1023)
            self.assertLessEqual(right, 1023)
            # Independent continuous scale: bit replication differs by < 1 unit.
            self.assertLess(abs(left * 255 - value * 1023), 255)
            self.assertLess(abs(right * 255 - (255 - value) * 1023), 255)
            self.assertEqual(left + right, 1023)
            previous = left
        self.assertEqual(left, 1023)
        self.assertEqual(right, 0)
        self.update({TRIGGERS: bytes([64, 192])})
        self.assertEqual(struct.unpack_from("<HH", self.report(), 8), (257, 771))

    def test_pad_release_without_coordinates_immediately_centers(self):
        self.update({BUTTONS: buttons(LEFT_TOUCH | LEFT_CLICK | RIGHT_TOUCH),
                     LEFTPAD: axes(30000, 30000), RIGHTPAD: axes(12000, -20000)})
        active = self.report()
        self.assertEqual(active[12], 2)
        self.assertNotEqual(active[4:8], NEUTRAL[4:8])
        self.update({BUTTONS: buttons(0)})
        self.assertEqual(self.report(), NEUTRAL)
        # Missing fields retain their cache, while report visibility is touch-gated.
        self.update({BUTTONS: buttons(LEFT_TOUCH | LEFT_CLICK | RIGHT_TOUCH)})
        self.assertEqual(self.report(), active)

    def test_left_pad_eight_directions_and_radial_threshold(self):
        self.update({BUTTONS: buttons(LEFT_TOUCH | LEFT_CLICK)})
        cases = [(0, 20000, 1), (20000, 20000, 2), (20000, 0, 3),
                 (20000, -20000, 4), (0, -20000, 5), (-20000, -20000, 6),
                 (-20000, 0, 7), (-20000, 20000, 8), (8000, 0, 3),
                 (-8000, 0, 7), (0, 8000, 1), (0, -8000, 5),
                 (4800, 6400, 2), (4799, 6400, 0), (4800, 6399, 0),
                 (6000, 6000, 2), (10000, 5000, 3), (5000, 10000, 1),
                 (-32768, -32768, 6), (-32768, 32767, 8)]
        cases.extend((x, y, 0) for x, y in [(7999, 0), (-7999, 0), (0, 7999),
                                            (0, -7999), (0, 0), (2000, -2000)])
        for x, y, expected in cases:
            with self.subTest(x=x, y=y):
                self.update({LEFTPAD: axes(x, y)})
                self.assertEqual(self.report()[12], expected)
        self.update({BUTTONS: buttons(LEFT_CLICK), LEFTPAD: axes(20000, 0)})
        self.assertEqual(self.report()[12], 0, "left click alone is not touch")

    def test_partial_deltas_retain_other_fields(self):
        self.update({BUTTONS: buttons(0x80 | RIGHT_TOUCH), TRIGGERS: b"\xff\x40",
                     STICK: axes(-1000, 4000), RIGHTPAD: axes(2000, -3000)})
        expected = self.report()
        for chunks in [{EXTRA: b"\x00\xff\x55"}, {ACCEL: b"\x01" * 6},
                       {GYRO: b"\x02" * 6}, {QUAT: b"\x03" * 8}, {}] * 3:
            self.assertEqual(self.update(chunks), 1)
            self.assertEqual(self.report(), expected, "no synthetic timeout/idle release")
        self.update({TRIGGERS: b"\x00\xff"})
        changed = self.report()
        self.assertEqual(changed[:8] + changed[12:], expected[:8] + expected[12:])
        self.assertEqual(changed[8:12], b"\x00\x00\xff\x03")

    def test_first_partial_snapshot_exposes_missing_fields(self):
        self.update({BUTTONS: buttons(0x80)})
        report = bytearray(NEUTRAL)
        report[13] = 1
        self.assertEqual(self.report(), bytes(report))
        self.assertNotEqual(self.arm.call("input_test_received", self.state) & 0x3B0, 0x3B0)
        self.update({TRIGGERS: b"\x00\x00", STICK: axes(0, 0),
                     LEFTPAD: axes(0, 0), RIGHTPAD: axes(0, 0)})
        self.assertEqual(self.arm.call("input_test_received", self.state) & 0x3B0, 0x3B0)

    def test_invalid_headers_and_lengths_do_not_mutate(self):
        self.update({BUTTONS: buttons(RB | STEAM | 0x80), STICK: axes(32000, -32000)})
        self.arm.call("input_suppress_startup", self.state, RB | STEAM)
        before = self.snapshot()
        full = delta({BUTTONS: buttons(0), TRIGGERS: b"\xff\xff", EXTRA: b"123",
                      STICK: axes(1, 2), LEFTPAD: axes(3, 4), RIGHTPAD: axes(5, 6),
                      ACCEL: b"abcdef", GYRO: b"ghijkl", QUAT: b"mnopqrst"})
        invalid = [full[:cut] for cut in range(len(full))]
        invalid += [full + b"\x00", full + b"\x00" * 20]
        invalid += [struct.pack("<H", low) for low in range(16) if low != 4]
        invalid += [struct.pack("<H", flag | 4) for flag in [0x2000, 0x4000, 0x8000, 0xE000]]
        for packet in invalid:
            with self.subTest(packet=packet.hex()):
                self.assertEqual(self.update_raw(packet), 0)
                self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.arm.call("input_update", self.state, 0, 2), 0)
        self.assertEqual(self.arm.call("input_update", 0, self.packet, 2), 0)
        self.assertEqual(self.snapshot(), before)

    def test_single_header_byte_does_not_overread(self):
        address = self.arm.RAM + self.arm.RAM_SIZE - 1
        self.arm.write(address, b"\x04")
        before = self.snapshot()
        self.assertEqual(self.arm.call("input_update", self.state, address, 1), 0)
        self.assertEqual(self.snapshot(), before)

    def test_all_512_chunk_combinations_and_exact_lengths(self):
        fields = {
            BUTTONS: buttons(LEFT_TOUCH | LEFT_CLICK | RIGHT_TOUCH | 0x80), TRIGGERS: b"\xff\x00",
            EXTRA: b"\x55\xaa\xff", STICK: axes(12345, -23456),
            LEFTPAD: axes(-30000, -30000), RIGHTPAD: axes(-20000, 12000),
            ACCEL: bytes(range(6)), GYRO: bytes(range(6, 12)), QUAT: bytes(range(12, 20)),
        }
        flags = sorted(fields)
        for combination in range(512):
            with self.subTest(combination=combination):
                self.arm.call("input_reset", self.state)
                chunks = {flag: fields[flag] for i, flag in enumerate(flags) if combination & (1 << i)}
                packet = delta(chunks)
                self.assertEqual(self.update_raw(packet), 1)
                expected = bytearray(NEUTRAL)
                if STICK in chunks:
                    struct.pack_into("<HH", expected, 0, scaled_axis(12345, 3000),
                                     scaled_axis(-23456, 3000, True))
                if TRIGGERS in chunks:
                    struct.pack_into("<HH", expected, 8, 1023, 0)
                if BUTTONS in chunks:
                    expected[13] = 1
                    if LEFTPAD in chunks:
                        expected[12] = 6
                    if RIGHTPAD in chunks:
                        struct.pack_into("<HH", expected, 4, scaled_axis(-20000, 3500),
                                         scaled_axis(12000, 3500, True))
                self.assertEqual(self.report(), bytes(expected))
                before = self.snapshot()
                for malformed in [packet[:-1], packet + b"\x00"]:
                    self.assertEqual(self.update_raw(malformed), 0)
                    self.assertEqual(self.snapshot(), before)

    def test_startup_suppression_until_all_selected_keys_release(self):
        selected = RB | STEAM | Y
        self.arm.call("input_suppress_startup", self.state, selected)
        self.update({BUTTONS: buttons(selected | 0x80 | 0x4000)})
        self.assertEqual(self.mapped_buttons(), 0x0801)
        # Either still-held selected key keeps the entire startup latch active.
        for raw in [RB | 0x80, STEAM | Y | 0x80, Y | 0x80]:
            self.update({BUTTONS: buttons(raw)})
            self.assertEqual(self.mapped_buttons(), 1)
        self.update({GYRO: bytes(6)})
        self.assertEqual(self.mapped_buttons(), 1)
        self.update({BUTTONS: buttons(0x80)})  # Selected keys all released; A need not release.
        self.update({BUTTONS: buttons(selected | 0x80)})
        self.assertEqual(self.mapped_buttons(), 0x1091)

    def test_startup_mask_does_not_swallow_y_or_old_left_back(self):
        self.arm.call("input_suppress_startup", self.state, RB | STEAM | LEFT_BACK | 0x80)
        self.update({BUTTONS: buttons(RB | STEAM | LEFT_BACK | Y | 0x80 | STICK_CLICK)})
        self.assertEqual(self.mapped_buttons(), 0x6011)
        # Old left-back gesture and A cannot enter the allowed startup mask.
        self.update({BUTTONS: buttons(LEFT_BACK | Y | 0x80)})
        self.update({BUTTONS: buttons(RB | STEAM | LEFT_BACK | Y | 0x80)})
        self.assertEqual(self.mapped_buttons(), 0x5091)

    def test_plain_reset_and_reconnect_never_implicitly_arm_suppression(self):
        held = RB | STEAM | Y | 0x80
        self.update({BUTTONS: buttons(held)})
        self.assertEqual(self.mapped_buttons(), 0x1091)
        self.arm.call("input_suppress_startup", self.state, RB | STEAM | Y)
        self.assertEqual(self.mapped_buttons(), 1)
        self.arm.call("input_reset", self.state)
        self.update({BUTTONS: buttons(held)})
        self.assertEqual(self.mapped_buttons(), 0x1091)

    def test_mapping_default_wire_bytes_and_wrapper_equivalence(self):
        self.arm.write(self.config - 8, b"\xa5" * 40)
        self.arm.call("mapping_default", self.config)
        self.assertEqual(self.arm.read(self.config - 8, 40), b"\xa5" * 8 + DEFAULT_MAPPING + b"\xa5" * 8)
        self.assertEqual(self.arm.call("mapping_valid", self.config), 1)
        self.update({BUTTONS: buttons(0xFFFFFF), STICK: axes(4000, -32768),
                     LEFTPAD: axes(10000, 5000), RIGHTPAD: axes(-32768, 12000),
                     TRIGGERS: bytes([12, 34])})
        self.assertEqual(self.report(), self.report(DEFAULT_MAPPING))

    def valid_mapping(self, config):
        self.arm.write(self.config - 8, b"\xa5" * 40)
        self.arm.write(self.config, config)
        result = self.arm.call("mapping_valid", self.config)
        self.assertEqual(self.arm.read(self.config - 8, 40), b"\xa5" * 8 + bytes(config) + b"\xa5" * 8)
        return result

    def test_mapping_version_flags_bindings_and_numeric_boundaries(self):
        self.assertEqual(self.arm.call("mapping_valid", 0), 0)
        for version in [0, 1, 2, 255]:
            config = bytearray(DEFAULT_MAPPING)
            config[0] = version
            self.assertEqual(self.valid_mapping(config), int(version == 1))
        for flags in range(256):
            with self.subTest(flags=flags):
                self.assertEqual(self.valid_mapping(configuration(flags=flags)), int(not flags & 8))
        for index in range(16):
            for target in [0, 1, 17, 18, 255]:
                config = bytearray(DEFAULT_MAPPING)
                config[2 + index] = target
                self.assertEqual(self.valid_mapping(config), int(target <= 17))
        for offset, cases in [
            (18, [(0, 1), (16000, 1), (16001, 0), (65535, 0)]),
            (20, [(0, 1), (16000, 1), (16001, 0), (65535, 0)]),
            (22, [(0, 0), (255, 0), (256, 1), (30000, 1), (30001, 0), (65535, 0)]),
        ]:
            for value, expected in cases:
                with self.subTest(offset=offset, value=value):
                    config = bytearray(DEFAULT_MAPPING)
                    struct.pack_into("<H", config, offset, value)
                    self.assertEqual(self.valid_mapping(config), expected)

    def test_mapping_crc32_ieee_all_bytes_and_single_bit_changes(self):
        vectors = [DEFAULT_MAPPING, bytes(24), b"\xff" * 24, bytes(range(24)),
                   b"123456789123456789123456"]
        for bit in range(192):
            changed = bytearray(DEFAULT_MAPPING)
            changed[bit // 8] ^= 1 << (bit % 8)
            vectors.append(bytes(changed))
        for config in vectors:
            with self.subTest(config=config.hex()):
                self.arm.write(self.config, config)
                self.assertEqual(self.arm.call("mapping_token", self.config), zlib.crc32(config))
                self.assertEqual(self.arm.read(self.config, 24), config)
        default_token = zlib.crc32(DEFAULT_MAPPING)
        self.assertTrue(all(zlib.crc32(config) != default_token for config in vectors[5:]))

    def test_mapping_exact_24_byte_access_at_memory_boundary(self):
        address = self.arm.RAM + self.arm.RAM_SIZE - 24
        self.arm.write(address - 8, b"\xa5" * 32)
        self.arm.call("mapping_default", address)
        self.assertEqual(self.arm.read(address - 8, 32), b"\xa5" * 8 + DEFAULT_MAPPING)
        self.assertEqual(self.arm.call("mapping_valid", address), 1)
        self.assertEqual(self.arm.call("mapping_token", address), zlib.crc32(DEFAULT_MAPPING))
        self.arm.call("input_report_mapped", self.state, self.output, address)
        self.assertEqual(self.arm.read(self.output, 16), NEUTRAL)

    def test_invalid_or_null_mapping_falls_back_to_defaults(self):
        self.update({BUTTONS: buttons(LEFT_BACK | RIGHT_BACK | LEFT_CLICK | LEFT_TOUCH | RIGHT_TOUCH),
                     STICK: axes(32767, -32768), RIGHTPAD: axes(-20000, 12000),
                     LEFTPAD: axes(10000, 10000)})
        expected = self.report()
        for offset, value in [(0, 0), (0, 2), (1, 8), (2, 255), (17, 18)]:
            config = bytearray(DEFAULT_MAPPING)
            config[offset] = value
            self.assertEqual(self.report(config), expected)
        for offset in [18, 20, 22]:
            config = bytearray(DEFAULT_MAPPING)
            struct.pack_into("<H", config, offset, 32767)
            self.assertEqual(self.report(config), expected)
        self.arm.call("input_report_mapped", self.state, self.output, 0)
        self.assertEqual(self.arm.read(self.output, 16), expected)

    def test_all_sixteen_sources_to_all_eighteen_targets(self):
        for source, bit in enumerate(SOURCE_BITS):
            self.update({BUTTONS: buttons(1 << bit)})
            for target in range(18):
                with self.subTest(source=source, target=target):
                    bindings = [0] * 16
                    bindings[source] = target
                    expected = bytearray(NEUTRAL)
                    if target < 12:
                        struct.pack_into("<H", expected, 13, TARGET_MASKS[target])
                    elif target < 16:
                        expected[12] = [1, 5, 7, 3][target - 12]
                    else:
                        struct.pack_into("<H", expected, 8 + 2 * (target - 16), 1023)
                    self.assertEqual(self.report(configuration(bindings=bindings)), bytes(expected))

    def test_analog_triggers_and_remapped_bottom_switches_are_independent(self):
        bindings = list(DEFAULT_MAPPING[2:18])
        bindings[14:16] = [1, 2]  # Bottom switches become A/B.
        bindings[4:6] = [16, 17]  # Bumpers provide full trigger overrides.
        config = configuration(bindings=bindings)
        self.update({TRIGGERS: bytes([64, 192]), BUTTONS: buttons(3)})
        result = self.report(config)
        self.assertEqual(struct.unpack_from("<2H", result, 8), (257, 771))
        self.assertEqual(struct.unpack_from("<H", result, 13)[0], 3)
        for raw, triggers in [(8, (1023, 771)), (4, (257, 1023)), (12, (1023, 1023)),
                              (0, (257, 771))]:
            self.update({BUTTONS: buttons(raw)})
            result = self.report(config)
            self.assertEqual(struct.unpack_from("<2H", result, 8), triggers)
            self.assertEqual(result[13:16], bytes(3))

    def test_direction_remaps_cancel_opposites_and_merge_with_pad(self):
        bindings = [12, 13, 14, 15] + [0] * 12
        config = configuration(bindings=bindings)
        expected_hats = [0, 1, 5, 0, 7, 8, 6, 7, 3, 2, 4, 3, 0, 1, 5, 0]
        for bits, expected in enumerate(expected_hats):
            self.update({BUTTONS: buttons(sum(1 << SOURCE_BITS[i] for i in range(4) if bits & (1 << i)))})
            self.assertEqual(self.report(config)[12], expected)
        self.update({BUTTONS: buttons(LEFT_TOUCH | LEFT_CLICK | 0x20), LEFTPAD: axes(20000, 20000)})
        self.assertEqual(self.report(config)[12], 3, "D-down cancels pad up, retaining right")
        self.update({BUTTONS: buttons(LEFT_TOUCH | LEFT_CLICK | 0x20 | 0x40)})
        self.assertEqual(self.report(config)[12], 0, "both opposing axes cancel")

    def test_left_pad_click_requirement_and_touch_release(self):
        self.update({LEFTPAD: axes(-20000, 0)})
        for raw, expected in [(LEFT_TOUCH, 0), (LEFT_TOUCH | LEFT_CLICK, 7),
                              (LEFT_TOUCH, 0), (LEFT_CLICK, 0), (0, 0)]:
            self.update({BUTTONS: buttons(raw)})
            self.assertEqual(self.report()[12], expected)
        config = configuration(flags=0x60)
        for raw, expected in [(LEFT_TOUCH, 7), (LEFT_TOUCH | LEFT_CLICK, 7),
                              (LEFT_CLICK, 0), (0, 0)]:
            self.update({BUTTONS: buttons(raw)})
            self.assertEqual(self.report(config)[12], expected)
        # A bound left click is a separate input; requiring a click does not consume it.
        bindings = list(DEFAULT_MAPPING[2:18])
        bindings[12] = 1
        self.update({BUTTONS: buttons(LEFT_TOUCH | LEFT_CLICK)})
        result = self.report(configuration(bindings=bindings))
        self.assertEqual((result[12], result[13]), (7, 1))

    def test_all_diagonal_angles_both_boundaries_in_every_quadrant(self):
        self.update({BUTTONS: buttons(LEFT_TOUCH | LEFT_CLICK)})
        for code, degrees in enumerate(ANGLES):
            config = configuration(flags=4 | code << 4)
            for sx, sy, horizontal, vertical, diagonal in [
                (1, 1, 3, 1, 2), (1, -1, 3, 5, 4),
                (-1, -1, 7, 5, 6), (-1, 1, 7, 1, 8),
            ]:
                if not degrees:
                    for x, y, expected in [(20000, 20000, horizontal), (19999, 20000, vertical),
                                           (20000, 19999, horizontal)]:
                        self.update({LEFTPAD: axes(sx * x, sy * y)})
                        self.assertEqual(self.report(config)[12], expected)
                    continue
                for inside in [-1, 1]:
                    # Oracle uses geometric angles rather than the firmware's ratio table.
                    angle = math.radians((90 - degrees) / 2 + inside)
                    x, y = int(20000 * math.cos(angle)), int(20000 * math.sin(angle))
                    with self.subTest(degrees=degrees, sx=sx, sy=sy, inside=inside):
                        self.update({LEFTPAD: axes(sx * x, sy * y)})
                        self.assertEqual(self.report(config)[12], horizontal if inside < 0 else diagonal)
                        self.update({LEFTPAD: axes(sx * y, sy * x)})
                        self.assertEqual(self.report(config)[12], vertical if inside < 0 else diagonal)

    def test_dpad_threshold_limits_and_integer_angular_boundary(self):
        self.update({BUTTONS: buttons(LEFT_TOUCH | LEFT_CLICK)})
        for threshold in [256, 8000, 30000]:
            config = configuration(threshold=threshold)
            for x, y, expected in [(threshold - 1, 0, 0), (threshold, 0, 3),
                                   (0, -threshold, 5), (-32768, -32768, 6)]:
                self.update({LEFTPAD: axes(x, y)})
                self.assertEqual(self.report(config)[12], expected)
        for x, y, expected in [(10000, 5773, 3), (10000, 5774, 2),
                               (5773, 10000, 1), (5774, 10000, 2)]:
            self.update({LEFTPAD: axes(x, y)})
            self.assertEqual(self.report()[12], expected)

    def test_deadzone_boundaries_linear_rescaling_and_monotonicity(self):
        self.update({BUTTONS: buttons(RIGHT_TOUCH)})
        for deadzone in [0, 1, 3000, 3500, 16000]:
            config = configuration(stick=deadzone, pad=deadzone)
            points = sorted(set([-32768, -32767, -deadzone - 1, -deadzone, -1, 0,
                                 1, deadzone, deadzone + 1, 32766, 32767] + list(range(-32768, 32768, 1021))))
            previous_x, previous_y = 0, 65536
            for raw in points:
                with self.subTest(deadzone=deadzone, raw=raw):
                    self.update({STICK: axes(raw, raw), RIGHTPAD: axes(raw, raw)})
                    result = struct.unpack_from("<4H", self.report(config))
                    expected_x, expected_y = scaled_axis(raw, deadzone), scaled_axis(raw, deadzone, True)
                    self.assertEqual(result, (expected_x, expected_y, expected_x, expected_y))
                    self.assertGreaterEqual(result[0], previous_x)
                    self.assertLessEqual(result[1], previous_y)
                    previous_x, previous_y = result[:2]
        # Distinct defaults and per-axis (rather than radial) deadzones.
        self.update({STICK: axes(3000, 3001), RIGHTPAD: axes(3500, 3501)})
        self.assertEqual(struct.unpack_from("<4H", self.report()), (32768, 32767, 32768, 32767))
        self.update({STICK: axes(3000, 3000), RIGHTPAD: axes(-3500, -3500)})
        self.assertEqual(self.report(), NEUTRAL)

    def test_startup_suppression_precedes_remapping_and_preserves_alias(self):
        bindings = list(DEFAULT_MAPPING[2:18])
        bindings[5] = 16  # RB would generate LT, even if analog LT is zero.
        bindings[8] = 12  # Steam would generate D-up.
        bindings[3] = 1   # Y would generate A.
        bindings[9] = 1   # Left back also generates A and must remain live.
        config = configuration(bindings=bindings)
        selected = RB | STEAM | Y
        self.arm.call("input_suppress_startup", self.state, selected | LEFT_BACK)
        self.update({BUTTONS: buttons(selected | LEFT_BACK), TRIGGERS: bytes([64, 192])})
        result = self.report(config)
        self.assertEqual(struct.unpack_from("<2H", result, 8), (257, 771))
        self.assertEqual((result[12], result[13]), (0, 1))
        self.update({BUTTONS: buttons(LEFT_BACK)})
        self.update({BUTTONS: buttons(selected | LEFT_BACK)})
        result = self.report(config)
        self.assertEqual(struct.unpack_from("<2H", result, 8), (1023, 771))
        self.assertEqual((result[12], result[13]), (1, 1))


if __name__ == "__main__":
    unittest.main()
