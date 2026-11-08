#!/usr/bin/env python3
"""Build version-bound LPC/nRF payloads and apply explicit, verified binary edits."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import struct
import subprocess
import sys
import tempfile

if __package__:
    from .dependencies import BASE_IMAGES
    from .image import Edit, bl_target, patch_image, sha256, thumb_bl
else:
    from dependencies import BASE_IMAGES
    from image import Edit, bl_target, patch_image, sha256, thumb_bl


ROOT = Path(__file__).resolve().parents[1]
BASE_NAMES = {
    "host": "vcf_wired_controller_d0g_5b0f21bd.bin",
    "radio": "vcf_wired_controller_d0g_5a0e3f348_radio.bin",
    "softdevice": "s110_nrf51_8.0.0_softdevice.bin",
}
SOURCES = {
    "host": ("host.c", "bond_store.c", "mapping.c", "runtime.c"),
    "radio": ("radio.c", "radio_config.c", "input.c", "mapping.c", "runtime.c"),
}
CFLAGS = (
    "-mcpu=cortex-m0", "-mthumb", "-Os", "-ffreestanding", "-fno-builtin",
    "-fno-unwind-tables", "-fno-asynchronous-unwind-tables",
    "-ffunction-sections", "-fdata-sections", "-Wall", "-Wextra", "-Werror",
    "-nostdlib",
)
ARTIFACTS = ("host.bin", "radio.bin", "host-payload.elf", "radio-payload.elf", "manifest.json")
MANDATORY_PROTECTION = {
    "host": [(0, 0x2000), (0x1E000, 0x20000)],
    # The single length field is only accessible through image_end_minus.
    "radio": [(0, 0x18010), (0x18014, 0x180C0), (0x3F800, 0x40000)],
}
LIMITS = {
    "host": (0x14000, 0x1E000, 0x10000000, 0x10002000),
    "radio": (0x20000, 0x3F800, 0x20003E28, 0x20004000),
}


def number(value, field="address"):
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise ValueError(f"{field} must be an integer or hexadecimal string")
    try:
        result = int(value, 0) if isinstance(value, str) else value
    except ValueError as error:
        raise ValueError(f"Invalid {field}: {value!r}") from error
    if not 0 <= result <= 0xFFFFFFFF:
        raise ValueError(f"{field} is outside the unsigned 32-bit range")
    return result


def read_config(path, name):
    config = json.loads(path.read_text(encoding="utf-8"))
    if (not isinstance(config, dict) or type(config.get("schema_version")) is not int
            or config.get("schema_version") != 1 or config.get("name") != name):
        raise ValueError(f"{path}: expected schema_version 1 and name {name}")
    base = config["base"]
    if base != {"filename": BASE_NAMES[name], "sha256": BASE_IMAGES[BASE_NAMES[name]]}:
        raise ValueError(f"{path}: base filename and SHA-256 must match the pinned {name} image")
    origin = number(config["payload"]["origin"], "payload.origin")
    limit = number(config["payload"]["limit"], "payload.limit")
    ram_start = number(config["ram"]["origin"], "ram.origin")
    ram_size = number(config["ram"]["size"], "ram.size")
    min_flash, max_flash, min_ram, max_ram = LIMITS[name]
    if not min_flash <= origin < limit <= max_flash or origin % 4 or limit % 4:
        raise ValueError(f"{name}: invalid payload range or four-byte alignment")
    if not min_ram <= ram_start < ram_start + ram_size <= max_ram:
        raise ValueError(f"{name}: RAM reservation exceeds the permitted hardware range")
    if not isinstance(config["hooks"], list) or not config["hooks"]:
        raise ValueError(f"{name}: hooks must be a nonempty list")
    if name == "radio":
        length_hooks = [h for h in config["hooks"] if h.get("kind") == "image_end_minus"]
        if len(length_hooks) != 1 or number(length_hooks[0]["address"]) != 0x18010:
            raise ValueError("radio requires exactly one image_end_minus hook at 0x18010")
        if number(length_hooks[0]["base"]) != 0x18000:
            raise ValueError("radio application-length base must be 0x18000")
    return config


def verify_bases(base_dir):
    images = {}
    for name, filename in BASE_NAMES.items():
        data = (base_dir / filename).read_bytes()
        if sha256(data) != BASE_IMAGES[filename]:
            raise ValueError(f"Base firmware SHA-256 mismatch: {filename}")
        images[name] = data
    if images["radio"][:len(images["softdevice"])] != images["softdevice"]:
        raise ValueError("Radio image does not contain the exact S110 SoftDevice prefix")
    return images


def find_tools(bin_dir):
    result = {}
    for name in ("gcc", "nm", "objdump", "objcopy"):
        basename = "arm-none-eabi-" + name
        candidates = (bin_dir / (basename + ".exe"), bin_dir / basename) if os.name == "nt" else (
            bin_dir / basename,
        )
        executable = next((p for p in candidates if p.is_file()), None)
        if executable is None:
            raise ValueError(f"Missing tool: {bin_dir / basename}")
        result[name] = executable.resolve()
    return result


def run(command):
    result = subprocess.run(
        [str(argument) for argument in command], cwd=ROOT, check=False,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8",
        errors="replace", env={**os.environ, "LC_ALL": "C"},
    )
    if result.returncode:
        raise ValueError(f"Command failed ({result.returncode}): {' '.join(map(str, command))}\n"
                         f"{result.stdout}{result.stderr}")
    return result.stdout


def parse_symbols(text):
    symbols = {}
    for line in text.splitlines():
        match = re.fullmatch(r"\s*([0-9a-fA-F]+)\s+([A-Za-z?])\s+(\S+)\s*", line)
        if not match:
            if line.strip():
                raise ValueError(f"Unrecognized nm output: {line}")
            continue
        address, kind, name = match.groups()
        if name in symbols:
            raise ValueError(f"Ambiguous ELF symbol: {name}")
        symbols[name] = {"address": int(address, 16), "type": kind}
    return symbols


def parse_sections(text):
    sections = []
    lines = text.splitlines()
    for index, line in enumerate(lines):
        match = re.match(
            r"\s*\d+\s+(\S+)\s+([0-9a-fA-F]+)\s+([0-9a-fA-F]+)\s+"
            r"([0-9a-fA-F]+)\s+([0-9a-fA-F]+)\s+2\*\*\d+", line,
        )
        if match:
            name, size, vma, lma, offset = match.groups()
            if index + 1 == len(lines):
                raise ValueError(f"ELF section flags missing for {name}")
            flags = [flag.strip() for flag in lines[index + 1].split(",")]
            sections.append({"name": name, "size": int(size, 16), "vma": int(vma, 16),
                             "lma": int(lma, 16), "file_offset": int(offset, 16), "flags": flags})
    if not sections:
        raise ValueError("No ELF sections found in objdump output")
    return sections


def validate_layout(config, sections, payload):
    origin = number(config["payload"]["origin"])
    limit = number(config["payload"]["limit"])
    ram_start = number(config["ram"]["origin"])
    ram_end = ram_start + number(config["ram"]["size"])
    text = [s for s in sections if s["name"] == ".text"]
    if len(text) != 1 or not payload:
        raise ValueError("ELF must have exactly one nonempty .text payload section")
    text = text[0]
    if (text["vma"] != origin or text["lma"] != origin or text["size"] != len(payload)
            or not {"CONTENTS", "ALLOC", "LOAD", "CODE"}.issubset(text["flags"])):
        raise ValueError("ELF .text address/size/flags disagree with the payload configuration")
    if origin + len(payload) + (-(origin + len(payload)) % 4) > limit:
        raise ValueError("Four-byte-aligned payload exceeds its flash limit")
    ram_sections = []
    for section in sections:
        if not section["size"] or "ALLOC" not in section["flags"] or section["name"] == ".text":
            continue
        if "LOAD" in section["flags"] or "CONTENTS" in section["flags"]:
            raise ValueError(f"Unexpected initialized ELF section: {section['name']}; put constants in .text")
        if not ram_start <= section["vma"] < section["vma"] + section["size"] <= ram_end:
            raise ValueError(f"NOLOAD section exceeds reserved RAM: {section['name']}")
        ram_sections.append(section)
    return ram_sections


def symbol_address(symbols, name, config, function=False):
    if name not in symbols:
        raise ValueError(f"Missing hook symbol: {name}")
    symbol = symbols[name]
    address = symbol["address"]
    if function:
        if symbol["type"] not in {"t", "T", "w", "W"}:
            raise ValueError(f"Hook symbol is not a function: {name}")
        address &= ~1
    start = number(config["payload"]["origin"])
    end = number(config["payload"]["limit"])
    if not start <= address < end:
        raise ValueError(f"Hook symbol is outside the payload flash range: {name}")
    return address


def make_edits(config, original, symbols, image_end):
    edits, records = [], []
    for hook in config["hooks"]:
        kind = hook["kind"]
        address = number(hook["address"])
        purpose = hook["purpose"]
        if not isinstance(purpose, str) or not purpose.strip():
            raise ValueError("Every hook needs a nonempty purpose")
        if config["name"] == "radio" and address < 0x18014 and address + 4 > 0x18010:
            if kind != "image_end_minus" or address != 0x18010:
                raise ValueError("Radio vector length field permits only image_end_minus")
        record = {"kind": kind, "address": hex(address), "purpose": purpose, "symbol": hook.get("symbol")}
        if kind == "bl":
            target = number(hook["expected_target"])
            before = thumb_bl(address, target)
            actual = original[address:address + 4]
            if len(actual) != 4 or bl_target(address, actual) != target:
                raise ValueError(f"Original BL target differs at {address:#x}")
            replacement = symbol_address(symbols, hook["symbol"], config, function=True)
            after = thumb_bl(address, replacement)
            record.update(expected_target=hex(target), target=hex(replacement))
        elif kind == "literal":
            thumb = hook.get("thumb", False)
            if not isinstance(thumb, bool):
                raise ValueError("literal.thumb must be boolean")
            before = struct.pack("<I", number(hook["expected"]))
            replacement = symbol_address(symbols, hook["symbol"], config, function=thumb)
            if thumb:
                replacement |= 1
            after = struct.pack("<I", replacement)
            record.update(target=hex(replacement), thumb=thumb)
        elif kind == "exact":
            before, after = bytes.fromhex(hook["before"]), bytes.fromhex(hook["after"])
        elif kind == "image_end_minus":
            base = number(hook["base"])
            expected = number(hook["expected"])
            if expected != len(original) - base or not 0 <= image_end - base <= 0xFFFFFFFF:
                raise ValueError("Inferred length field does not match original image length minus base")
            before, after = struct.pack("<I", expected), struct.pack("<I", image_end - base)
            record.update(base=hex(base), value=image_end - base, confidence="inferred",
                          evidence="Original field equals original image length minus application base; not execution-verified")
        else:
            raise ValueError(f"Unsupported hook kind: {kind}")
        record.update(before=before.hex(), after=after.hex(), size=len(before))
        if hook.get("symbol"):
            record["symbol_address"] = hex(symbols[hook["symbol"]]["address"])
        edits.append(Edit(address, before, after, purpose))
        records.append(record)
    return edits, records


def apply_payload(config, original, payload, symbols):
    origin = number(config["payload"]["origin"])
    padded_payload = payload + b"\xff" * (-(origin + len(payload)) % 4)
    image_end = origin + len(padded_payload)
    edits, records = make_edits(config, original, symbols, image_end)
    protected = list(MANDATORY_PROTECTION[config["name"]])
    for region in config.get("protected_ranges", []):
        protected.append((number(region["start"]), number(region["end"])))
    result = patch_image(original, config["base"]["sha256"], edits, padded_payload, origin,
                         number(config["payload"]["limit"]), protected_ranges=protected)
    # Audit every unchanged interval instead of relying only on the patch writer.
    cursor = 0
    for edit in sorted(edits, key=lambda item: item.offset):
        if result[cursor:edit.offset] != original[cursor:edit.offset]:
            raise ValueError("Unexpected change outside declared hooks")
        cursor = edit.offset + len(edit.after)
    if result[cursor:len(original)] != original[cursor:]:
        raise ValueError("Unexpected change after declared hooks")
    if result[len(original):origin] != b"\xff" * (origin - len(original)):
        raise ValueError("Image extension must be filled with FF")
    return result, records, protected


def build_payload(name, config, tools, staging):
    elf = staging / f"{name}-payload.elf"
    payload_file = staging / f"{name}-payload.bin"
    sources = ["src/" + filename for filename in SOURCES[name]]
    link_flags = ["-T", f"src/{name}.ld", "-Wl,--build-id=none"]
    # Only compiler arithmetic helpers; no libc, startup objects or SDK runtime.
    run([tools["gcc"], *CFLAGS, *link_flags, *sources, "-lgcc", "-o", elf])
    undefined = run([tools["nm"], "--undefined-only", elf]).strip()
    if undefined:
        raise ValueError(f"Undefined symbols remain in {name} payload: {undefined}")
    symbols = parse_symbols(run([tools["nm"], "--extern-only", "--defined-only", "--numeric-sort", elf]))
    sections = parse_sections(run([tools["objdump"], "-h", elf]))
    run([tools["objcopy"], "-O", "binary", "--only-section=.text", elf, payload_file])
    payload = payload_file.read_bytes()
    ram_sections = validate_layout(config, sections, payload)
    return payload, symbols, sections, ram_sections


def write_checksums(directory):
    lines = [f"{sha256((directory / name).read_bytes())}  {name}\n" for name in sorted(ARTIFACTS)]
    (directory / "SHA256SUMS").write_text("".join(lines), encoding="ascii", newline="\n")


def build(base_dir, toolchain_bin, output):
    bases = verify_bases(base_dir)
    configs = {name: read_config(ROOT / "config" / f"{name}-patches.json", name)
               for name in SOURCES}
    tools = find_tools(toolchain_bin)
    version = run([tools["gcc"], "-dumpfullversion"]).strip()
    if version != "9.2.1":
        raise ValueError(f"Expected pinned GCC 9.2.1 (9-2019q4), got {version}")
    manifest = {
        "schema_version": 1, "project": "XboxSteamController", "experimental": True,
        "hardware_tested": False, "compiler": run([tools["gcc"], "--version"]).splitlines()[0],
        "compiler_version": version, "compile_flags": list(CFLAGS),
        "link_flags": ["--build-id=none", "-lgcc"], "section_gc": False,
        "tool_sha256": {name: sha256(path.read_bytes()) for name, path in tools.items()},
        "base_images": {key: {"filename": BASE_NAMES[key], "sha256": sha256(data), "size": len(data)}
                        for key, data in bases.items()},
        "softdevice_prefix_verified": True,
        "hardware_assumptions": {
            "host_flash_bytes": 0x20000, "radio": "nRF51822, 256 KiB Flash / 16 KiB RAM",
            "radio_ficr_read": False, "radio_reserved_pages": ["0x3f800", "0x3fc00"],
        },
        "images": {},
    }
    output = output.resolve()
    if output == ROOT or output in ROOT.parents or output in [ROOT / d for d in ("src", "tools", "tests", "config", "docs")]:
        raise ValueError("Output must be a dedicated build directory")
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="xsc-build-", dir=output.parent) as temporary:
        staging = Path(temporary)
        for name, config in configs.items():
            payload, symbols, sections, ram_sections = build_payload(name, config, tools, staging)
            # A symbol at/after the actual payload end must not pass a broad flash-limit check.
            payload_end = number(config["payload"]["origin"]) + len(payload)
            for hook in config["hooks"]:
                if hook.get("symbol") and hook["symbol"] in symbols:
                    if (symbols[hook["symbol"]]["address"] & ~1) >= payload_end:
                        raise ValueError(f"Hook symbol lies beyond emitted payload: {hook['symbol']}")
            image, hooks, protected = apply_payload(config, bases[name], payload, symbols)
            if name == "radio" and image[:len(bases["softdevice"])] != bases["softdevice"]:
                raise ValueError("Patched image changed the SoftDevice")
            (staging / f"{name}.bin").write_bytes(image)
            source_files = [ROOT / "src" / filename for filename in SOURCES[name]]
            source_files += sorted((ROOT / "src").glob("*.h"))
            source_files += [ROOT / "src" / f"{name}.ld", ROOT / "config" / f"{name}-patches.json"]
            manifest["images"][name] = {
                "filename": f"{name}.bin", "sha256": sha256(image), "size": len(image),
                "base": manifest["base_images"][name],
                "elf": {"filename": f"{name}-payload.elf",
                        "sha256": sha256((staging / f"{name}-payload.elf").read_bytes())},
                "payload": {"origin": hex(number(config["payload"]["origin"])),
                            "code_end": hex(payload_end), "image_end": hex(len(image)),
                            "limit": hex(number(config["payload"]["limit"])),
                            "code_bytes": len(payload), "padding_bytes": len(image) - payload_end,
                            "sha256": sha256(payload)},
                "ram": {"origin": hex(number(config["ram"]["origin"])),
                        "size": number(config["ram"]["size"]),
                        "allocated_bytes": sum(s["size"] for s in ram_sections),
                        "sections": ram_sections, "initialization": "Payload runtime must initialize NOLOAD state"},
                "protected_ranges": [{"start": hex(a), "end": hex(b)} for a, b in protected],
                "hooks": hooks, "sections": sections,
                "source_sha256": {p.relative_to(ROOT).as_posix(): sha256(p.read_bytes()) for p in source_files},
            }
            print(f"{name}: {len(payload)} payload bytes, {len(image)} image bytes, "
                  f"{len(hooks)} verified edits")
        (staging / "manifest.json").write_text(
            json.dumps(manifest, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
            encoding="utf-8", newline="\n",
        )
        write_checksums(staging)
        # Compilation and validation of BOTH MCUs finish before publishing any artifact.
        output.mkdir(parents=True, exist_ok=True)
        for filename in (*ARTIFACTS, "SHA256SUMS"):
            (staging / filename).replace(output / filename)
    return manifest


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-dir", type=Path, default=ROOT / ".tools" / "base")
    parser.add_argument("--toolchain-bin", type=Path, default=ROOT / ".tools" / "gcc" / "bin")
    parser.add_argument("--output", type=Path, default=ROOT / "build")
    args = parser.parse_args(argv)
    try:
        build(args.base_dir.resolve(), args.toolchain_bin.resolve(), args.output)
    except (OSError, ValueError, KeyError, TypeError, struct.error) as error:
        print(f"Build failed: {error}", file=sys.stderr)
        return 1
    print(f"Build artifacts: {args.output.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
