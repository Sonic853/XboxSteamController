"""Bonded CCCD lifecycle through shipped device-manager/pstorage instructions.

The opaque sys-attribute blob is supplied by a modeled S110 boundary. It is not
an implementation of its private wire format. The original load/restore/save
functions, dirty-range handling and patched event/service code execute on ARM.
"""
import struct
import unittest
import json

from test_radio_integration import OriginalRadioMachine
from radio_harness import RadioBuild, HIDS, CONN, ROOT


class BondRadioMachine(OriginalRadioMachine):
    PEER = b"\x00ABCDEF"

    @staticmethod
    def sysattrs(value):
        return b"S110-model:" + struct.pack('<H', value)

    @classmethod
    def initial_store(cls):
        data = bytearray(b'\xff' * 128)
        data[:16] = bytes(range(16))  # Synthetic IRK, never a live device key.
        data[16:23] = cls.PEER
        data[24:28] = b'\x34\x12\xfc\x00'
        data[28:56] = bytes(28)
        data[56:104] = struct.pack('<II', 3, len(cls.sysattrs(0))) + cls.sysattrs(0) + bytes(27)
        return data

    def __init__(self, build, persisted=None):
        self.persisted = bytearray(persisted if persisted is not None else self.initial_store())
        self.sysattr_get_error = 0
        self.sysattr_sets = []
        self.flushes = []
        super().__init__(build)
        self.start_patch_timer()
        # pstorage's asynchronous LPC read has completed with this 128-byte image.
        self.write(0x20002CE0, self.persisted)
        self.write(0x200020F8, b'\x04')
        self.write16(0x200020F0, 0xFFFF)
        self.write16(0x200020F2, 0)
        self.write(0x200020F4, b'\x00')
        # Execute original device-manager init/register; restore its real callback.
        del self.boundary_stubs[0x18B9C]
        self.call(0x18B9C, 0)
        self.boundary_stubs[0x18788] = self.lpc_write
        # Original pstorage debounce timer (created by 1e47c in production).
        self.call(0x16CD8, self.alloc(struct.pack('<I', 0x20002CC0)), 0, 0x1BEC1)
        self.cccd = 0
        self.ble_event(0x10, 0, self.PEER)
        self.ble_event(0x18, 0, b'\x21\x10')

    def _svc(self, number, args):
        r0, r1, r2, r3 = args
        if number == 0xA9:
            blob = self.read(r1, r2) if r1 else None
            self.sysattr_sets.append((r0, blob, r3))
            if blob is None:
                self.cccd = 0
            else:
                if blob not in (self.sysattrs(0), self.sysattrs(1)):
                    raise AssertionError(f'Unexpected opaque sysattrs: {blob.hex()}')
                self.cccd = int(blob == self.sysattrs(1))
            return 0
        if number == 0xAA:
            if r0 != self.u16(CONN) or r3 != 3:
                raise AssertionError('Snapshot must use current link and SYS|USR attributes')
            if self.sysattr_get_error:
                return self.sysattr_get_error
            blob = self.sysattrs(self.cccd)
            if self.u16(r2) < len(blob):
                raise AssertionError('Snapshot buffer is too small')
            self.write(r1, blob)
            self.write16(r2, len(blob))
            return 0
        return super()._svc(number, args)

    def lpc_write(self, args):
        offset, pointer, length = args[:3]
        if not 0 < length <= 128 - offset:
            raise AssertionError('Original pstorage emitted an invalid range')
        data = self.read(pointer, length)
        self.flushes.append((offset, data))
        self.persisted[offset:offset + length] = data
        return 0

    def flush(self):
        # Fire the shipped debounce callback, not a copy of its range algorithm.
        self.call(0x1BEC0, 0)

    def subscribe(self, value):
        self.cccd = value  # S110 applies the ATT write before dispatching WRITE.
        event = self.softdevice_write(self.u16(HIDS + 24), struct.pack('<H', value))
        self.call(0x187D8, event)

    def service(self):
        self.irq_tick()
        self.main_iteration()

    def diagnostics(self):
        pointer = self.alloc(bytes(16))
        self.call('radio_diagnostics', 8, pointer)
        return struct.unpack('<IIIHBB', self.read(pointer, 16))


class RadioBondTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.build = RadioBuild()
        cls.addClassCleanup(cls.build.close)

    def test_original_pairing_snapshot_precedes_subscription_and_cold_boot_loses_it(self):
        m = BondRadioMachine(self.build)
        # Original auth-complete path stores keys and sysattrs while CCCD is zero.
        m.call(0x1C140, m.alloc(b'\x00\x00\x00\xff'), 1)
        m.flush()
        before = bytes(m.persisted)
        m.subscribe(1)
        # No service tick: observe original event dispatch without the new saver.
        self.assertEqual(m.cccd, 1)
        self.assertEqual(bytes(m.persisted), before)
        self.assertEqual(m.read(0x20002CE0 + 56, 48), before[56:104])
        cold = BondRadioMachine(self.build, before)
        self.assertEqual(cold.cccd, 0)
        self.assertIn((0, m.sysattrs(0), 3), cold.sysattr_sets)

    def test_subscription_is_saved_before_power_off_and_original_stack_restores_it(self):
        m = BondRadioMachine(self.build)
        m.subscribe(1)
        m.service()
        self.assertEqual(m.diagnostics(), (1, 1, 0, len(m.sysattrs(1)), 0, 0x8a))
        self.assertEqual(m.flushes, [])  # Original half-second debounce still owns delivery.
        m.flush()
        self.assertEqual(m.flushes, [(56, m.read(0x20002CE0 + 56, 48))])
        self.assertEqual(m.persisted[:56], m.initial_store()[:56])
        self.assertEqual(m.persisted[104:], m.initial_store()[104:])
        cold = BondRadioMachine(self.build, m.persisted)
        self.assertEqual(cold.cccd, 1)
        self.assertIn((0, m.sysattrs(1), 3), cold.sysattr_sets)
        # Input works after original bonded restore, without a new CCCD WRITE.
        cold.receive_uart(bytes.fromhex('b403800000') + bytes(14))
        cold.service()
        self.assertTrue(cold.notifications)
        self.assertEqual(cold.diagnostics()[:2], (0, 0))

    def test_explicit_disable_and_repeated_write_preserve_real_client_choice(self):
        m = BondRadioMachine(self.build)
        m.subscribe(1)
        m.service()
        m.flush()
        m.subscribe(1)
        m.service()
        self.assertEqual(m.diagnostics()[:2], (2, 1))
        m.subscribe(0)
        m.service()
        self.assertEqual(m.diagnostics()[:2], (3, 2))
        m.flush()
        cold = BondRadioMachine(self.build, m.persisted)
        self.assertEqual(cold.cccd, 0)
        cold.receive_uart(bytes.fromhex('b403800000') + bytes(14))
        cold.service()
        self.assertEqual(cold.notifications, [])

    def test_failed_snapshot_retries_without_overwriting_valid_bond(self):
        m = BondRadioMachine(self.build)
        m.subscribe(1)
        m.sysattr_get_error = 8
        m.service()
        self.assertEqual(m.diagnostics()[:3], (1, 0, 8))
        self.assertEqual(m.diagnostics()[4], 1)
        self.assertEqual(m.read(0x20002CE0, 128), bytes(m.initial_store()))
        m.sysattr_get_error = 0
        m.service()
        self.assertEqual(m.diagnostics()[:2], (1, 0))  # Backoff is bounded, not a busy loop.
        for _ in range(50):
            m.service()
        self.assertEqual(m.diagnostics()[:3], (2, 1, 0))

    def test_only_current_encrypted_bond_can_save_and_disconnect_cancels_pending(self):
        for field, value in ((0x20002f1f, b'\xff'), (0x20002f1c, b'\x01\x00'),
                             (0x20002f1e, b'\xca'), (0x20002ec6, b'\xff')):
            with self.subTest(field=hex(field)):
                m = BondRadioMachine(self.build)
                m.subscribe(1)
                m.write(field, value)
                m.service()
                self.assertEqual(m.diagnostics()[:2], (0, 0))
        m = BondRadioMachine(self.build)
        m.subscribe(1)
        m.sec = b'\x11\x00'
        m.service()
        self.assertEqual(m.diagnostics()[:2], (0, 0))
        # Isolate patch cancellation; actual DM disconnect also has its own save path.
        m.call('radio_event', m.alloc(struct.pack('<HHH', 0x11, 6, 0)))
        self.assertEqual(m.diagnostics()[4], 0)

    def test_unsubscribed_bond_is_never_enabled_by_the_saver(self):
        m = BondRadioMachine(self.build)
        for _ in range(60):
            m.service()
        self.assertEqual(m.diagnostics()[:2], (0, 0))
        self.assertEqual(m.cccd, 0)
        self.assertEqual(m.persisted, m.initial_store())

    def test_staged_slice_survives_real_lpc_journal_write_and_cold_boot(self):
        from test_host import Machine, compile_host, firmware, patched_image
        symbols, payload, _ = compile_host()
        base = firmware('vcf_wired_controller_d0g_5b0f21bd.bin')
        config = json.loads((ROOT / 'config/host-patches.json').read_text())
        image = patched_image(base, payload, symbols, config)
        host = Machine(image, symbols)
        host.host_stubs()
        host.xbox()
        host.seed(BondRadioMachine.initial_store())
        mapping = host.mapping_read()
        radio = BondRadioMachine(self.build, host.store_read())
        radio.subscribe(1)
        radio.service()
        radio.flush()
        offset, data = radio.flushes[-1]
        # Exact packet emitted by original 18788, delivered to the real deferred
        # host callback. Only UART transport and ROM flash hardware are modeled.
        packet = b'\x5e' + struct.pack('<II', offset, len(data)) + data
        host.uc.mem_write(0x10001264, packet)
        host.call('host_config_write', 0)
        self.assertEqual(host.word(0x10001FD0 + 8), 0)
        self.assertTrue(host.iap_calls)
        reboot = Machine(image, symbols)
        reboot.host_stubs()
        reboot.uc.mem_write(0x1E000, host.read(0x1E000, 8192))
        self.assertEqual(reboot.mapping_read(), mapping)
        cold = BondRadioMachine(self.build, reboot.store_read())
        self.assertEqual(cold.cccd, 1)


if __name__ == '__main__':
    unittest.main()
