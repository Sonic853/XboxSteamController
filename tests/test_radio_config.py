"""ARM execution of the real BLE configuration protocol and UART persistence boundary."""
import struct
import unittest
import zlib
from radio_harness import RadioBuild, RadioMachine, HIDS

MAGIC = 0x58434D31
DEFAULT = bytes.fromhex("01640102030405060708090b0a0a000b1011b80bac0d401f")


class RadioConfigTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.build = RadioBuild()
        cls.addClassCleanup(cls.build.close)

    def setUp(self):
        self.m = RadioMachine(self.build)
        self.m.xbox()
        self.request_handle = next(c["handles"][0] for c in self.m.characteristics if c["uuid"] == 2)
        self.response_handle = next(c["handles"][0] for c in self.m.characteristics if c["uuid"] == 3)
        self.seq = 0

    def service(self):
        self.m.call("radio_service")

    def reply(self, config=DEFAULT, status=0, corrupt=False):
        token = zlib.crc32(config) ^ int(corrupt)
        packet = b"\x5d" + struct.pack("<II", MAGIC, status) + config + struct.pack("<I", token)
        self.m.call("radio_config", self.m.alloc(packet), len(packet))
        self.service()

    def request(self, op, arg=0, payload=b"", *, offset=0, operation=1):
        self.seq += 1
        frame = bytes([op, self.seq, arg, 0]) + payload.ljust(16, b"\0")
        self.assertEqual(len(frame), 20)
        event = bytearray(52)
        struct.pack_into("<HHHH", event, 0, 0x50, 52, 0x123, self.request_handle)
        event[8] = operation
        struct.pack_into("<HH", event, 28, offset, 20)
        event[32:] = frame
        self.m.call("radio_event", self.m.alloc(event))
        self.service()
        return self.m.values.get(self.response_handle)

    def stage(self, config, token=None):
        if token is None:
            token = zlib.crc32(DEFAULT)
        self.commit_payload = struct.pack("<II", zlib.crc32(config), token)
        self.assertEqual(self.request(2, 0, struct.pack("<I", token) + config[:12])[2], 0)
        self.assertEqual(self.request(2, 1, config[12:])[2], 0)

    def commit(self):
        return self.request(3, payload=self.commit_payload)

    def test_gatt_identity_permissions_and_fixed_mtu(self):
        pointer = self.m.call("config_mapping")
        self.assertEqual(self.m.read(pointer, 24), DEFAULT)
        self.assertEqual(self.m.uuid_bases, [bytes.fromhex("4d43656c4258448f534c152000003085")])
        request, response = self.m.characteristics
        self.assertEqual((request["uuid"], response["uuid"]), (2, 3))
        self.assertEqual((request["properties"], response["properties"]), (8, 2))
        self.assertEqual(request["permission"], bytes.fromhex("002102"))
        self.assertEqual(response["permission"], bytes.fromhex("210002"))
        for c in (request, response):
            self.assertEqual((c["length"], c["maximum"]), (20, 20))
            self.assertIsNone(c["cccd_permission"])

    def test_boot_requests_mapping_without_touching_original_callback(self):
        self.m.write32(0x2000204C, 0xDEADBEEF)
        self.service()
        self.assertEqual(self.m.uart_packets, [b"\x5d" + struct.pack("<II", MAGIC, 24)])
        self.assertEqual(self.m.u32(0x2000204C), 0xDEADBEEF)
        self.assertEqual(self.request(1)[2], 6)
        self.reply(corrupt=True)
        self.assertEqual(self.request(1)[2], 6)
        self.reply()
        self.assertEqual(self.request(1)[4:], DEFAULT[:16])
        self.assertEqual(self.request(1, 1)[4:16], DEFAULT[16:] + struct.pack("<I", zlib.crc32(DEFAULT)))

    def test_commit_waits_for_flash_ack_and_applies_only_acknowledged_mapping(self):
        self.service()
        self.reply()
        changed = bytearray(DEFAULT)
        changed[2] = 2  # A -> B
        changed = bytes(changed)
        self.stage(changed)
        response = self.commit()
        self.assertEqual(response[2], 4)
        self.assertEqual(self.m.uart_packets[-1], b"\x5e" + struct.pack("<III", MAGIC, 28, zlib.crc32(DEFAULT)) + changed)
        self.m.full_input(0x80)
        self.m.cycle()
        self.assertEqual(self.m.notifications[-1][13], 1)
        self.reply()  # Stale read response must not claim commit success.
        self.assertEqual(self.m.values[self.response_handle][2], 4)
        self.reply(changed)
        saved = self.m.values[self.response_handle]
        self.assertEqual(saved[:4], response[:2] + b"\0\0")
        self.assertEqual(saved[4:12], struct.pack("<II", zlib.crc32(changed), 0))
        self.m.cycle()
        self.assertEqual(self.m.notifications[-1][13], 2)

    def test_conflict_busy_iap_error_and_timeout_never_claim_persistence(self):
        self.service()
        self.reply()
        self.stage(DEFAULT, token=123)
        self.assertEqual(self.commit()[2], 2)
        self.assertEqual(len(self.m.uart_packets), 1)
        for error, expected in ((2, 2), (3, 3), (0x10004, 7)):
            self.stage(DEFAULT)
            self.assertEqual(self.commit()[2], 4)
            self.reply(status=error)
            response = self.m.values[self.response_handle]
            self.assertEqual(response[2], expected)
            self.assertEqual(struct.unpack_from("<I", response, 8)[0], error)
        self.stage(DEFAULT)
        self.commit()
        for _ in range(500):
            self.m.call(self.m.timer_callback, 0)
        self.service()
        self.assertEqual(self.m.values[self.response_handle][2], 5)
        self.assertEqual(self.request(1)[2], 6)
        self.assertEqual(sum(p[0] == 0x5E for p in self.m.uart_packets), 4)  # No automatic write retry.

    def test_staging_rejects_invalid_mapping_and_disconnect_discards_partial(self):
        self.service()
        self.reply()
        self.assertEqual(self.request(2, 1, DEFAULT[12:])[2], 1)
        self.request(2, 0, struct.pack("<I", zlib.crc32(DEFAULT)) + DEFAULT[:12])
        self.m.event(0x11)
        self.assertEqual(self.request(2, 1, DEFAULT[12:])[2], 1)
        bad = bytes([2]) + DEFAULT[1:]
        self.request(2, 0, struct.pack("<I", 0) + bad[:12])
        self.assertEqual(self.request(2, 1, bad[12:])[2], 1)
        self.assertEqual(self.request(3)[2], 1)
        old = self.m.values[self.response_handle]
        self.assertEqual(self.request(1, offset=1), old)
        self.assertEqual(self.request(1, operation=2), old)

    def test_commit_binds_staged_bytes_and_expected_token_across_clients(self):
        self.service()
        self.reply()
        self.stage(DEFAULT)
        intent = self.commit_payload
        other = bytearray(DEFAULT)
        other[2] = 3
        self.stage(bytes(other))  # A second application replaced the staged bytes.
        self.assertEqual(self.request(3, payload=intent)[2], 2)
        self.assertEqual(len(self.m.uart_packets), 1)
        self.stage(DEFAULT)
        wrong_expected = struct.pack("<II", zlib.crc32(DEFAULT), 0)
        self.assertEqual(self.request(3, payload=wrong_expected)[2], 2)
        self.assertEqual(len(self.m.uart_packets), 1)

    def test_diagnostics_identify_raw_parse_and_send_gates(self):
        identity = self.request(0x10)
        self.assertEqual(identity[4:11], b"XSC2\x05\0\x01")
        self.assertEqual(self.request(0x10, 8)[4:], bytes(16))
        self.m.input(b"bad")
        rx = self.request(0x10, 1)
        self.assertEqual(struct.unpack_from("<III", rx, 4), (2, 1, 1))
        self.m.sec_error = 8
        self.m.cycle()
        diag = self.request(0x10, 3)
        self.assertEqual(struct.unpack_from("<I", diag, 4)[0], 8)
        self.assertEqual(diag[14], 4)
        self.m.sec_error = 0
        self.m.cycle()
        tx = self.request(0x10, 2)
        self.assertEqual(struct.unpack_from("<III", tx, 4), (1, 1, 0))

    def test_subscription_trace_distinguishes_client_writes_and_later_clear(self):
        m = self.m
        m.cccd = 0
        m.cycle()
        initial = self.request(0x10, 6)
        self.assertEqual(struct.unpack_from("<I6H", initial, 4), (0, 0, 0, 0x123, 0x44, 0x46, 2))
        event = bytearray(34)
        struct.pack_into("<4H", event, 0, 0x50, 34, 0x123, 0x46)
        event[8] = 1
        struct.pack_into("<3H", event, 28, 0, 2, 1)
        m.call("radio_event", m.alloc(event))
        # The observer must not change the stack's CCCD or bypass the gate.
        m.cycle()
        self.assertEqual(m.notifications, [])
        m.cccd = 1
        m.cycle()
        self.assertEqual(len(m.notifications), 1)
        m.event(0x52)
        m.cccd = 0  # Model a subsequent system-attribute reset, with no client write.
        m.cycle()
        observed = self.request(0x10, 6)
        self.assertEqual(struct.unpack_from("<I6H", observed, 4), (1, 1, 1, 0x123, 0x44, 0x46, 2))
        trace = self.request(0x10, 7)
        self.assertEqual(struct.unpack_from("<HHIIHH", trace, 4), (1, 0, 1, 3, 1, 1))
        self.assertEqual(self.request(0x10, 3)[14], 7)
        self.assertEqual(len(m.notifications), 1)

    def test_subscription_trace_filters_events_and_records_explicit_disable(self):
        m = self.m
        event = bytearray(34)
        struct.pack_into("<4H", event, 0, 0x50, 34, 0x123, 0x46)
        event[8] = 2
        struct.pack_into("<3H", event, 28, 0, 2, 0)
        for field, bad_value in ((6, 0x45), (8, 3), (28, 1), (30, 1)):
            bad = bytearray(event)
            if field == 8:
                bad[field] = bad_value
            else:
                struct.pack_into("<H", bad, field, bad_value)
            m.call("radio_event", m.alloc(bad))
        self.assertEqual(struct.unpack_from("<I", self.request(0x10, 6), 4)[0], 0)
        m.call("radio_event", m.alloc(event))
        self.assertEqual(struct.unpack_from("<I", self.request(0x10, 6), 4)[0], 1)
        self.assertEqual(struct.unpack_from("<HH", self.request(0x10, 7), 4), (0, 1))


if __name__ == "__main__":
    unittest.main()
