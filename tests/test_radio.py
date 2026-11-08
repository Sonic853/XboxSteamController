"""Run compiled ARM and original SDK routines; no BLE device is accessed.

CI must set XSC_REQUIRE_EMULATION=1 after preparing pinned dependencies.
"""
from pathlib import Path
import json
import struct
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from radio_harness import RadioBuild, RadioMachine, ROOT, HIDS, STACK


NEUTRAL = bytes.fromhex("00800080008000800000000000000000")


class RadioAbiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.build = RadioBuild()
        cls.addClassCleanup(cls.build.close)

    def setUp(self):
        self.m = RadioMachine(self.build)

    def test_original_scatter_stack_and_hook_table_bindings(self):
        base = self.build.base
        self.assertEqual(struct.unpack_from("<I", base, 0x18000)[0], STACK)
        self.assertEqual(struct.unpack_from("<I", base, 0x180D0)[0], STACK)
        scatter = struct.unpack_from("<8I", base, 0x1EFCC)
        self.assertEqual(scatter, (0x1EFEC, 0x20002000, 0x1E4, 0x18258,
                                   0x1F014, 0x200021E4, 0x1C44, 0x19AA6))
        self.assertEqual(scatter[5] + scatter[6], STACK)
        config = json.loads((ROOT / "config/radio-patches.json").read_text())
        hooks = config["hooks"]
        self.assertEqual(len(hooks), 13)
        for hook in hooks:
            address = int(hook["address"], 0)
            if hook["kind"] == "bl":
                upper, lower = struct.unpack_from("<HH", base, address)
                self.assertEqual((upper & 0xF800, lower & 0xD000), (0xF000, 0xD000))
                sign = upper >> 10 & 1
                i1 = 1 ^ (lower >> 13 & 1) ^ sign
                i2 = 1 ^ (lower >> 11 & 1) ^ sign
                delta = (sign << 24 | i1 << 23 | i2 << 22 | (upper & 1023) << 12 |
                         (lower & 2047) << 1)
                if sign:
                    delta -= 1 << 25
                self.assertEqual(address + 4 + delta, int(hook["expected_target"], 0))
            else:
                self.assertEqual(struct.unpack_from("<I", base, address)[0],
                                 int(hook["expected"], 0))
            if "symbol" in hook:
                self.assertIn(hook["symbol"], self.build.symbols)
        advertising = next(h for h in hooks if h.get("symbol") == "radio_advertising")
        self.assertEqual(advertising["address"], "0x18300")

    def test_boot_clears_only_reserved_bss_before_original_main(self):
        start = self.build.symbols["__bss_start__"]
        end = self.build.symbols["__bss_end__"]
        self.assertEqual(start, STACK)
        self.assertLessEqual(end, 0x20004000)
        self.m.write(start, b"\xA5" * (end - start))
        self.m.write(end, b"\x6D" * (0x20004000 - end))
        self.m.call("radio_boot")
        self.assertEqual(self.m.read(start, end - start), bytes(end - start))
        self.assertEqual(self.m.read(end, 0x20004000 - end), b"\x6D" * (0x20004000 - end))
        self.assertEqual([name for name, _ in self.m.calls], ["main"])

    def test_profile_marker_is_exact_and_frozen_before_init(self):
        for bad in ({"marker": 0}, {"length": 136}, {"length": 138},
                    {"first": 0x5C}, {"index": 1}):
            with self.subTest(bad=bad):
                m = RadioMachine(self.build)
                m.call("radio_boot")
                pointer = m.configure(**bad)
                self.assertEqual(m.calls[-1][1][:2], (pointer, bad.get("length", 137)))
                m.call("radio_init", 1)
                m.configure()  # Too late to change a registered Steam GATT profile.
                p = m.alloc(b"steam")
                m.call("radio_input", p, 5)
                self.assertEqual(m.calls[-1][0], "input")
                self.assertEqual(m.calls[-1][1][:2], (p, 5))
                self.assertFalse(any(name.startswith("timer") for name, _ in m.calls))
        self.m.xbox()
        self.m.configure(marker=0)
        self.m.call("radio_input", self.m.alloc(b"\x04\x00"), 2)
        self.assertNotEqual(self.m.calls[-1][0], "input")

    def test_steam_wrappers_preserve_buffers_and_fifth_argument(self):
        m = self.m
        m.call("radio_boot")
        data = bytes(range(160))
        pointer = m.alloc(data)
        for wrapper, original in (("radio_input", "input"), ("radio_feature", "feature"),
                                  ("radio_status", "status")):
            m.call(wrapper, pointer, 137)
            self.assertEqual(m.calls[-1][0], original)
            self.assertEqual(m.calls[-1][1][:2], (pointer, 137))
            self.assertEqual(m.read(pointer, len(data)), data)
        m.return_codes.update(dis=0xAC, hids=0xAD, advertising=0xAE)
        self.assertEqual(m.call("radio_dis", pointer), 0xAC)
        self.assertEqual(m.call("radio_hids", HIDS, pointer), 0xAD)
        scan = m.alloc(data)
        args = (pointer, scan, 0x11223344, 0x55667789, 0x99AABBCC)
        self.assertEqual(m.call("radio_advertising", *args), 0xAE)
        self.assertEqual(m.calls[-1], ("advertising", args))
        self.assertEqual(m.read(pointer, len(data)), data)
        self.assertEqual(m.read(scan, len(data)), data)
        m.call("radio_gap")
        m.call("radio_service")
        self.assertEqual(m.svcs, [])

    def test_original_config_callback_uses_packet_length_not_profile_marker(self):
        m = self.m
        m.call("radio_boot")
        m.execute_original.add("config")
        m.write32(0x2000204C, 0x3EF01)
        pointer = m.configure()
        self.assertEqual(m.calls[-1][0], "config_sink")
        self.assertEqual(m.calls[-1][1][:3], (0, pointer + 9, 128))

    def test_timer_abi_and_original_service_always_runs(self):
        self.m.xbox()
        create = next(args for name, args in self.m.calls if name == "timer_create")
        start = next(args for name, args in self.m.calls if name == "timer_start")
        self.assertEqual(create[1], 1)  # Repeated, not single-shot.
        self.assertEqual(self.m.u32(create[0]), start[0])
        self.assertEqual(start[1:3], (328, 0))
        self.assertEqual(create[2] & ~1, self.build.symbols["tick"])
        self.m.call("radio_service")
        self.assertIn("service", [name for name, _ in self.m.calls])
        self.assertEqual(self.m.svcs, [])  # No tick yet.

    def test_gap_s110_name_and_appearance_registers(self):
        self.m.xbox()
        self.m.call("radio_gap")
        name, appearance = self.m.svcs
        self.assertEqual(name[0], 0x7C)
        permission, pointer, length, _ = name[1]
        # Permission is an automatic local; inspect its value via SVC snapshot below.
        self.assertTrue(permission < STACK)
        self.assertEqual(self.m.read(pointer, length), b"Xbox Wireless Controller")
        self.assertEqual(self.m.name_permission, b"\x11")
        self.assertEqual(appearance, (0x78, (0x03C4, 0, 0, 0)))

    def test_dis_natural_padding_and_original_wire_encoder(self):
        self.m.xbox()
        init = self.m.alloc(bytes(64))
        self.m.execute_original.add("dis")
        self.assertEqual(self.m.call("radio_dis", init), 0)
        pnp = self.m.u32(init + 56)
        self.assertEqual(pnp % 4, 0)
        self.assertEqual(self.m.read(pnp, 8), bytes.fromhex("02005e04130b0905"))
        pnp_char = [c for c in self.m.characteristics if c["uuid"] == 0x2A50]
        self.assertEqual(len(pnp_char), 1)
        self.assertEqual(pnp_char[0]["value"], bytes.fromhex("025e04130b0905"))

    def test_hids_72_byte_init_and_original_gatt_registration(self):
        m = self.m
        m.xbox()
        init_data = bytearray(72)
        init_data[44] = init_data[52] = 0x21
        init_data[60:72] = bytes([0x21]) * 12
        init = m.alloc(bytes(init_data) + b"BOUNDARY")
        m.execute_original.add("hids")
        self.assertEqual(m.call("radio_hids", HIDS, init), 0)
        self.assertEqual(m.read(init + 72, 8), b"BOUNDARY")
        self.assertEqual(m.read(init + 8, 3), b"\0\0\1")
        self.assertEqual(m.u32(init + 12) % 4, 0)
        self.assertEqual(m.u32(init + 20) % 4, 0)
        self.assertEqual(m.read(m.u32(init + 12), 8), bytes.fromhex("1000010121212100"))
        self.assertEqual(m.read(m.u32(init + 20), 8), bytes.fromhex("0800030200212100"))
        self.assertEqual(m.read(init + 24, 8), bytes(8))
        self.assertEqual(m.u16(init + 36), 283)
        self.assertEqual(m.read(init + 48, 4), bytes.fromhex("11010003"))
        reports = [c for c in m.characteristics if c["uuid"] == 0x2A4D]
        self.assertEqual([c["maximum"] for c in reports], [16, 8])
        self.assertEqual([c["properties"] for c in reports], [0x1A, 0x0E])
        self.assertEqual(reports[0]["permission"][:2], b"\x21\x21")
        self.assertEqual(reports[1]["permission"][:2], b"\x21\x21")
        self.assertEqual(reports[0]["cccd_permission"], b"\x11\x21")
        self.assertIsNone(reports[1]["cccd_permission"])
        self.assertEqual(m.u16(HIDS + 24), reports[0]["handles"][2])
        refs = [d["value"] for d in m.descriptors if d["uuid"] == 0x2908]
        self.assertEqual(refs, [b"\x01\x01", b"\x03\x02"])
        maps = [c for c in m.characteristics if c["uuid"] == 0x2A4B]
        self.assertEqual(len(maps), 1)
        self.assertEqual(maps[0]["value"], m.read(m.u32(init + 32), 283))
        self.assertFalse({0x2A22, 0x2A32, 0x2A33} & {c["uuid"] for c in m.characteristics})

    def test_advertising_layout_bounds_and_aapcs_fifth_argument(self):
        m = self.m
        m.xbox()
        advert = m.alloc(b"\xA5" * 56 + b"ADGUARD!")
        scan = m.alloc(b"\xA5" * 56 + b"SCGUARD!")
        args = (advert, scan, 0x11223344, 0x55667789, 0x99AABBCC)
        m.call("radio_advertising", *args)
        self.assertEqual(m.calls[-1], ("advertising", args))
        self.assertEqual(m.read(advert, 4), bytes([0, 0, 1, 5]))
        self.assertEqual(m.u16(advert + 16), 1)
        self.assertEqual(m.read(m.u32(advert + 20), 4), bytes.fromhex("12180100"))
        self.assertEqual(m.read(scan, 56), b"\x02" + bytes(55))
        self.assertEqual(m.read(advert + 56, 8), b"ADGUARD!")
        self.assertEqual(m.read(scan + 56, 8), b"SCGUARD!")
        self.assertLessEqual(3 + 4 + 4, 31)  # Flags + appearance + complete 16-bit UUID.
        self.assertLessEqual(2 + len(b"Xbox Wireless Controller"), 31)

    def test_original_advertising_encoder_produces_bounded_hid_and_full_name(self):
        m = self.m
        m.xbox()
        m.call("radio_gap")
        m.execute_original.add("advertising")
        advert, scan, modes = (m.alloc(bytes(size)) for size in (56, 56, 36))
        self.assertEqual(m.call("radio_advertising", advert, scan, modes, 0, 0), 0)
        self.assertEqual(m.advertisements, [(bytes.fromhex("0319c40302010503031218"),
                                           b"\x19\x09Xbox Wireless Controller")])

    def test_unencrypted_unsubscribed_disconnected_and_svc_errors_gate_hvx(self):
        cases = [dict(sec=b"\x11\x10"), dict(sec=b"\x22\x10"), dict(sec=b"\x21\x06"),
                 dict(sec_error=8), dict(cccd=0), dict(cccd=2), dict(cccd_error=0x3001)]
        for settings in cases:
            with self.subTest(settings=settings):
                m = RadioMachine(self.build)
                m.xbox()
                for key, value in settings.items():
                    setattr(m, key, value)
                m.cycle()
                self.assertEqual(m.notifications, [])
        self.m.xbox()
        self.m.connect(0xFFFF)
        self.m.cycle()
        self.assertEqual(self.m.svcs, [])

    def test_original_send_uses_correct_register_order_and_16_byte_value(self):
        self.m.xbox()
        self.m.cycle()
        self.assertEqual(self.m.notifications, [NEUTRAL])
        self.assertEqual([number for number, _ in self.m.svcs], [0x83, 0xA5, 0xA6])
        self.m.call("radio_service")
        self.assertEqual(len(self.m.notifications), 1)  # At most one attempt per tick.
        self.m.cycle()
        self.assertEqual(len(self.m.notifications), 1)  # Suppress unchanged success.

    def test_full_tx_busy_and_partial_length_retry_latest_state(self):
        for failure in (4, 17, 0x3004):
            with self.subTest(failure=failure):
                m = RadioMachine(self.build)
                m.xbox()
                m.hvx_results = [failure, 0]
                m.input(bytes.fromhex("1400800000"))  # A.
                m.cycle()
                self.assertEqual(m.notifications[-1][13:15], b"\x01\0")
                m.input(bytes.fromhex("1400200000"))  # Newest B supersedes A while busy.
                m.call("radio_service")
                self.assertEqual(len(m.notifications), 1)
                m.cycle()
                self.assertEqual(m.notifications[-1][13:15], b"\x02\0")
                m.cycle()
                self.assertEqual(len(m.notifications), 2)
        self.m.xbox()
        self.m.hvx_length = 15  # SDK converts successful short send to NRF_ERROR_DATA_SIZE.
        self.m.cycle()
        self.m.hvx_length = None
        self.m.cycle()
        self.assertEqual(self.m.notifications, [NEUTRAL, NEUTRAL])

    def test_bonded_cccd_restore_and_encryption_restore_resend_unchanged(self):
        self.m.xbox()
        self.m.cycle()
        for gate, blocked, restored in (("cccd", 0, 1), ("sec", b"\x11\x10", b"\x21\x10")):
            previous = len(self.m.notifications)
            setattr(self.m, gate, blocked)
            self.m.cycle()
            self.assertEqual(len(self.m.notifications), previous)
            setattr(self.m, gate, restored)
            self.m.cycle()  # No synthetic CCCD-write/security-complete callback needed.
            self.assertEqual(len(self.m.notifications), previous + 1)

    def test_original_hids_suspend_exit_and_boot_report_event_numbers(self):
        m = self.m
        m.xbox()
        m.write32(HIDS, self.build.symbols["hids_event"] | 1)
        m.write16(HIDS + 0x16E, 0x99)
        m.write16(HIDS + 10, 0x9A)
        for handle in (0x99, 0x9A):
            m.original_hids_write(handle, 0)
            before = len(m.notifications)
            m.cycle()
            self.assertEqual(len(m.notifications), before)
            m.original_hids_write(handle, 1)
            m.cycle()
            self.assertEqual(len(m.notifications), before + 1)

    def test_disconnect_and_connect_reset_input_suspend_and_report_cache(self):
        for number in (0x10, 0x11):
            with self.subTest(event=number):
                m = RadioMachine(self.build)
                m.xbox()
                m.input(bytes.fromhex("b400800000ffff3412ff7f"))
                m.cycle()
                self.assertNotEqual(m.notifications[-1], NEUTRAL)
                m.hids_event(0)
                m.hids_event(5)
                m.event(number)
                self.assertEqual(m.calls[-1][0], "event")
                before = len(m.notifications)
                m.cycle()
                self.assertEqual(len(m.notifications), before)  # Full snapshot required again.
                m.full_input()
                m.cycle()
                self.assertEqual(m.notifications[-1], NEUTRAL)

    def test_complete_snapshot_required_after_boot_and_every_reconnect(self):
        m = self.m
        m.xbox(seed=False)
        for event in (None, 0x11, 0x10):
            if event is not None:
                m.event(event)
            before = len(m.notifications)
            # Buttons/triggers/stick/leftpad still cannot publish without rightpad.
            for packet in ("1400000000", "24000000", "840000000000", "040100000000"):
                m.input(bytes.fromhex(packet))
                m.cycle()
                self.assertEqual(len(m.notifications), before)
            m.input(bytes.fromhex("040200000000"))
            m.cycle()
            self.assertEqual(len(m.notifications), before + 1)
            self.assertEqual(m.notifications[-1], NEUTRAL)

    def test_startup_suppression_survives_connect_until_all_keys_released(self):
        m = self.m
        m.xbox(seed=False)
        held = 0x002014  # RB + Steam + optional Y.
        m.full_input(held | 0x80)  # A is an unrelated button and remains visible.
        m.cycle()
        self.assertEqual(m.notifications[-1][13:15], b"\x01\x00")
        for event in (0x11, 0x10):
            m.event(event)
            m.full_input(held | 0x80)
            m.cycle()
            self.assertEqual(m.notifications[-1][13:15], b"\x01\x00")
        m.full_input(0x2000)  # Partial release must not retire the remaining Steam latch.
        m.cycle()
        self.assertEqual(m.notifications[-1], NEUTRAL)
        m.full_input(0)
        m.cycle()
        # Repeated marker after init must not re-arm the retired boot gesture.
        m.configure()
        m.event(0x11)
        m.event(0x10)
        m.full_input(held)
        m.cycle()
        self.assertEqual(m.notifications[-1][13:15], b"\x90\x10")

    def test_xbox_swallows_steam_feature_and_status_without_sdk_call(self):
        self.m.xbox()
        before = len(self.m.calls)
        pointer = self.m.alloc(b"\xFF" * 64)
        self.m.call("radio_feature", pointer, 64)
        self.m.call("radio_status", pointer, 64)
        self.assertEqual(len(self.m.calls), before)


if __name__ == "__main__":
    unittest.main()
