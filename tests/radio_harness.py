"""Independent Cortex-M0 ABI harness: real radio/input code and shipped SDK code.

Only hardware/SoftDevice and named application entry points are mocked. This
module deliberately does not import the firmware builder or duplicate radio.c.
"""
import hashlib
import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / ".tools/base/vcf_wired_controller_d0g_5a0e3f348_radio.bin"
BASE_SHA = "db45055fc5251a5800961337454a76fb7a0ccf30a09414f757df5b20d73b96bb"
HIDS = 0x2000251C
CONN = 0x20002006
STACK = 0x20003E28
SCRATCH = 0x10000000
RETURN = 0x3F000

# Independent AES-128 ECB known-answer fixtures (synthetic keys). No crypto
# package is needed by the tests; only the hardware boundary returns a vector.
IDENTITY_VECTORS = {
    bytes(range(16)): bytes.fromhex('68bf4573019416e8f1197465299aae0b'),
    bytes(range(16, 32)): bytes.fromhex('5f88dfe0f6ed4633c7a258bea196719b'),
}


def unavailable(message):
    if os.environ.get("XSC_REQUIRE_EMULATION") == "1":
        raise RuntimeError(message)
    raise unittest.SkipTest(message)


class RadioBuild:
    def __init__(self):
        sys.path.insert(0, str(ROOT / ".tools/python"))
        try:
            import unicorn
        except ImportError:
            unavailable("radio tests need unicorn==2.1.4 (XSC_REQUIRE_EMULATION=1 forbids skip)")
        if unicorn.__version__ != "2.1.4":
            raise RuntimeError(f"radio tests require Unicorn 2.1.4, got {unicorn.__version__}")
        suffix = ".exe" if os.name == "nt" else ""
        compiler = ROOT / f".tools/gcc/bin/arm-none-eabi-gcc{suffix}"
        if not compiler.exists():
            found = shutil.which("arm-none-eabi-gcc")
            if not found:
                unavailable("radio tests need arm-none-eabi-gcc")
            compiler = Path(found)
        if not BASE.exists():
            unavailable(f"radio tests need pinned base image {BASE}")
        self.base = BASE.read_bytes()
        if hashlib.sha256(self.base).hexdigest() != BASE_SHA:
            raise RuntimeError("radio baseline SHA-256 mismatch")
        self.temporary = tempfile.TemporaryDirectory(prefix="radio-abi-", dir=ROOT / ".tools")
        self.directory = Path(self.temporary.name)
        self.elf = self.directory / "radio.elf"
        command = [str(compiler), "-mcpu=cortex-m0", "-mthumb", "-Os", "-std=c11",
                   "-ffreestanding", "-fno-builtin", "-fno-unwind-tables",
                   "-fno-asynchronous-unwind-tables", "-Wall", "-Wextra", "-Werror",
                   "-nostdlib", "-Wl,--build-id=none", "-T", str(ROOT / "src/radio.ld"),
                   "-I", str(ROOT / "src"), "-o", str(self.elf)]
        command += [str(ROOT / "src" / name) for name in ("radio.c", "radio_config.c", "input.c", "mapping.c", "runtime.c")]
        command += ["-lgcc"]
        compiled = subprocess.run(command, capture_output=True, text=True)
        if compiled.returncode:
            self.temporary.cleanup()
            raise RuntimeError(f"ARM build failed:\n{compiled.stdout}\n{compiled.stderr}")
        objcopy = compiler.with_name(f"arm-none-eabi-objcopy{suffix}")
        binary = self.directory / "radio.bin"
        subprocess.run([str(objcopy), "-O", "binary", str(self.elf), str(binary)], check=True,
                       capture_output=True, text=True)
        self.payload = binary.read_bytes()
        nm = compiler.with_name(f"arm-none-eabi-nm{suffix}")
        symbols = subprocess.check_output([str(nm), "-n", str(self.elf)], text=True)
        self.symbols = {}
        for line in symbols.splitlines():
            fields = line.split()
            if len(fields) == 3:
                self.symbols[fields[2]] = int(fields[0], 16)

    def close(self):
        self.temporary.cleanup()


class RadioMachine:
    STUBS = {
        0x1D2F0: "main", 0x189C8: "config", 0x18394: "init", 0x18D10: "gap",
        0x1B7B0: "dis", 0x1B960: "hids", 0x1B23C: "advertising",
        0x19418: "input", 0x18948: "feature", 0x19518: "status", 0x1912C: "event",
        0x18744: "service", 0x16CD8: "timer_create", 0x16DC8: "timer_start",
        0x1AF68: "fatal",
        0x16A46: "uart_send",
        0x3EF00: "config_sink",  # Test-only callback target, outside shipped/payload code.
    }

    def __init__(self, build):
        from unicorn import Uc, UC_ARCH_ARM, UC_MODE_THUMB, UC_MODE_MCLASS, UC_HOOK_CODE
        from unicorn.arm_const import (UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2,
                                       UC_ARM_REG_R3, UC_ARM_REG_SP, UC_ARM_REG_LR,
                                       UC_ARM_REG_PC, UC_ARM_REG_XPSR, UC_CPU_ARM_CORTEX_M0)
        from unicorn import arm_const
        self.regs = (UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_R3)
        self.sp, self.lr, self.pc, self.xpsr = (UC_ARM_REG_SP, UC_ARM_REG_LR,
                                             UC_ARM_REG_PC, UC_ARM_REG_XPSR)
        self.uc = Uc(UC_ARCH_ARM, UC_MODE_THUMB | UC_MODE_MCLASS)
        self.uc.ctl_set_cpu_model(UC_CPU_ARM_CORTEX_M0)
        self.saved_regs = [getattr(arm_const, f"UC_ARM_REG_R{index}") for index in range(4, 12)]
        self.uc.mem_map(0, 0x40000)
        self.uc.mem_write(0, build.base)
        self.uc.mem_write(0x20000, build.payload)
        self.uc.mem_map(0x20000000, 0x4000)
        self.uc.mem_map(SCRATCH, 0x10000)
        self.symbols = build.symbols
        self.calls, self.svcs, self.notifications = [], [], []
        self.characteristics, self.descriptors = [], []
        self.advertisements = []
        self.uart_packets = []
        self.values = {}
        self.uuid_bases = []
        self.name, self.appearance = b"SteamController", 0
        self.name_permission = None
        self.identity_irk = bytes(range(16))
        self.identity_interval = 900
        self.execute_original = set()
        self.return_codes = {}
        self.sec = b"\x21\x10"
        self.sec_error = self.cccd_error = 0
        self.cccd = 1
        self.hvx_results = []
        self.hvx_length = None
        self.timer_callback = None
        self.allocation = SCRATCH
        self.next_handle = 0x40
        self.uc.hook_add(UC_HOOK_CODE, self._instruction)
        self.write16(CONN, 0xFFFF)
        self.write16(HIDS + 0x176, 0xFFFF)

    def read(self, address, size):
        return bytes(self.uc.mem_read(address, size))

    def write(self, address, value):
        self.uc.mem_write(address, bytes(value))

    def u16(self, address):
        return struct.unpack("<H", self.read(address, 2))[0]

    def u32(self, address):
        return struct.unpack("<I", self.read(address, 4))[0]

    def write16(self, address, value):
        self.write(address, struct.pack("<H", value))

    def write32(self, address, value):
        self.write(address, struct.pack("<I", value))

    def alloc(self, data):
        address = self.allocation
        self.allocation += (len(data) + 7) & ~7
        assert self.allocation < SCRATCH + 0x10000
        self.write(address, data)
        return address

    def arguments(self):
        return tuple(self.uc.reg_read(reg) for reg in self.regs)

    def call(self, name, *args):
        address = self.symbols[name] if isinstance(name, str) else name
        self.uc.reg_write(self.xpsr, 0x01000000)
        self.uc.reg_write(self.sp, STACK)
        self.uc.reg_write(self.lr, RETURN | 1)
        for index, reg in enumerate(self.saved_regs):
            self.uc.reg_write(reg, 0xCA110000 + index)
        for index, reg in enumerate(self.regs):
            self.uc.reg_write(reg, args[index] if index < len(args) else 0)
        # Entry stack arguments are below patch BSS, never in the reserved state.
        entry_sp = STACK - 8 * ((max(0, len(args) - 4) + 1) // 2)
        self.uc.reg_write(self.sp, entry_sp)
        for index, value in enumerate(args[4:]):
            self.write32(entry_sp + index * 4, value)
        self.uc.emu_start(address | 1, RETURN, count=300000)
        if self.uc.reg_read(self.pc) != RETURN:
            raise AssertionError(f"instruction budget exhausted at {self.uc.reg_read(self.pc):#x}")
        if self.uc.reg_read(self.sp) != entry_sp:
            raise AssertionError("callee did not restore SP")
        for index, reg in enumerate(self.saved_regs):
            if self.uc.reg_read(reg) != 0xCA110000 + index:
                raise AssertionError("callee-saved register corruption")
        return self.uc.reg_read(self.regs[0])

    def _instruction(self, uc, address, size, _):
        if address in self.STUBS:
            name = self.STUBS[address]
            args = self.arguments()
            if name == "advertising":
                args += (self.u32(uc.reg_read(self.sp)),)
            self.calls.append((name, args))
            if name == "fatal":
                raise AssertionError(f"original fatal error: {args}")
            if name == "uart_send":
                self.uart_packets.append(self.read(args[0], args[1]))
            if name == "timer_create":
                self.timer_callback = args[2]
            if name not in self.execute_original:
                uc.reg_write(self.regs[0], self.return_codes.get(name, 0))
                uc.reg_write(self.pc, uc.reg_read(self.lr))
                return
        raw = self.read(address, 2)
        if raw[1] == 0xDF:
            args = self.arguments()
            self.svcs.append((raw[0], args))
            result = self._svc(raw[0], args)
            uc.reg_write(self.regs[0], result)
            uc.reg_write(self.pc, (address + 2) | 1)

    def _attribute(self, pointer):
        length, offset, maximum = struct.unpack("<HHH", self.read(pointer + 8, 6))
        value = self.u32(pointer + 16)
        return {"uuid": self.u16(self.u32(pointer)), "length": length,
                "offset": offset, "maximum": maximum,
                "permission": self.read(self.u32(pointer + 4), 3),
                "value": self.read(value, length) if value and length else b""}

    def _svc(self, number, args):
        r0, r1, r2, r3 = args
        if number in (0x68, 0x69):
            assert r0 == 0x23, 'S110 v8 BLE_GAP_OPT_PRIVACY'
            pointer = self.u32(r1)
            if number == 0x69:
                self.write(pointer, self.identity_irk)
                self.write16(r1 + 4, self.identity_interval)
            else:
                self.identity_irk = self.read(pointer, 16)
                self.identity_interval = self.u16(r1 + 4)
            return 0
        if number == 0x4D:
            assert r0 % 4 == 0, 'ECB hardware data must be word-aligned'
            assert self.read(r0 + 16, 16) == b'XSC2015 Xbox IRK'
            self.write(r0 + 32, IDENTITY_VECTORS[self.read(r0, 16)])
            return 0
        if number == 0x63:
            self.uuid_bases.append(self.read(r0, 16))
            self.write(r1, bytes([len(self.uuid_bases) + 1]))
            return 0
        if number == 0xA4:
            assert r0 == 0xFFFF
            assert self.u16(r2 + 2) == 0
            self.values[r1] = self.read(self.u32(r2 + 4), self.u16(r2))
            return 0
        if number == 0x83:
            assert r0 == self.u16(CONN)
            self.write(r1, self.sec)
            return self.sec_error
        if number == 0xA5:
            assert r0 == self.u16(CONN)
            assert r1 == self.u16(HIDS + 24), "must read actual input[0] CCCD"
            assert self.read(r2, 4) == b"\x02\x00\x00\x00", "S110 v8 value struct ABI"
            self.write16(self.u32(r2 + 4), self.cccd)
            return self.cccd_error
        if number == 0xA6:
            # This SVC is reached through the unmodified shipped 0x1ba90 routine.
            assert r0 == self.u16(HIDS + 0x176)
            handle, kind, _, offset = struct.unpack("<HBBH", self.read(r1, 6))
            length_pointer = self.u32(r1 + 8)
            data_pointer = self.u32(r1 + 12)
            assert (handle, kind, offset) == (self.u16(HIDS + 20), 1, 0)
            self.notifications.append(self.read(data_pointer, self.u16(length_pointer)))
            if self.hvx_length is not None:
                self.write16(length_pointer, self.hvx_length)
            return self.hvx_results.pop(0) if self.hvx_results else 0
        if number == 0x7C:
            self.name_permission = self.read(r0, 1)
            self.name = self.read(r1, r2)
            return 0
        if number == 0x78:
            self.appearance = r0
            return 0
        if number == 0x79:
            self.write16(r0, self.appearance)
            return 0
        if number == 0x7D:
            capacity = self.u16(r1)
            self.write(r0, self.name[:capacity])
            self.write16(r1, len(self.name))
            return 0
        if number == 0x65:
            assert self.read(r0 + 2, 1) == b"\x01", "only standard BLE UUID is expected"
            self.write(r1, b"\x02")
            if r2:
                self.write(r2, self.read(r0, 2))
            return 0
        if number == 0x72:
            self.advertisements.append((self.read(r0, r1), self.read(r2, r3)))
            return 0
        if number == 0xA0:
            self.write16(r2, self.next_handle)
            self.next_handle += 1
            return 0
        if number == 0xA2:
            item = self._attribute(r2)
            item["properties"] = self.read(r1, 1)[0]
            cccd_md = self.u32(r1 + 20)
            item["cccd_permission"] = self.read(cccd_md, 2) if cccd_md else None
            item["handles"] = tuple(range(self.next_handle, self.next_handle + 4))
            self.next_handle += 4
            self.write(r3, struct.pack("<4H", *item["handles"]))
            self.characteristics.append(item)
            return 0
        if number == 0xA3:
            self.descriptors.append(self._attribute(r1))
            self.write16(r2, self.next_handle)
            self.next_handle += 1
            return 0
        raise AssertionError(f"unexpected SVC {number:#x} args={args}")

    def configure(self, marker=0x58420180, length=137, first=0x5D, index=0):
        packet = bytes([first]) + struct.pack("<II", index, marker) + bytes(128)
        pointer = self.alloc(packet)
        self.call("radio_config", pointer, length)
        return pointer

    def xbox(self, seed=True):
        self.call("radio_boot")
        self.configure()
        self.call("radio_init", 0)
        self.svcs.clear()  # Individual tests observe post-initialization SVC traffic.
        self.write(HIDS + 18, b"\x01")
        self.write16(HIDS + 20, 0x44)
        self.write16(HIDS + 24, 0x46)
        self.connect()
        if seed:
            self.full_input()

    def full_input(self, buttons=0):
        self.input(struct.pack("<H", 0x03B4) + buttons.to_bytes(3, "little") + bytes(14))

    def connect(self, handle=0x123):
        self.write16(CONN, handle)
        self.write16(HIDS + 0x176, handle)

    def cycle(self):
        assert self.timer_callback is not None
        self.call(self.timer_callback, 0)
        self.call("radio_service")

    def input(self, packet):
        self.call("radio_input", self.alloc(packet), len(packet))

    def event(self, number):
        self.call("radio_event", self.alloc(struct.pack("<HHH", number, 6, self.u16(CONN))))

    def hids_event(self, number):
        self.call("hids_event", HIDS, self.alloc(bytes([number]) + bytes(15)))

    def original_hids_write(self, handle, value):
        event = bytearray(33)
        struct.pack_into("<HHH", event, 0, 0x50, len(event), self.u16(CONN))
        struct.pack_into("<H", event, 6, handle)
        struct.pack_into("<H", event, 30, 1)
        event[32] = value
        self.call(0x1E17C, HIDS, self.alloc(event))
