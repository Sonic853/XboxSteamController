"""Build freestanding Cortex-M0 C and execute its Thumb code in Unicorn.

No host-native copy of the implementation is used. Shared by firmware tests.
Dependencies: pinned project GCC and unicorn==2.1.4 (global or .tools/python).
"""

import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / ".tools" / "python"))


def _tool(name):
    suffix = ".exe" if os.name == "nt" else ""
    local = ROOT / ".tools" / "gcc" / "bin" / ("arm-none-eabi-" + name + suffix)
    found = local if local.is_file() else shutil.which("arm-none-eabi-" + name)
    if not found:
        raise RuntimeError("Missing ARM GCC tools; run the project's dependency step first")
    return str(found)


class ArmBuild:
    FLASH = 0x10000
    FLASH_SIZE = 0x10000

    def __init__(self, sources, probe="", entry="input_reset"):
        (ROOT / ".tools").mkdir(exist_ok=True)
        self._temp = tempfile.TemporaryDirectory(prefix="arm-test-", dir=ROOT / ".tools")
        try:
            directory = Path(self._temp.name)
            script = directory / "test.ld"
            script.write_text(
                "SECTIONS { . = 0x10000; "
                ".text : { *(.text*) *(.rodata*) } "
                ".data : { *(.data*) } .bss : { *(.bss*) *(COMMON) } "
                "/DISCARD/ : { *(.comment*) *(.ARM.exidx*) *(.ARM.extab*) } }\n",
                encoding="ascii",
            )
            sources = [str(Path(source)) for source in sources]
            if probe:
                generated = directory / "probe.c"
                generated.write_text(probe, encoding="ascii")
                sources.append(str(generated))
            elf = directory / "test.elf"
            binary = directory / "test.bin"
            command = [
                _tool("gcc"), "-std=c11", "-Os", "-mcpu=cortex-m0", "-mthumb",
                "-ffreestanding", "-fno-builtin", "-fno-stack-protector",
                "-fno-unwind-tables", "-fno-asynchronous-unwind-tables",
                "-Wall", "-Wextra", "-Werror", "-nostdlib",
                "-I", str(ROOT / "src"), "-Wl,--build-id=none",
                "-Wl,-e," + entry, "-T", str(script), *sources, "-o", str(elf),
            ]
            subprocess.run(command, check=True, capture_output=True, text=True)
            subprocess.run([_tool("objcopy"), "-O", "binary", str(elf), str(binary)],
                           check=True, capture_output=True, text=True)
            output = subprocess.run([_tool("nm"), "--defined-only", str(elf)],
                                    check=True, capture_output=True, text=True).stdout
            self.symbols = {}
            for line in output.splitlines():
                columns = line.split()
                if len(columns) == 3:
                    self.symbols[columns[2]] = int(columns[0], 16)
            self.code = binary.read_bytes()
            if len(self.code) >= self.FLASH_SIZE - 0x100:
                raise RuntimeError("Test code overlaps the return sentinel")
        except subprocess.CalledProcessError as error:
            self.close()
            raise RuntimeError(error.stderr or error.stdout) from error
        except BaseException:
            self.close()
            raise

    def close(self):
        self._temp.cleanup()


class ArmMachine:
    RAM = 0x20000000
    RAM_SIZE = 0x10000

    def __init__(self, build):
        try:
            import unicorn
            from unicorn import arm_const
        except ImportError as error:
            raise RuntimeError(
                "Install emulator: python -m pip install --target .tools/python unicorn==2.1.4"
            ) from error
        self.arm = arm_const
        self.build = build
        self.uc = unicorn.Uc(unicorn.UC_ARCH_ARM, unicorn.UC_MODE_THUMB | unicorn.UC_MODE_MCLASS)
        self.uc.ctl_set_cpu_model(arm_const.UC_CPU_ARM_CORTEX_M0)
        self.uc.mem_map(build.FLASH, build.FLASH_SIZE)
        self.uc.mem_write(build.FLASH, build.code)
        self.uc.mem_map(self.RAM, self.RAM_SIZE)
        self.stop = build.FLASH + build.FLASH_SIZE - 0x100
        self.stack = self.RAM + self.RAM_SIZE - 0x1000

    def call(self, name, *arguments):
        if len(arguments) > 4:
            raise ValueError("ARM test runner supports up to four register arguments")
        arm = self.arm
        for index in range(4):
            value = arguments[index] if index < len(arguments) else 0
            self.uc.reg_write(getattr(arm, "UC_ARM_REG_R" + str(index)), value & 0xFFFFFFFF)
        saved = []
        for index in range(4, 12):
            register = getattr(arm, "UC_ARM_REG_R" + str(index))
            value = 0xA5000000 + index
            self.uc.reg_write(register, value)
            saved.append((register, value))
        self.uc.reg_write(arm.UC_ARM_REG_SP, self.stack)
        self.uc.reg_write(arm.UC_ARM_REG_LR, self.stop | 1)
        self.uc.emu_start(self.build.symbols[name] | 1, self.stop, count=200000)
        if self.uc.reg_read(arm.UC_ARM_REG_PC) != self.stop:
            raise AssertionError("ARM call did not return within its instruction budget: " + name)
        if self.uc.reg_read(arm.UC_ARM_REG_SP) != self.stack:
            raise AssertionError("ARM call corrupted the stack pointer: " + name)
        for register, value in saved:
            if self.uc.reg_read(register) != value:
                raise AssertionError("ARM call violated the callee-saved register ABI: " + name)
        return self.uc.reg_read(arm.UC_ARM_REG_R0)

    def write(self, address, data):
        self.uc.mem_write(address, bytes(data))

    def read(self, address, size):
        return bytes(self.uc.mem_read(address, size))
