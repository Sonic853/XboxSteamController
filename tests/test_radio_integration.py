"""Execute shipped timer/UART/BLE dispatch chains beyond the unit-test stubs.

These are deterministic firmware integration probes, not physical RF evidence.
Only explicit peripheral/SoftDevice state and unrelated persistence setup is modeled.
"""
import json
from pathlib import Path
import struct
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from radio_harness import RadioBuild, RadioMachine, ROOT, HIDS, CONN, STACK
sys.path.insert(0, str(ROOT))
from tools.image import thumb_bl


class OriginalRadioMachine(RadioMachine):
    def __init__(self, build):
        super().__init__(build)
        self.uc.mem_map(0x40000000, 0x20000)
        self.uc.mem_map(0xE000E000, 0x2000)
        self.trace = []
        self.boundary_stubs = {}
        self.execute_original.update(("timer_create", "timer_start", "service", "event",
                                      "config", "gap", "dis", "hids", "advertising", "init",
                                      "uart_send"))
        # Restore original compressed initialized RAM, instead of all-zero fake globals.
        self.call(0x18258, 0x1EFEC, 0x20002000, 0x1E4)
        hooks = json.loads((ROOT / "config/radio-patches.json").read_text())["hooks"]
        for hook in hooks:
            if "symbol" not in hook:
                continue
            address = int(hook["address"], 0)
            target = self.symbols[hook["symbol"]]
            if hook["kind"] == "bl":
                self.write(address, thumb_bl(address, target))
            elif hook["kind"] == "literal":
                self.write32(address, target | bool(hook.get("thumb")))
        # Original receive-ring setup; UART byte delivery itself is external hardware.
        self.call(0x1AF8E, 0x2000293C, 0x2000295C + 0x100, 256)
        self.call(0x1AF8E, 0x2000294C, 0x20002B5C, 256)
        self.write32(0x2000205C, 0x19041)
        self.write(0x20002034, b"\x03")

    def _instruction(self, uc, address, size, user):
        if address in (0x18394, 0x1844C, 0x18744, 0x16CD8, 0x16DC8, 0x16D38, 0x16B2C,
                       0x16B4C, 0x177C4, 0x17812, 0x19040, 0x189F0, 0x18A0C,
                       0x1912C, 0x1BAF0, 0x1BA90, self.symbols["radio_input"],
                       self.symbols["radio_service"], self.symbols["config_init"], self.symbols["tick"]):
            self.trace.append((address, self.arguments()))
        if address in self.boundary_stubs:
            callback = self.boundary_stubs[address]
            result = callback(self.arguments()) if callable(callback) else callback
            uc.reg_write(self.regs[0], result)
            uc.reg_write(self.pc, uc.reg_read(self.lr))
            return
        super()._instruction(uc, address, size, user)

    def _svc(self, number, args):
        if number in (0x60, 0x70, 0x73, 0x77, 0x7A, 0x32, 0x33, 0x2E, 0x4C):
            # BLE setters; NVIC/radio-notification configuration has no external hardware here.
            return 0
        if number == 0x71:
            self.write(args[0], b"\x01\x01\x02\x03\x04\x05\xC6")
            return 0
        if number == 0x7B:
            self.write(args[0], struct.pack("<4H", 6, 12, 0, 400))
            return 0
        if number in (0x36, 0x37):  # SoftDevice critical-region enter/exit.
            if number == 0x36:
                self.write(args[0], b"\0")
            return 0
        return super()._svc(number, args)

    def original_timers(self):
        # Exact second app_timer_init call in BLE main at 0x1d388..0x1d392.
        # No scheduler trampoline: callbacks execute in the original RTC IRQ.
        self.run_block(0x1D388, 0x1D396)
        self.assert_success(self.uc.reg_read(self.regs[0]))

    def run_block(self, start, stop):
        self.uc.reg_write(self.xpsr, 0x01000000)
        self.uc.reg_write(self.sp, STACK - 16)  # original main's 16-byte frame
        self.uc.emu_start(start | 1, stop, count=300000)
        if self.uc.reg_read(self.pc) != stop:
            raise AssertionError(f"original block did not reach {stop:#x}")

    def main_iteration(self):
        self.run_block(0x1D404, 0x1D414)  # service -> mode check -> app_sched_execute -> wait

    @staticmethod
    def assert_success(value):
        if value:
            raise AssertionError(f"original firmware operation failed: {value:#x}")

    def start_patch_timer(self):
        self.original_timers()
        self.configure()
        # Run radio_init and the original top-level 18394 separately from the
        # persistence/device-manager setup, which requires the LPC and flash.
        self.boundary_stubs.update({0x1EA60: 0, 0x18B9C: 0, 0x18FA0: 0,
                                    0x1CAA0: self.empty_bond_whitelist})
        self.run_block(0x1D43A, 0x1D440)
        self.call(0x16B4C)  # Real SWI0 processes app_timer_start's operation queue.

    def empty_bond_whitelist(self, args):
        # Device-manager persistence was not initialized by the 18b9c boundary.
        # Advertising's real callback queries it on reconnect; model zero peers.
        self.write(args[1] + 4, b"\0")
        self.write(args[1] + 12, b"\0")
        return 0

    def irq_tick(self):
        # Model RTC1 advancing to its programmed compare, then execute both
        # original IRQ handlers. Never directly call patch tick().
        self.write32(0x40011504, self.u32(0x40011540))
        self.call(0x16B2C)
        self.call(0x16B4C)

    def receive_uart(self, payload):
        packet = b"\x51" + payload
        wire = bytearray(b"\x02")
        for value in packet:
            if value in (2, 3, 0x1F):
                wire.extend((0x1F, value ^ 0x20))
            else:
                wire.append(value)
        wire.append(3)
        for value in wire:
            self.assert_success(self.call(0x1AFB2, 0x2000293C, value))
        self.main_iteration()

    def ble_event(self, number, connection=0, body=None):
        data = bytearray(40)
        struct.pack_into("<HHH", data, 0, number, len(data), connection)
        if number == 0x10:
            struct.pack_into("<4H", data, 22, 6, 12, 0, 400)
        if body:
            data[6:6 + len(body)] = body
        self.call(0x187D8, self.alloc(data))
        self.call(0x16B4C)

    def softdevice_write(self, handle, payload, operation=6, offset=0):
        """Run the shipped SD event serializer, with only attribute lookup modeled.

        Internal operation 6/5 serializes WRITE_REQ/WRITE_CMD. The switch table
        at 0x38b2 routes both to 0x3942; operation 7 is a different procedure.
        This is not an
        ATT/RF decoder test: the input is a constructed internal stack record.
        """
        record = bytearray(24)
        struct.pack_into("<H", record, 2, self.u16(CONN))
        record[8] = operation
        struct.pack_into("<H", record, 12, handle)
        struct.pack_into("<HHI", record, 16, offset, len(payload), self.alloc(payload))
        event = self.alloc(bytes([0xA5]) * (32 + len(payload)))
        size = self.alloc(bytes(2))

        def context(args):
            if args[0] != handle:
                raise AssertionError("SoftDevice serializer looked up the wrong handle")
            # Three ble_uuid_t values, service/value handles, attribute type.
            # Deliberately make event+24 != WRITE_REQ to catch the old op offset.
            value = struct.pack("<HBxHBxHBxHHBx", 1, 3, 2, 3, 0, 0, 0x77, handle, 5)
            self.write(args[1], value)
            return 0

        self.boundary_stubs[0x33CC] = context
        try:
            self.assert_success(self.call(0x388E, self.alloc(record), event, size))
        finally:
            del self.boundary_stubs[0x33CC]
        if self.u16(size) != 32 + len(payload):
            raise AssertionError("unexpected serialized BLE event length")
        return event


class RadioIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.build = RadioBuild()
        cls.addClassCleanup(cls.build.close)

    def test_real_timer_create_start_swi_rtc_callback(self):
        m = OriginalRadioMachine(self.build)
        m.start_patch_timer()
        self.assertIn(0x16CD8, [address for address, _ in m.trace])
        self.assertIn(0x16DC8, [address for address, _ in m.trace])
        before = sum(address == m.symbols["tick"] for address, _ in m.trace)
        m.irq_tick()
        m.irq_tick()
        self.assertEqual(sum(address == m.symbols["tick"] for address, _ in m.trace), before + 2)

    def test_uart_ring_service_deframer_dispatch_preserves_unfragmented_delta(self):
        m = OriginalRadioMachine(self.build)
        m.start_patch_timer()
        payload = (struct.pack("<H", 0x0FF4) + b"\x80\0\0" + b"\x02\x03" + bytes(3) +
                   struct.pack("<6h", 3, 2, 31, 4, 5, 6) + bytes(12))
        m.receive_uart(payload)
        deliveries = [args for address, args in m.trace if address == m.symbols["radio_input"]]
        self.assertEqual(len(deliveries), 1)
        pointer, length = deliveries[0][:2]
        self.assertEqual(length, len(payload))
        self.assertEqual(m.read(pointer, length), payload)
        self.assertIn(0x19040, [address for address, _ in m.trace])
        self.assertIn(0x189F0, [address for address, _ in m.trace])

    def test_original_connect_hids_connection_and_main_send_chain(self):
        m = OriginalRadioMachine(self.build)
        m.start_patch_timer()
        m.ble_event(0x10, 0x0123)
        self.assertEqual(m.u16(CONN), 0x0123)
        self.assertEqual(m.u16(HIDS + 0x176), 0x0123)
        self.assertIn(0x1912C, [address for address, _ in m.trace])
        self.assertIn(0x1BAF0, [address for address, _ in m.trace])
        m.receive_uart(bytes.fromhex("b403800000") + bytes(14))
        m.irq_tick()
        m.main_iteration()
        self.assertEqual(len(m.notifications), 1)
        self.assertEqual(m.notifications[0][13:15], b"\x01\x00")

    def test_original_disconnect_reconnect_does_not_leave_hids_handle_stale(self):
        m = OriginalRadioMachine(self.build)
        m.start_patch_timer()
        m.ble_event(0x10, 0x123)
        m.receive_uart(bytes.fromhex("b403800000") + bytes(14))
        m.irq_tick()
        m.main_iteration()
        self.assertEqual(len(m.notifications), 1)
        m.ble_event(0x11, 0x123, b"\x08")
        self.assertEqual((m.u16(CONN), m.u16(HIDS + 0x176)), (0xFFFF, 0xFFFF))
        m.irq_tick()
        m.main_iteration()
        self.assertEqual(len(m.notifications), 1)
        m.ble_event(0x10, 0x234)
        self.assertEqual((m.u16(CONN), m.u16(HIDS + 0x176)), (0x234, 0x234))
        m.irq_tick()
        m.main_iteration()
        self.assertEqual(len(m.notifications), 1)  # Original event hook invalidated cached input.
        m.receive_uart(bytes.fromhex("b403200000") + bytes(14))
        m.irq_tick()
        m.main_iteration()
        self.assertEqual(len(m.notifications), 2)
        self.assertEqual(m.notifications[-1][13:15], b"\x02\x00")

    def test_original_main_chain_exposes_incomplete_snapshot_as_no_hvx(self):
        m = OriginalRadioMachine(self.build)
        m.start_patch_timer()
        m.ble_event(0x10, 0x123)
        # A syntactically valid buttons-only stream still cannot satisfy the
        # patch's 0x3b0 initial snapshot gate, regardless of time or button edges.
        for buttons in (0x80, 0x20, 0x40):
            m.receive_uart(bytes.fromhex("1400") + bytes([buttons, 0, 0]))
            m.irq_tick()
            m.main_iteration()
        self.assertEqual(m.notifications, [])
        # Remaining flags are triggers/stick/leftpad/rightpad, not fabricated ticks.
        m.receive_uart(struct.pack("<H", 0x03A4) + bytes(14))
        m.irq_tick()
        m.main_iteration()
        self.assertEqual(len(m.notifications), 1)
        self.assertEqual(m.notifications[0][13:15], b"\x08\0")

    def test_real_event_and_uart_chain_waits_for_actual_security_and_cccd_values(self):
        m = OriginalRadioMachine(self.build)
        m.start_patch_timer()
        m.ble_event(0x10, 0x123)
        m.sec = b"\x11\0"
        m.cccd = 0
        m.receive_uart(bytes.fromhex("b403800000") + bytes(14))
        m.ble_event(0x17, 0x123)  # Successful auth-status event is insufficient by itself.
        m.irq_tick()
        m.main_iteration()
        self.assertEqual(m.notifications, [])
        m.sec = b"\x21\x10"  # External SoftDevice security state now reports encrypted.
        m.ble_event(0x18, 0x123, m.sec)
        m.irq_tick()
        m.main_iteration()
        self.assertEqual(m.notifications, [])
        m.cccd = 1  # External SoftDevice system attributes restored, no write callback.
        m.cccd_error = 0x3001
        m.irq_tick()
        m.main_iteration()
        self.assertEqual(m.notifications, [])
        m.cccd_error = 0
        m.irq_tick()
        m.main_iteration()
        self.assertEqual(len(m.notifications), 1)

    def test_shipped_softdevice_write_layout_and_config_service_without_tick(self):
        m = OriginalRadioMachine(self.build)
        m.start_patch_timer()
        m.ble_event(0x10, 0x123)
        request_handle = next(c["handles"][0] for c in m.characteristics if c["uuid"] == 2)
        response_handle = next(c["handles"][0] for c in m.characteristics if c["uuid"] == 3)
        request = bytes([0x10, 0x71, 0, 0]) + bytes(16)
        event = m.softdevice_write(request_handle, request)
        raw = m.read(event, 52)
        self.assertEqual(struct.unpack_from("<HHHHB", raw), (0x50, 52, 0x123, request_handle, 1))
        self.assertNotEqual(raw[24], 1)
        self.assertEqual(struct.unpack_from("<HH", raw, 28), (0, 20))
        self.assertEqual(raw[32:], request)
        m.call(0x187D8, event)
        m.main_iteration()  # No RTC callback: diagnostic requests must still complete.
        self.assertIn(response_handle, m.values,
                      "valid shipped WRITE_REQ produced no config response; check event op at +8")
        response = m.values[response_handle]
        self.assertEqual(response[:4], bytes([0x10, 0x71, 0, 0]))
        self.assertEqual(response[4:8], b"XSC2")
        self.assertEqual(response[12:16], bytes(4))
        for operation, offset in ((5, 0), (6, 1)):
            with self.subTest(operation=operation, offset=offset):
                event = m.softdevice_write(request_handle, bytes([0x10, 0x72, 0, 0]) + bytes(16),
                                           operation, offset)
                self.assertEqual(m.read(event + 8, 1), bytes([1 if operation == 6 else 2]))
                m.call(0x187D8, event)
                m.main_iteration()
                self.assertEqual(m.values[response_handle], response)

    def test_shipped_uuid_allocator_reuses_base_and_has_ten_vendor_slots(self):
        m = OriginalRadioMachine(self.build)
        # Original 0x17ec..0x17f0 passes 11 total slots, including standard BLE.
        table = m.alloc(bytes(11 * 16))
        self.assertEqual(m.call(0x2BF0, 11, table), 0)
        output = m.alloc(bytes(1))
        valve_base = m.read(0x1EE39, 16)
        for _ in range(3):
            self.assertEqual(m.call(0x2C62, m.alloc(valve_base), output), 0)
            self.assertEqual(m.read(output, 1), b"\x02")
        # UUID bytes 12/13 are the short service/characteristic id, not a new base.
        variant = bytearray(valve_base)
        variant[12:14] = b"\x02\x01"
        self.assertEqual(m.call(0x2C62, m.alloc(variant), output), 0)
        self.assertEqual(m.read(output, 1), b"\x02")
        custom = bytes.fromhex("4d43656c4258448f534c152000003085")
        self.assertEqual(m.call(0x2C62, m.alloc(custom), output), 0)
        self.assertEqual(m.read(output, 1), b"\x03")
        for index in range(8):
            base = bytearray(custom)
            base[0] = index
            self.assertEqual(m.call(0x2C62, m.alloc(base), output), 0)
            self.assertEqual(m.read(output, 1), bytes([index + 4]))
        self.assertEqual(m.call(0x2C62, m.alloc(bytes(16)), output), 4)  # NRF_ERROR_NO_MEM.

    def test_config_registration_precedes_deferred_original_advertising_start(self):
        m = OriginalRadioMachine(self.build)
        m.start_patch_timer()
        addresses = [address for address, _ in m.trace]
        self.assertLess(addresses.index(0x1844C), addresses.index(m.symbols["config_init"]))
        self.assertEqual(m.uuid_bases[:3], [m.read(0x1EE39, 16)] * 3)
        self.assertEqual(len(m.uuid_bases), 4)
        self.assertEqual(len(set(m.uuid_bases)), 2)
        request, response = m.characteristics[-2:]
        for item in (request, response):
            self.assertEqual((item["length"], item["maximum"]), (20, 20))
            self.assertIsNone(item["cccd_permission"])
        self.assertEqual((request["uuid"], request["properties"], request["permission"]),
                         (2, 8, b"\0\x21\x02"))
        self.assertEqual((response["uuid"], response["properties"], response["permission"]),
                         (3, 2, b"\x21\0\x02"))
        self.assertNotIn(0x73, [number for number, _ in m.svcs])
        # Original main waits for this hardware/stack-ready bit before init.
        # Model it and the deferred advertise request, then run actual service.
        m.write(0x200020F8, bytes([m.read(0x200020F8, 1)[0] | 4]))
        m.write(0x20002039, b"\x01")
        m.main_iteration()
        self.assertEqual(sum(number == 0x73 for number, _ in m.svcs), 1)


if __name__ == "__main__":
    unittest.main()
