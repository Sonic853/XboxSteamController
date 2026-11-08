"""Compile and execute the real Cortex-M0 payload with simulated ROM IAP.

Install Unicorn with: python -m pip install --target .tools/python unicorn==2.1.4
Run: python -m unittest discover -s tests -p test_host.py -v
No firmware updater, hardware, native REA session or target process is used.
"""
import hashlib
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import unittest
import zlib

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / ".tools/python"))
from tools.image import bl_target, thumb_bl

try:
    from unicorn import Uc, UC_ARCH_ARM, UC_MODE_THUMB, UC_MODE_MCLASS, UC_HOOK_CODE
    from unicorn.arm_const import (UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2,
                                  UC_ARM_REG_R3, UC_ARM_REG_R4, UC_ARM_REG_R5,
                                  UC_ARM_REG_R6, UC_ARM_REG_R7, UC_ARM_REG_SP,
                                  UC_ARM_REG_LR, UC_ARM_REG_PC, UC_ARM_REG_PRIMASK)
    HAS_UNICORN = True
except ImportError:
    HAS_UNICORN = False

STATE = 0x10001FD0
BUFFER = 0x10001000
STOP = 0x30000
REGS = [] if not HAS_UNICORN else [UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_R3]


def u32(data):
    return struct.unpack("<I", data)[0]


def record(body, generation, mapping=None):
    """Independent format oracle, using Python's standard CRC implementation."""
    page = bytearray(b"\xff" * 256)
    struct.pack_into("<IHHII", page, 0, 0x314A4258, 1, 128,
                     generation, generation ^ 0xFFFFFFFF)
    page[16:144] = body
    if mapping is not None:
        struct.pack_into("<I", page, 144, 0x314D4358)
        page[148:172] = mapping
    struct.pack_into("<I", page, 248, 0x4B4F4258)
    struct.pack_into("<I", page, 252, zlib.crc32(page[:252]))
    return bytes(page)


def compiler_tool(name):
    suffix = ".exe" if os.name == "nt" else ""
    local = ROOT / f".tools/gcc/bin/arm-none-eabi-{name}{suffix}"
    result = str(local) if local.is_file() else shutil.which(f"arm-none-eabi-{name}")
    if not result:
        raise unittest.SkipTest("ARM GCC toolchain missing; prepare .tools/gcc")
    return result


def firmware(filename):
    candidates = [Path(os.environ.get("XSC_BASE_DIR", str(ROOT / ".tools/base"))) / filename,
                  ROOT.parent / "Steam Controller FW Update Tool/updatescripts/fw_images/ble" / filename]
    for path in candidates:
        if path.is_file():
            return path.read_bytes()
    raise unittest.SkipTest(f"Pinned firmware fixture missing: {filename}")


def compile_host():
    out = ROOT / "build/host-tests"
    out.mkdir(parents=True, exist_ok=True)
    flags = ["-mcpu=cortex-m0", "-mthumb", "-Os", "-std=c11", "-ffreestanding",
             "-fno-builtin", "-fno-unwind-tables", "-fno-asynchronous-unwind-tables",
             "-ffunction-sections", "-fdata-sections",
             "-Wall", "-Wextra", "-Werror", "-fstack-usage"]
    objects = []
    for source in ("host", "bond_store", "mapping", "runtime"):
        obj = out / f"{source}.o"
        subprocess.run([compiler_tool("gcc"), *flags, "-c", str(ROOT / f"src/{source}.c"),
                        "-o", str(obj)], check=True, capture_output=True, text=True)
        objects.append(str(obj))
    elf = out / "host.elf"
    subprocess.run([compiler_tool("gcc"), *flags, "-nostdlib",
                    f"-Wl,-T,{ROOT / 'src/host.ld'},-Map,{out / 'host.map'}",
                    *objects, "-lgcc", "-o", str(elf)], check=True, capture_output=True, text=True)
    subprocess.run([compiler_tool("objcopy"), "-O", "binary", "-j", ".text", str(elf),
                    str(out / "host.bin")], check=True)
    nm = subprocess.check_output([compiler_tool("nm"), "-n", str(elf)], text=True)
    symbols = {parts[2]: int(parts[0], 16) for line in nm.splitlines()
               if len(parts := line.split()) == 3}
    return symbols, (out / "host.bin").read_bytes(), out


def patched_image(base, payload, symbols, config):
    """Test-side schema interpreter checks the complete hook contract."""
    assert hashlib.sha256(base).hexdigest() == config["base"]["sha256"]
    origin, limit = (int(config["payload"][k], 0) for k in ("origin", "limit"))
    assert origin >= len(base) and origin + len(payload) <= limit
    data = bytearray(base + b"\xff" * (origin - len(base)) + payload)
    touched = set()
    for hook in config["hooks"]:
        a = int(hook["address"], 0)
        if hook["kind"] == "bl":
            assert bl_target(a, base[a:a+4]) == int(hook["expected_target"], 0)
            after = thumb_bl(a, symbols[hook["symbol"]] & ~1)
        elif hook["kind"] == "literal":
            assert u32(base[a:a+4]) == int(hook["expected"], 0)
            after = struct.pack("<I", symbols[hook["symbol"]] | int(hook.get("thumb", False)))
        else:
            before, after = bytes.fromhex(hook["before"]), bytes.fromhex(hook["after"])
            assert len(before) == len(after) and base[a:a+len(before)] == before
        for i in range(a, a + len(after)):
            assert i >= 0x2000 and i not in touched
            for region in config["protected_ranges"]:
                assert not int(region["start"], 0) <= i < int(region["end"], 0)
            touched.add(i)
        data[a:a+len(after)] = after
    return bytes(data)


class Machine:
    def __init__(self, image, symbols=None):
        self.uc = Uc(UC_ARCH_ARM, UC_MODE_THUMB | UC_MODE_MCLASS)
        for address, size in ((0, 0x40000), (0x10000000, 0x2000),
                              (0x20000000, 0x10000), (0x1FFF0000, 0x2000)):
            self.uc.mem_map(address, size)
        self.uc.mem_write(0, b"\xff" * 0x40000)
        self.uc.mem_write(0, image)
        self.symbols = symbols or {}
        self.hooks = {}
        self.calls = []
        self.buttons = set()
        self.sent = []
        self.iap_calls = []
        self.iap_sp = []
        self.fail_at = None
        self.fail_status = 9
        self.cut_at = None
        self.torn_size = None
        self.no_copy = False
        self.cut = False
        self.prepared = set()
        self.word(0x100007D8, 48000000)

    def read(self, address, size):
        return bytes(self.uc.mem_read(address, size))

    def word(self, address, value=None):
        if value is None:
            return u32(self.read(address, 4))
        self.uc.mem_write(address, struct.pack("<I", value))

    def stub(self, address, action=None, result=0):
        def hook(uc, pc, size, data):
            args = [uc.reg_read(r) for r in REGS]
            assert uc.reg_read(UC_ARM_REG_SP) % 8 == 0, hex(pc)
            self.calls.append((address, args))
            value = action(*args) if action else result
            if self.cut:
                uc.emu_stop()
                return
            if value is not None:
                uc.reg_write(UC_ARM_REG_R0, value)
            uc.reg_write(UC_ARM_REG_PC, uc.reg_read(UC_ARM_REG_LR))
        self.hooks[address] = self.uc.hook_add(UC_HOOK_CODE, hook, begin=address, end=address)

    def remove_stub(self, address):
        self.uc.hook_del(self.hooks.pop(address))

    def host_stubs(self):
        for address in (0x64B4, 0x50AC, 0x52FC, 0x7A40, 0xD7E4, 0xD560, 0xD8BC, 0xD604):
            self.stub(address)
        self.stub(0x8034, lambda key, *_: int(key in self.buttons))
        self.stub(0x6BC8, result=7)
        self.stub(0xD400, lambda p, size, *_: self.sent.append(self.read(p, size)))
        self.stub(0x1FFF1FF0, self.rom_iap)
        # Real image's division/memcpy run unchanged.

    def rom_iap(self, command, result, *_):
        args = struct.unpack("<5I", self.read(command, 20))
        self.iap_calls.append(args)
        self.iap_sp.append(self.uc.reg_read(UC_ARM_REG_SP))
        assert self.uc.reg_read(UC_ARM_REG_PRIMASK) == 1
        assert self.iap_sp[-1] >= 0x10001D00
        cmd, a, b, c, d = args
        call_number = len(self.iap_calls)
        if self.cut_at == call_number:
            self.cut = True
            return
        if self.fail_at == call_number:
            self.word(result, self.fail_status)
            return
        if cmd in (50, 52):
            assert a == b and a in (30, 31)
        if cmd == 50:
            self.prepared.add(a)
        elif cmd == 52:
            assert a in self.prepared and c == 48000
            self.uc.mem_write(a * 4096, b"\xff" * 4096)
            self.prepared.remove(a)
        elif cmd == 51:
            assert 0x1E000 <= a < 0x20000 and a % 256 == 0
            assert a // 4096 in self.prepared
            assert b % 4 == 0 and 0x10001C00 <= b <= STATE - 256
            assert c == 256 and d == 48000
            self.prepared.remove(a // 4096)
            if not self.no_copy:
                page = self.read(b, c)
                count = c if self.torn_size is None else self.torn_size
                old = self.read(a, count)
                assert all((x & y) == y for x, y in zip(old, page[:count]))
                self.uc.mem_write(a, page[:count])
        else:
            raise AssertionError(f"unexpected IAP command {cmd}")
        self.word(result, 0)

    def call(self, name, *args, sp=STATE, primask=0, registers=None, stop=STOP):
        address = self.symbols[name] if isinstance(name, str) else name
        self.uc.reg_write(UC_ARM_REG_SP, sp)
        self.uc.reg_write(UC_ARM_REG_LR, stop | 1)
        self.uc.reg_write(UC_ARM_REG_PRIMASK, primask)
        for reg, value in zip(REGS, args):
            self.uc.reg_write(reg, value)
        for reg, value in (registers or {}).items():
            self.uc.reg_write(reg, value)
        self.uc.emu_start(address | 1, stop, count=5000000)
        if not self.cut:
            assert self.uc.reg_read(UC_ARM_REG_PC) == stop, hex(self.uc.reg_read(UC_ARM_REG_PC))
            assert self.uc.reg_read(UC_ARM_REG_SP) == sp
        return self.uc.reg_read(UC_ARM_REG_R0)

    def xbox(self, pairing=0, first=0):
        self.uc.mem_write(STATE, struct.pack("<IBBBBII", 0x31485358, 1, 3, pairing, first, 0, 0))

    def seed(self, body, generation=1, address=0x1E000):
        self.uc.mem_write(address, record(body, generation))

    def store_read(self, reset=0):
        assert self.call("bond_store_read", BUFFER, 0, 128, reset) == 1
        return self.read(BUFFER, 128)

    def store_write(self, body, offset=0, reset=0, **kwargs):
        self.uc.mem_write(BUFFER, body)
        return self.call("bond_store_write", offset, BUFFER, len(body), reset, **kwargs)

    def mapping_read(self):
        self.call("mapping_store_read", BUFFER)
        return self.read(BUFFER, 24)

    def mapping_write(self, config, token=None, **kwargs):
        if token is None:
            token = zlib.crc32(self.mapping_read())
        self.uc.mem_write(BUFFER, config)
        return self.call("mapping_store_write", token, BUFFER, **kwargs)

    def scatter(self, base):
        """Original load/scatter bytes; do not hand-create the input field table."""
        self.uc.mem_write(0x10000000, base[0x11C20:0x11D2C])
        self.uc.mem_write(0x20000000, base[0x12024:0x120A0])
        p, out = 0x11D2C, bytearray()
        while len(out) < 0x5E0:
            c = base[p]
            p += 1
            literal, count = c & 7, c >> 4
            if not literal:
                literal = base[p]
                p += 1
            if not count:
                count = base[p]
                p += 1
            out.extend(base[p:p + literal - 1])
            p += literal - 1
            if c & 8:
                distance = base[p]
                p += 1
                for _ in range(count + 2):
                    out.append(out[-distance])
            else:
                out.extend(bytes(count))
        assert p == 0x12024 and len(out) == 0x5E0
        self.uc.mem_write(0x10000200, bytes(out))

    def full_input_chain(self, base):
        self.scatter(base)
        self.xbox()
        self.remove_stub(0x52FC)  # use the actual default/min/max table and setter
        self.word(0x20000078, 0x10836)
        self.call(0x52C0)
        self.word(0x10000348, BUFFER + 256)
        for address in (0x74CC, 0xCCEC, 0x82DC, 0x6A64, 0x79B8, 0x8368,
                        0x82D0, 0x6EC4, 0x3150, 0x680C, 0x849C, 0xACE8, 0xAD0C):
            self.stub(address)
        self.stick = (12000, -9000)
        self.left = (10000, 13000)
        self.right = (-6000, 8000)
        self.analog = {4: 0x2000, 5: 0x4000}
        def stick(_, x, y, __):
            self.uc.mem_write(x, struct.pack("<h", self.stick[0]))
            self.uc.mem_write(y, struct.pack("<h", self.stick[1]))
        def left(*_):
            self.uc.mem_write(BUFFER + 160, struct.pack("<hhhh", self.left[0], 0, self.left[1], 0))
            return BUFFER + 160
        def right(_, x, y, touch):
            self.uc.mem_write(x, struct.pack("<h", self.right[0]))
            self.uc.mem_write(y, struct.pack("<h", self.right[1]))
            self.uc.mem_write(touch, b"\x01")
        self.stub(0x67D4, stick)
        self.stub(0xB514, result=1)
        self.stub(0xB520, left)
        self.stub(0xB538, right)
        self.stub(0x8010, lambda index, *_: self.analog.get(index, 0))
        self.wire = bytearray()
        self.stub(0xBA54, lambda ptr, size, *_: self.wire.extend(self.read(ptr, size)))

    def input_packet(self):
        wire = bytes(self.wire)
        assert wire[0] == 2 and wire[-1] == 3, wire.hex()
        result = bytearray()
        escaped = False
        for value in wire[1:-1]:
            if escaped:
                result.append(value ^ 0x20)
                escaped = False
            elif value == 0x1F:
                escaped = True
            else:
                result.append(value)
        assert not escaped
        return bytes(result)


@unittest.skipUnless(HAS_UNICORN, "Install Unicorn in .tools/python for real ARM execution tests")
class HostTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.config = json.loads((ROOT / "config/host-patches.json").read_text())
        cls.base = firmware(cls.config["base"]["filename"])
        cls.symbols, cls.payload, cls.out = compile_host()
        cls.image = patched_image(cls.base, cls.payload, cls.symbols, cls.config)

    def setUp(self):
        self.m = Machine(self.image, self.symbols)
        self.m.host_stubs()

    def test_linker_and_patch_contract(self):
        self.assertEqual(self.symbols["host_state"], STATE)
        self.assertEqual(self.symbols["__host_state_end"], STATE + 16)
        self.assertEqual(self.image[:0x2000], self.base[:0x2000])
        self.assertEqual(u32(self.image[0x2010:0x2014]), 0)
        self.assertEqual(u32(self.image[0x2000:0x2004]), STATE)
        self.assertEqual(u32(self.image[0x20D0:0x20D4]), STATE)
        self.assertLessEqual(len(self.image), 0x1E000)
        stack = {}
        for path in self.out.glob("*.su"):
            for line in path.read_text().splitlines():
                function, size, _ = line.split("\t")
                stack[function.split(":")[-1]] = int(size)
        self.assertLessEqual(sum(stack[n] for n in ("write_record", "program", "iap")), 416)
        self.assertLessEqual(stack["bond_store_write"], 24)
        self.assertLessEqual(stack["mapping_store_write"], 24)

    def test_selector_ordinary_and_xbox_chords(self):
        m = self.m
        for keys in ({10}, {3, 10}, {2, 10}, {6}, {6, 2}, {20, 10}, {20, 2, 10}):
            m.xbox()
            m.buttons = keys
            m.calls.clear()
            m.call("host_selector")
            self.assertIn(0x64B4, [a for a, _ in m.calls])
            self.assertEqual(m.call(0x6B58), 0x10000998)
        m.seed(bytes(range(128)), 12)
        for keys, pairing in (({6, 10}, 0), ({6, 10, 2}, 1), ({6, 10, 3, 2}, 1)):
            m.buttons = keys
            m.calls.clear()
            m.call("host_selector")
            self.assertNotIn(0x64B4, [a for a, _ in m.calls])
            self.assertEqual(m.call(0x6B58), STATE + 5)
            self.assertEqual(m.read(STATE + 6, 1), bytes([pairing]))
            self.assertEqual(m.read(0x100000D0, 1), bytes([2 if pairing else 0]))
            self.assertEqual(m.word(0x100000E0), 4000 if pairing else 2000)
            self.assertEqual(m.word(STATE + 12), 12)
        self.assertEqual(m.iap_calls, [])

    def test_original_rb_physical_index_maps_to_p1_14(self):
        m = self.m
        hardware = u32(self.base[0xA554:0xA558])
        m.uc.mem_write(hardware + 4, b"\x08")
        # Official board-revision branch and GPIO-index setter, not our enum.
        m.call(0xA296, stop=0xA2C0, registers={UC_ARM_REG_R4: hardware})
        self.assertEqual(m.read(0x20000214 + 6, 1), b"\xae")
        m.remove_stub(0x8034)
        m.stub(0x4568, result=0)  # active-low physical GPIO
        self.assertEqual(m.call(0x8034, 6), 1)
        self.assertEqual(m.calls[-1][1][:3], [0x50000000, 1, 14])

    def test_first_xbox_boot_requests_pairing_without_steam_seed(self):
        m = self.m
        m.buttons = {6, 10}
        m.uc.mem_write(0x10000850, b"\xa5" * 0x14C)
        original = m.read(0x10000850, 0x14C)
        m.call("host_selector")
        self.assertEqual(m.read(STATE + 6, 1), b"\x01")
        self.assertEqual(m.call("host_config_read", 1, BUFFER, 0, 128), 1)
        self.assertEqual(m.read(BUFFER, 128), b"\xff" * 128)
        self.assertEqual(m.read(0x10000850, 0x14C), original)

    def test_all_ordinary_wrappers_forward_original_arguments(self):
        m = self.m
        m.uc.mem_write(BUFFER, b"\x5e" + b"\xff" * 136)
        cases = [("host_radio_start", (), 0xD560), ("host_connection", (3,), 0x7A40),
                 ("host_config_read", (1, BUFFER, 7, 12), 0x6BC8),
                 ("host_config_write", (4,), 0xD8BC),
                 ("host_uart_receive", (BUFFER, 137), 0xD604),
                 ("host_reply_send", (BUFFER, 137), 0xD400),
                 ("host_startup_send", (BUFFER, 137), 0xD400)]
        for name, args, original in cases:
            m.calls.clear()
            result = m.call(name, *args)
            self.assertEqual(m.calls[-1][0], original)
            self.assertEqual(m.calls[-1][1][:len(args)], list(args))
            if original == 0x6BC8:
                self.assertEqual(result, 7)
        self.assertEqual(m.call("host_full_mask", 0x1230), 0x1230)
        self.assertEqual(m.iap_calls, [])

    def test_raw_settings_reapplied_after_connection_callback(self):
        m = self.m
        m.xbox()
        for state in (0, 1):
            m.calls.clear()
            m.call("host_connection", state)
            self.assertEqual(m.calls[0][0], 0x7A40)
            self.assertEqual(m.calls[0][1][0], state)
            self.assertEqual([(a[0], a[1]) for p, a in m.calls if p == 0x52FC],
                             [(49, 2), (7, 7), (8, 7), (48, 24)])
            self.assertEqual(m.read(STATE + 7, 1), bytes([state]))
        m.calls.clear()
        m.call("host_radio_start")
        self.assertEqual(m.calls[-1][0], 0xD560)

    def test_marker_exact_match_and_startup_header_initialization(self):
        m = self.m
        m.xbox()
        for offset, size, length, mark in ((0, 128, 137, True), (1, 128, 137, False),
                                         (0, 127, 137, False), (0, 0, 9, False)):
            packet = b"\x5d" + struct.pack("<II", offset, size) + bytes(range(128))
            m.uc.mem_write(BUFFER, packet)
            m.call("host_reply_send", BUFFER, length)
            self.assertEqual(u32(m.sent[-1][5:9]), 0x58420180 if mark else size)
            self.assertEqual(m.sent[-1][9:], packet[9:length])
        m.uc.mem_write(BUFFER, b"\x5d" + b"\xa5" * 136)
        m.call("host_startup_send", BUFFER, 137)
        self.assertEqual(m.sent[-1][:9], b"\x5d\0\0\0\0\x80\x01BX")
        self.assertEqual(len(m.sent[-1]), 137)

    def test_snapshot_trampoline_preserves_abi_and_full_controls(self):
        m = self.m
        data = bytes(range(40))
        m.uc.mem_write(BUFFER, data)
        saved = {UC_ARM_REG_R4: 0x44556677, UC_ARM_REG_R6: 0x66778899, UC_ARM_REG_R7: 0x778899AA}
        for active, first, expected in ((False, 0, 0x1000), (True, 1, 0x1FF0), (True, 0, 0x13F0)):
            if active:
                m.xbox(first=first)
            m.call("host_snapshot_copy", BUFFER + 128, BUFFER, 40,
                   registers={**saved, UC_ARM_REG_R5: 0x1000})
            self.assertEqual(m.read(BUFFER + 128, 40), data)
            self.assertEqual(m.uc.reg_read(UC_ARM_REG_R5), expected)
            for reg, value in saved.items():
                self.assertEqual(m.uc.reg_read(reg), value)
        self.assertEqual(m.call("host_full_mask", 0), 0x3F0)

    def test_real_builder_tail_emits_full_twenty_byte_control_snapshot(self):
        m = self.m
        m.xbox()
        fields = [(0x10, 0, 3), (0x20, 3, 2), (0x40, 5, 3), (0x80, 8, 4),
                  (0x100, 12, 4), (0x200, 16, 4), (0x400, 20, 6),
                  (0x800, 26, 6), (0x1000, 32, 8)]
        m.uc.mem_write(0x10000350, b"".join(struct.pack("<HBB", *f) for f in fields))
        m.word(0x10000348, BUFFER)
        sp = STATE - 112
        for address in (0x6EC4, 0x3150, 0x680C, 0x849C):
            m.stub(address)
        for sample in (bytes(range(40)), bytes(40), bytes(40)):
            m.uc.mem_write(sp + 12, sample)
            m.word(sp + 0x48, sp + 12)
            # Resume the shipped builder from patched memcpy through packer.
            m.call(0x91D0, 0x100013AC, sp + 12, 40, sp=sp, stop=0x9228,
                   registers={UC_ARM_REG_R5: 0, UC_ARM_REG_R6: 0})
            self.assertEqual(m.read(BUFFER, 23), bytes([22, 0xF4, 3]) + sample[:20])
            self.assertEqual(m.read(0x1000032B, 1), b"\x01")
            # Actual no-delta gate must still return send=true, including neutral.
            m.call(0x9228, sp=sp, stop=0x925E)
            self.assertEqual(m.uc.reg_read(UC_ARM_REG_R6), 1)
            self.assertEqual(m.read(0x1000032B, 1), b"\0")

    def test_entire_input_chain_samples_and_transmits_through_real_uart_framing(self):
        m = self.m
        m.full_input_chain(self.base)
        m.buttons = {5, 6, 10, 20}
        m.call("host_radio_start")
        self.assertEqual(m.call(0x524C, 8), 7)
        for keys in ({5, 6, 10, 20}, set(), set()):
            m.buttons = keys
            m.wire.clear()
            m.call(0x7200)
            packet = m.input_packet()
            self.assertEqual(packet[:3], b"\x51\xf4\x03")
            self.assertEqual(len(packet), 23)
            buttons = int.from_bytes(packet[3:6], "little")
            # Both pads touched; A/RB/Steam/LGrip add their original masks.
            expected = 0x180000 | (0xA084 if keys else 0)
            self.assertEqual(buttons, expected)
            self.assertEqual(packet[6:8], bytes([64, 128]))
            self.assertEqual(struct.unpack("<hhhhhh", packet[11:23]),
                             (*m.stick, *m.left, *m.right))
        m.stick = m.left = m.right = (0, 0)
        m.analog = {}
        m.wire.clear()
        m.call(0x7200)
        self.assertEqual(m.input_packet()[6:8], b"\0\0")
        self.assertEqual(m.input_packet()[11:23], bytes(12))

    def test_default_reset_reproduces_old_gate_failure_and_hook_repairs_it(self):
        m = self.m
        m.full_input_chain(self.base)
        m.call("host_radio_start")
        m.call(0x52C0)  # actual reset, same target as feature 8e at 0x3640
        self.assertEqual(m.call(0x524C, 8), 0)
        self.assertEqual(m.call(0xCD3C, 0), 0)
        m.wire.clear()
        m.call(0x7200)  # includes new BL hook, actual settings setter and builder
        self.assertEqual(m.call(0x524C, 8), 7)
        self.assertEqual(m.input_packet()[:3], b"\x51\xf4\x03")
        # Ordinary mode must not convert its settings or bypass the USB builder.
        m.uc.mem_write(STATE, bytes(16))
        m.call(0x52C0)
        m.stub(0xCD3C, result=17)
        self.assertEqual(m.call("host_input_build", 0), 17)
        self.assertEqual(m.call(0x524C, 8), 0)

    def test_empty_partial_merge_noop_and_independent_record_crc(self):
        m = self.m
        self.assertEqual(m.store_read(), b"\xff" * 128)
        self.assertEqual(m.call("bond_store_present"), 0)
        self.assertEqual(m.store_write(b"abc", 17), 0)
        body = b"\xff" * 17 + b"abc" + b"\xff" * 108
        self.assertEqual(m.read(0x1E000, 256), record(body, 1))
        self.assertEqual(m.store_read(), body)
        self.assertEqual([a[0] for a in m.iap_calls], [50, 52, 50, 51])
        count = len(m.iap_calls)
        self.assertEqual(m.store_write(b"abc", 17), 0)
        self.assertEqual(len(m.iap_calls), count)
        self.assertEqual(m.store_write(b"xyz", 25), 0)
        expected = body[:25] + b"xyz" + body[28:]
        self.assertEqual(m.store_read(), expected)
        self.assertEqual(m.read(0x1E000, 256), record(body, 1))
        self.assertEqual(m.read(0x1E100, 256), record(expected, 2))

    def test_repair_clears_only_xbox_keys_preserving_own_address(self):
        m = self.m
        body = bytearray(range(128))
        body[0x6C:0x73] = b"\x01ABCDEF"
        m.seed(body)
        empty = bytearray(b"\xff" * 128)
        empty[0x6C:0x73] = body[0x6C:0x73]
        self.assertEqual(m.store_read(reset=1), empty)
        self.assertEqual(m.store_write(b"new keys", 0, reset=1), 0)
        empty[:8] = b"new keys"
        self.assertEqual(m.store_read(), empty)
        self.assertEqual(m.read(0x1E000, 256), record(body, 1))

    def test_generation_wrap_and_corrupt_newest_record(self):
        m = self.m
        m.seed(b"A" * 128, 0xFFFFFFFF)
        m.seed(b"B" * 128, 0, 0x1F000)
        self.assertEqual(m.store_read(), b"B" * 128)
        m.uc.mem_write(0x1F010, b"C")
        self.assertEqual(m.store_read(), b"A" * 128)
        self.assertEqual(m.store_write(b"D"), 0)
        self.assertEqual(m.call("bond_store_generation"), 0)

    def test_rollover_preserves_previous_sector_and_reuses_old_sector(self):
        m = self.m
        for i in range(32):
            m.seed(bytes([i]) * 128, i + 1, 0x1E000 + i * 256)
        saved = m.read(0x1F000, 4096)
        self.assertEqual(m.store_write(b"Z" * 128), 0)
        self.assertEqual(m.read(0x1F000, 4096), saved)
        self.assertEqual(m.read(0x1E000, 256), record(b"Z" * 128, 33))
        self.assertEqual(m.read(0x1E100, 3840), b"\xff" * 3840)

    def test_every_torn_prefix_rejects_new_record_and_preserves_old(self):
        m = self.m
        old = b"A" * 128
        m.seed(old, 3)
        new = record(b"B" * 128, 4)
        for size in range(256):
            m.uc.mem_write(0x1E100, new[:size] + b"\xff" * (256 - size))
            self.assertEqual(m.store_read(), old, size)
        m.uc.mem_write(0x1E100, new)
        self.assertEqual(m.store_read(), b"B" * 128)

    def test_iap_failure_or_power_cut_at_each_rollover_stage(self):
        for failure in ("fail_at", "cut_at"):
            for stage in range(1, 5):
                with self.subTest(failure=failure, stage=stage):
                    m = Machine(self.image, self.symbols)
                    m.host_stubs()
                    m.seed(b"A" * 128, 16, 0x1EF00)
                    saved = m.read(0x1E000, 4096)
                    setattr(m, failure, stage)
                    status = m.store_write(b"B" * 128)
                    if failure == "fail_at":
                        self.assertEqual(status, 9)
                        self.assertEqual(m.uc.reg_read(UC_ARM_REG_PRIMASK), 0)
                    reboot = Machine(self.image, self.symbols)
                    reboot.uc.mem_write(0x1E000, m.read(0x1E000, 8192))
                    self.assertEqual(reboot.store_read(), b"A" * 128)
                    self.assertEqual(m.read(0x1E000, 4096), saved)

    def test_torn_iap_detected_and_dirty_slot_skipped_on_retry(self):
        m = self.m
        m.seed(b"A" * 128)
        m.torn_size = 100
        self.assertEqual(m.store_write(b"B" * 128), 0x10004)
        self.assertEqual(m.store_read(), b"A" * 128)
        m.torn_size = None
        self.assertEqual(m.store_write(b"C" * 128), 0)
        self.assertEqual(m.read(0x1E200, 256), record(b"C" * 128, 2))

    def test_range_clock_stack_and_primask_guards(self):
        m = self.m
        for offset, size in ((128, 1), (129, 0), (0xFFFFFFFF, 2), (1, 128), (0, 0)):
            self.assertEqual(m.call("bond_store_write", offset, BUFFER, size, 0), 0x10001)
        for offset, size in ((129, 0), (0xFFFFFFFF, 1), (0, 0xFFFFFFFF)):
            self.assertEqual(m.call("bond_store_read", BUFFER, offset, size, 0), 0)
        m.word(0x100007D8, 0)
        self.assertEqual(m.store_write(b"x"), 0x10002)
        m.word(0x100007D8, 48000000)
        m.uc.mem_write(0x10001C00, b"\xa5" * 256)
        self.assertEqual(m.store_write(b"x", sp=0x10001E00), 0x10003)
        self.assertEqual(m.read(0x10001C00, 256), b"\xa5" * 256)
        self.assertEqual(m.iap_calls, [])
        self.assertEqual(m.store_write(b"x", primask=1), 0)
        self.assertEqual(m.uc.reg_read(UC_ARM_REG_PRIMASK), 1)
        self.assertEqual(m.read(STATE, 16), b"\0" * 16)

    def test_uart_length_guard_and_original_5f_passthrough(self):
        m = self.m
        m.xbox()
        for offset, size, length in ((0, 128, 136), (127, 2, 11), (0, 0, 9),
                                     (0xFFFFFFFF, 1, 10), (0, 1, 8)):
            m.calls.clear()
            m.uc.mem_write(BUFFER, b"\x5e" + struct.pack("<II", offset, size) + b"x" * 128)
            m.call("host_uart_receive", BUFFER, length)
            self.assertNotIn(0xD604, [a for a, _ in m.calls])
        for packet in (b"\x5f\x34\x12", b"\x5e" + struct.pack("<II", 127, 1) + b"x"):
            m.uc.mem_write(BUFFER, packet)
            m.call("host_uart_receive", BUFFER, len(packet))
            self.assertEqual(m.calls[-1][0], 0xD604)

    def mapping_fixture(self):
        m = self.m
        m.call("mapping_default", BUFFER)
        default = m.read(BUFFER, 24)
        changed = bytearray(default)
        changed[1] ^= 1
        changed[2] = 17
        struct.pack_into("<HHH", changed, 18, 1600, 3200, 9000)
        return default, bytes(changed)

    def test_mapping_legacy_default_migration_and_bond_preservation(self):
        m = self.m
        default, changed = self.mapping_fixture()
        body = bytes(range(128))
        m.seed(body, 7)
        self.assertEqual(m.mapping_read(), default)
        self.assertEqual(m.mapping_write(changed), 0)
        self.assertEqual(m.mapping_read(), changed)
        self.assertEqual(m.store_read(), body)
        self.assertEqual(m.read(0x1E100, 256), record(body, 8, changed))
        self.assertEqual(m.store_write(b"XYZ", 17), 0)
        merged = body[:17] + b"XYZ" + body[20:]
        self.assertEqual(m.read(0x1E200, 256), record(merged, 9, changed))
        self.assertEqual(m.mapping_read(), changed)
        # A new boot reads only Flash; no RAM cache is being tested.
        reboot = Machine(self.image, self.symbols)
        reboot.uc.mem_write(0x1E000, m.read(0x1E000, 8192))
        self.assertEqual(reboot.mapping_read(), changed)

    def test_mapping_only_record_does_not_skip_first_pairing(self):
        m = self.m
        _, changed = self.mapping_fixture()
        self.assertEqual(m.mapping_write(changed), 0)
        self.assertEqual(m.store_read(), b"\xff" * 128)
        self.assertEqual(m.call("bond_store_present"), 0)
        m.buttons = {6, 10}
        m.call("host_selector")
        self.assertEqual(m.read(STATE + 6, 1), b"\x01")
        self.assertEqual(m.mapping_read(), changed)

    def test_mapping_repair_retains_mapping_and_own_address(self):
        m = self.m
        _, changed = self.mapping_fixture()
        body = bytearray(range(128))
        body[0x6C:0x73] = b"\x01ABCDEF"
        m.seed(body)
        self.assertEqual(m.mapping_write(changed), 0)
        self.assertEqual(m.store_write(b"new", reset=1), 0)
        expected = bytearray(b"\xff" * 128)
        expected[:3] = b"new"
        expected[0x6C:0x73] = body[0x6C:0x73]
        self.assertEqual(m.store_read(), expected)
        self.assertEqual(m.mapping_read(), changed)

    def test_mapping_invalid_conflict_and_noop_never_write_flash(self):
        m = self.m
        default, changed = self.mapping_fixture()
        for index, value in ((0, 0), (0, 2), (1, 8), (2, 18), (17, 255)):
            bad = bytearray(default)
            bad[index] = value
            self.assertEqual(m.mapping_write(bytes(bad)), 1)
        for offset, value in ((18, 16001), (20, 65535), (22, 255), (22, 30001)):
            bad = bytearray(default)
            struct.pack_into("<H", bad, offset, value)
            self.assertEqual(m.mapping_write(bytes(bad)), 1)
        self.assertEqual(m.mapping_write(changed, zlib.crc32(default) ^ 1), 2)
        self.assertEqual(m.mapping_write(default), 0)
        self.assertEqual(m.iap_calls, [])
        self.assertEqual(m.mapping_read(), default)
        self.assertEqual(m.read(0x1E000, 8192), b"\xff" * 8192)

    def test_mapping_failures_and_torn_pages_retain_previous_config(self):
        default, changed = self.mapping_fixture()
        for fail in (1, 2, 3, 4):
            m = Machine(self.image, self.symbols)
            m.host_stubs()
            m.seed(b"B" * 128, 16, 0x1EF00)
            m.fail_at = fail
            self.assertEqual(m.mapping_write(changed), 0x20009)
            self.assertEqual(m.mapping_read(), default)
            self.assertEqual(m.store_read(), b"B" * 128)
        m = self.m
        m.seed(b"B" * 128)
        m.torn_size = 172  # mapping itself copied, record commit/CRC not complete
        self.assertEqual(m.mapping_write(changed), 0x10004)
        self.assertEqual(m.mapping_read(), default)
        self.assertEqual(m.store_read(), b"B" * 128)

    def test_mapping_torn_prefixes_never_expose_partial_mapping(self):
        m = self.m
        default, changed = self.mapping_fixture()
        old = record(b"B" * 128, 1, default)
        new = record(b"B" * 128, 2, changed)
        m.uc.mem_write(0x1E000, old)
        for size in range(256):
            m.uc.mem_write(0x1E100, new[:size] + b"\xff" * (256 - size))
            self.assertEqual(m.mapping_read(), default, size)
        m.uc.mem_write(0x1E100, new)
        self.assertEqual(m.mapping_read(), changed)

    def mapping_rpc(self, command, config=None, token=None, size=None):
        if command == 0x5D:
            packet = b"\x5d" + struct.pack("<II", 0x58434D31, 24 if size is None else size)
        else:
            if token is None:
                token = zlib.crc32(self.m.mapping_read())
            packet = b"\x5e" + struct.pack("<III", 0x58434D31, 28 if size is None else size, token) + config
        self.m.uc.mem_write(BUFFER, packet)
        self.m.call("host_uart_receive", BUFFER, len(packet))

    def assert_mapping_reply(self, status, config):
        packet = self.m.sent[-1]
        self.assertEqual(packet, b"\x5d" + struct.pack("<II", 0x58434D31, status) +
                         config + struct.pack("<I", zlib.crc32(config)))

    def test_mapping_rpc_read_and_deferred_commit_ack(self):
        m = self.m
        default, changed = self.mapping_fixture()
        m.xbox(pairing=1)
        pending = []
        m.stub(0x7E10, lambda callback, arg, *_: pending.append((callback, arg)) or 1)
        self.mapping_rpc(0x5D)
        self.assert_mapping_reply(0, default)
        m.sent.clear()
        self.mapping_rpc(0x5E, changed)
        self.assertEqual(m.sent, [])
        self.assertEqual(m.iap_calls, [])
        self.assertEqual(m.mapping_read(), default)
        m.call(*pending.pop(), sp=STATE - 32)
        self.assert_mapping_reply(0, changed)
        self.assertEqual(m.mapping_read(), changed)
        self.assertEqual(m.read(0x10001264, 1), b"\0")
        self.assertEqual(m.read(STATE + 6, 1), b"\x01")  # mapping cannot consume pairing
        self.assertTrue(m.iap_calls)

    def test_mapping_rpc_busy_enqueue_failure_invalid_and_nonmagic_passthrough(self):
        m = self.m
        default, changed = self.mapping_fixture()
        self.mapping_rpc(0x5D, size=23)
        self.assert_mapping_reply(1, default)
        self.mapping_rpc(0x5E, changed, size=27)
        self.assert_mapping_reply(1, default)
        for header in (b"\x5e", b"\x01"):
            m.uc.mem_write(0x10001264, header)
            self.mapping_rpc(0x5E, changed)
            self.assert_mapping_reply(3, default)
            self.assertEqual(m.read(0x10001264, 1), header)
        m.uc.mem_write(0x10001264, b"\0")
        m.stub(0x7E10, result=0)
        self.mapping_rpc(0x5E, changed)
        self.assert_mapping_reply(3, default)
        self.assertEqual(m.read(0x10001264, 1), b"\0")
        self.assertEqual(m.iap_calls, [])
        m.uc.mem_write(BUFFER, b"\x5d" + struct.pack("<II", 0, 128))
        m.call("host_uart_receive", BUFFER, 9)
        self.assertEqual(m.calls[-1][0], 0xD604)

    def test_mapping_rpc_checks_token_at_deferred_commit_and_returns_real_failure(self):
        m = self.m
        default, changed = self.mapping_fixture()
        pending = []
        m.stub(0x7E10, lambda callback, arg, *_: pending.append((callback, arg)) or 1)
        self.mapping_rpc(0x5E, changed, token=zlib.crc32(default) ^ 1)
        m.call(*pending.pop(), sp=STATE - 32)
        self.assert_mapping_reply(2, default)
        self.assertEqual(m.iap_calls, [])
        self.mapping_rpc(0x5E, changed)
        m.fail_at = 1
        m.call(*pending.pop(), sp=STATE - 32)
        self.assert_mapping_reply(0x20009, default)
        self.assertEqual(m.read(0x10001264, 1), b"\0")
        self.assertEqual(m.mapping_read(), default)

        # Failure ACKs must carry the real nondefault committed config/token.
        m.fail_at = None
        self.assertEqual(m.mapping_write(changed), 0)
        self.mapping_rpc(0x5D, size=23)
        self.assert_mapping_reply(1, changed)
        self.mapping_rpc(0x5E, default)  # token is valid when queued
        self.mapping_rpc(0x5E, default)  # occupied buffer, no second queued task
        self.assert_mapping_reply(3, changed)
        self.assertEqual(len(pending), 1)
        concurrent = bytearray(changed)
        concurrent[1] ^= 2
        concurrent = bytes(concurrent)
        self.assertEqual(m.mapping_write(concurrent), 0)
        committed = m.read(0x1E000, 8192)
        calls = len(m.iap_calls)
        m.call(*pending.pop(), sp=STATE - 32)
        self.assert_mapping_reply(2, concurrent)  # CAS is checked at execution
        self.assertEqual(len(m.iap_calls), calls)
        self.assertEqual(m.read(0x1E000, 8192), committed)

        self.mapping_rpc(0x5E, default)
        m.fail_at = len(m.iap_calls) + 1
        m.fail_status = 2
        m.call(*pending.pop(), sp=STATE - 32)
        self.assert_mapping_reply(0x20002, concurrent)
        self.assertEqual(m.read(0x1E000, 8192), committed)
        self.assertEqual(m.read(0x10001264, 1), b"\0")

    def test_mapping_actual_original_queue_full_recovery_and_main_loop_commit(self):
        m = self.m
        default, changed = self.mapping_fixture()
        queue = u32(self.base[0x7E58:0x7E5C])
        m.word(queue + 4, 5)  # real queue full path returns 0, never stores a task
        self.mapping_rpc(0x5E, changed)
        self.assert_mapping_reply(3, default)
        self.assertEqual(m.read(0x10001264, 1), b"\0")
        self.assertEqual(m.word(queue + 4), 5)
        self.assertEqual(m.read(0x100000A0, 1), b"\0")
        m.word(queue + 4, 0)
        m.sent.clear()
        self.mapping_rpc(0x5E, changed)
        self.assertEqual(m.sent, [])
        self.assertEqual(m.word(queue + 4), 1)
        self.assertEqual(m.word(queue + 8), self.symbols["host_config_write"] | 1)
        self.assertEqual(m.iap_calls, [])
        m.call(0x7E5C)  # real main-loop queue drain, real indirect callback
        self.assert_mapping_reply(0, changed)
        self.assertEqual(m.word(queue + 4), 0)
        self.assertEqual(m.mapping_read(), changed)

    def test_mapping_rom_status_two_is_io_error_not_token_conflict(self):
        m = self.m
        default, changed = self.mapping_fixture()
        m.fail_at = 1
        m.fail_status = 2
        self.assertEqual(m.mapping_write(changed), 0x20002)
        self.assertEqual(m.mapping_read(), default)

    def test_real_uart_queue_write_and_config_reply_pipeline(self):
        m = self.m
        m.xbox(pairing=1)
        m.remove_stub(0xD604)
        pending = []
        m.stub(0x7E10, lambda callback, arg, *_: pending.append((callback, arg)) or 1)
        m.stub(0xF244, result=BUFFER + 256)
        m.stub(0xEA58)
        m.uc.mem_write(0x10000998, b"\x02\x01")
        m.uc.mem_write(0x1000030A, b"\x7f")
        packet = b"\x5e" + struct.pack("<II", 17, 3) + b"ABC"
        m.uc.mem_write(BUFFER, packet)
        m.call("host_uart_receive", BUFFER, len(packet))
        self.assertEqual(pending, [(self.symbols["host_config_write"] | 1, 0)])
        self.assertEqual(m.read(0x10001264, len(packet)), packet)
        m.call(*pending[0], sp=STATE - 32)
        self.assertEqual(m.word(STATE + 8), 0)
        self.assertEqual(m.read(STATE + 6, 1), b"\0")
        self.assertEqual(m.read(0x10001264, 1), b"\0")
        self.assertEqual(m.read(0x10000998, 2), b"\x02\x01")
        self.assertEqual(m.read(0x1000030A, 1), b"\x7f")
        packet = b"\x5d" + struct.pack("<II", 0, 128)
        m.uc.mem_write(BUFFER, packet)
        m.call("host_uart_receive", BUFFER, len(packet))
        self.assertEqual(m.sent[-1][:9], b"\x5d\0\0\0\0\x80\x01BX")
        self.assertEqual(m.sent[-1][9:], b"\xff" * 17 + b"ABC" + b"\xff" * 108)
        m.call(0xD79C)  # actual proactive startup path, including both BL hooks
        self.assertEqual(m.sent[-1], m.sent[-2])

    def test_original_nrf_accepts_erased_configuration_and_empty_bond_header(self):
        from tools.dependencies import BASE_IMAGES
        name = "vcf_wired_controller_d0g_5a0e3f348_radio.bin"
        base = firmware(name)
        self.assertEqual(hashlib.sha256(base).hexdigest(), BASE_IMAGES[name])
        m = Machine(base)
        m.stub(0x1C11C)  # unrelated runtime state reset
        m.stub(0x1E300)  # unrelated bond/connection state reset
        src = 0x20003000
        m.uc.mem_write(src, b"\xff" * 128)
        m.call(0x1E52C, 0, src, 128, sp=0x20003E28)
        self.assertEqual(m.read(0x20002CE0, 128), b"\xff" * 128)
        self.assertEqual(m.word(0x200020F8) & 4, 4)
        m.uc.mem_write(src, b"\0")  # existing-config init, not force reset
        self.assertEqual(m.call(0x1C70C, src, 0, 0, 0, sp=0x20003E28), 0)
        self.assertEqual(m.read(0x20002EAC, 28), b"\xff" * 28)
        self.assertEqual(m.read(0x20002194, 1), b"\x01")
        m.uc.mem_write(src + 16, b"\x02" + b"\0" * 6)
        self.assertEqual(m.call(0x1C1F0, src, src + 16, sp=0x20003E28), 0)
        self.assertEqual(m.read(0x20002EC6, 1), b"\xfe")


if __name__ == "__main__":
    unittest.main()
