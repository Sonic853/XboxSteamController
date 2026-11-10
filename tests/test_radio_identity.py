"""Profile identity isolation through shipped S110 instructions.

Synthetic keys only. These probes do not model Windows device association or RF.
"""
import struct
import unittest

from radio_harness import RadioBuild, IDENTITY_VECTORS
from test_radio_bond import BondRadioMachine
from test_radio_integration import OriginalRadioMachine


class IdentityRadioMachine(OriginalRadioMachine):
    """Real S110 option handlers/ECB driver; AES silicon is a vector boundary."""
    NATIVE_SVCS = {0x68: 0x1F3C, 0x69: 0x1FEE, 0x4D: 0x150C2}

    def __init__(self, build, default=bytes(range(16)), interval=900):
        self.default_irk, self.interval = default, interval
        self.svc_errors = {}
        self.ecb_calls = []
        super().__init__(build)
        from unicorn import UC_HOOK_MEM_WRITE
        self.uc.hook_add(UC_HOOK_MEM_WRITE, self._ecb_task,
                         begin=0x4000E000, end=0x4000E003)
        self.reset_identity()

    def reset_identity(self):
        self.write32(0x20000028, 0x20002000)  # S110 application RAM boundary.
        self.write(0x20000B40, self.default_irk)
        self.write(0x20000B50, b'\x01')  # Default IRK derivation has completed.
        self.write16(0x20000A90, self.interval)

    def _instruction(self, uc, address, size, user):
        if address == 0x16426:
            raise AssertionError(f'SoftDevice assert: {self.arguments()}')
        raw = self.read(address, 2)
        if raw[1] == 0xDF and raw[0] in self.NATIVE_SVCS:
            number, args = raw[0], self.arguments()
            self.svcs.append((number, args))
            if number in self.svc_errors:
                uc.reg_write(self.regs[0], self.svc_errors[number])
                uc.reg_write(self.pc, (address + 2) | 1)
            else:
                # Tail-enter the shipped handler using the veneer caller's LR.
                # Its AAPCS return resumes after the veneer call. Exception entry
                # and RF scheduling are outside this probe's coverage.
                uc.reg_write(self.pc, self.NATIVE_SVCS[number] | 1)
            return
        super()._instruction(uc, address, size, user)

    def _svc(self, number, args):
        if number == 0x60:
            self.reset_identity()
        if number == 0x65 and self.read(args[0] + 2, 1) != b'\x01':
            kind = self.read(args[0] + 2, 1)[0]
            uuid = bytearray(self.uuid_bases[kind - 2])
            uuid[12:14] = self.read(args[0], 2)
            self.write(args[1], b'\x10')
            if args[2]:
                self.write(args[2], uuid)
            return 0
        return super()._svc(number, args)

    def _ecb_task(self, uc, access, address, size, value, user):
        if value != 1:
            return
        pointer = self.u32(0x4000E504)
        if pointer % 4 or not 0x20002000 <= pointer <= 0x20004000 - 48:
            raise AssertionError('ECB pointer is not an aligned application RAM block')
        key, clear = self.read(pointer, 16), self.read(pointer + 16, 16)
        if clear != b'XSC2015 Xbox IRK':
            raise AssertionError('Unexpected identity derivation label')
        self.ecb_calls.append((key, clear))
        self.write(pointer + 32, IDENTITY_VECTORS[key])
        self.write32(0x4000E100, 1)  # ENDECB event, only after START task.

    def boot(self, xbox, erase=0):
        self.call('radio_boot')
        self.original_timers()
        self.configure(marker=0x58420180 if xbox else 128)
        self.boundary_stubs.update({0x1EA60: 0, 0x18B9C: 0, 0x18FA0: 0,
                                    0x1CAA0: self.empty_bond_whitelist})
        self.call('radio_init', erase)

    def distributed_identity(self, address):
        self.write(0x20000A87, address)
        context, output = self.alloc(bytes(0x104)), self.alloc(bytes(20))
        result = self.call(0xBB86, context, 1, 2, self.alloc(bytes(12)), output,
                           self.alloc(bytes(4)))
        self.assert_success(result)
        return self.read(self.u32(output + 12), 16), self.read(context + 0xFC, 7)


class RadioIdentityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.build = RadioBuild()
        cls.addClassCleanup(cls.build.close)

    def test_original_device_manager_requests_default_local_identity(self):
        class PairingRadio(BondRadioMachine):
            def _svc(self, number, args):
                if number == 0x7F:
                    self.reply = (args[:2], self.read(args[2], 5),
                                  struct.unpack('<6I', self.read(args[3], 24)))
                    return 0
                return super()._svc(number, args)

        m = PairingRadio(self.build)
        m.ble_event(0x13, 0)  # Actual SEC_PARAMS_REQUEST -> original DM -> reply.
        (connection, status), params, keys = m.reply
        self.assertEqual((connection, status), (0, 0))
        self.assertTrue(params[3] & 2, 'peripheral requests IdKey distribution')
        self.assertNotEqual(keys[0], 0)  # Its encryption-key output is separate.
        self.assertEqual(keys[1], 0)  # NULL own id selects configured/default IRK.
        self.assertNotEqual(keys[4], 0)  # Peer identity has its own receive buffer.

    def test_shipped_null_own_id_uses_global_irk_despite_distinct_addresses(self):
        identities = []
        for address in (b'\x01ABCDEF', b'\x01UVWXYZ'):
            m = OriginalRadioMachine(self.build)
            key = bytes(range(16))
            m.write(0x20000B40, key)
            m.write(0x20000B50, b'\x01')  # The stack has already derived its default IRK.
            m.write(0x20000A87, address)
            context = m.alloc(bytes(0x104))
            output = m.alloc(bytes(20))
            # Own IdKey requested, own id pointer NULL, as in the shipped DM.
            result = m.call(0xBB86, context, 1, 2, m.alloc(bytes(12)), output,
                            m.alloc(bytes(4)))
            self.assertEqual(result, 0)
            self.assertEqual(m.read(context + 0xFC, 7), address)
            identities.append(m.read(m.u32(output + 12), 16))
        self.assertEqual(identities, [bytes(range(16))] * 2)

    def test_exact_svc_numbers_and_layout_through_original_dispatch_tables(self):
        m = IdentityRadioMachine(self.build)
        block, option = 0x20003400, 0x20003500
        m.write(option, struct.pack('<IHH', block, 0, 0))
        frame = m.alloc(struct.pack('<4I', 0x23, option, 0, 0))
        self.assertEqual(m.call(0x2190, 0x69, frame), 0)
        self.assertEqual(m.read(block, 16), m.default_irk)
        self.assertEqual(m.u16(option + 4), 900)
        m.write(block + 16, b'XSC2015 Xbox IRK')
        ecb_frame = m.alloc(struct.pack('<4I', block, 0, 0, 0))
        self.assertEqual(m.call(0x14E3C, 0x4D, ecb_frame), 0)
        self.assertEqual(m.u32(ecb_frame), 0)  # SoC dispatcher writes saved R0.
        self.assertEqual(m.read(block + 32, 16), IDENTITY_VECTORS[m.default_irk])
        m.write32(option, block + 32)
        self.assertEqual(m.call(0x2190, 0x68, frame), 0)
        # The stack copies the IRK; reusing the caller's temporary is safe.
        m.write(block, bytes(48))
        self.assertEqual(m.distributed_identity(b'\x01ABCDEF')[0],
                         IDENTITY_VECTORS[m.default_irk])
        self.assertEqual(m.u16(0x20000A90), 900)

    def test_both_boot_orders_distribute_distinct_stable_profile_identities(self):
        for order in ((False, True, False, True), (True, False, True, False)):
            for default, xbox_key in IDENTITY_VECTORS.items():
                for xbox in order:
                    with self.subTest(order=order, default=default.hex(), xbox=xbox):
                        m = IdentityRadioMachine(self.build, default, interval=777)
                        m.boot(xbox)
                        address = b'\x01ABCD\x05\xc6' if xbox else b'\x01WXYZ\x07\xc8'
                        irk, distributed_address = m.distributed_identity(address)
                        self.assertEqual(irk, xbox_key if xbox else default)
                        self.assertEqual(distributed_address, address)
                        self.assertEqual(m.u16(0x20000A90), 777)
                        self.assertEqual(len(m.ecb_calls), int(xbox))

    def test_repair_uses_same_profile_irk_before_first_advertisement(self):
        for erase in (0, 1):
            m = IdentityRadioMachine(self.build)
            m.boot(True, erase)
            self.assertEqual(m.distributed_identity(b'\x01ABCDEF')[0],
                             IDENTITY_VECTORS[bytes(range(16))])
            operations = [number for number, _ in m.svcs]
            self.assertLess(operations.index(0x60), operations.index(0x69))
            self.assertLess(operations.index(0x69), operations.index(0x4D))
            self.assertLess(operations.index(0x4D), operations.index(0x68))
            self.assertNotIn(0x73, operations)
            m.write(0x200020F8, b'\x04')
            m.write(0x20002039, b'\x01')
            m.main_iteration()
            self.assertEqual(sum(number == 0x73 for number, _ in m.svcs), 1)

    def test_identity_api_error_stops_initialization_before_advertising(self):
        for failing in (0x69, 0x4D, 0x68):
            with self.subTest(svc=hex(failing)):
                m = IdentityRadioMachine(self.build)
                m.svc_errors[failing] = 8
                with self.assertRaisesRegex(AssertionError, 'original fatal error'):
                    m.boot(True)
                self.assertNotIn(0x73, [number for number, _ in m.svcs])
                self.assertEqual(m.read(0x20000B40, 16), m.default_irk)


if __name__ == '__main__':
    unittest.main()
