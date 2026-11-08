"""Patch-contract tests plus a real dual-MCU build when pinned inputs are available."""

import contextlib
import copy
import io
import json
import os
from pathlib import Path
import shutil
import struct
import tempfile
import unittest

from tools import build
from tools.image import Edit, bl_target, patch_image, sha256, thumb_bl


def fixture_config(name="host"):
    return {
        "schema_version": 1, "name": name,
        "base": {"filename": build.BASE_NAMES[name],
                 "sha256": build.BASE_IMAGES[build.BASE_NAMES[name]]},
        "payload": {"origin": "0x14000" if name == "host" else "0x20000",
                    "limit": "0x1e000" if name == "host" else "0x3f800"},
        "ram": {"origin": "0x10001fd0" if name == "host" else "0x20003e28",
                "size": 16 if name == "host" else 472},
        "hooks": [], "protected_ranges": [],
    }


class BuildContractTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def test_negative_and_out_of_image_edits_are_rejected(self):
        original = b"0123456789abcdef"
        for offset in (-4, 15, 16, 100):
            with self.subTest(offset=offset), self.assertRaisesRegex(ValueError, "outside original"):
                patch_image(original, sha256(original), [Edit(offset, b"ef", b"xx", "bad")],
                            b"code", 32, 64)

    def test_protected_regions_and_flash_limits_are_enforced(self):
        original = b"original"
        args = dict(original=original, expected_hash=sha256(original),
                    edits=[Edit(0, b"or", b"OR", "protected")],
                    payload=b"code", payload_address=16, limit=32)
        with self.assertRaisesRegex(ValueError, "protected"):
            patch_image(**args, protected_ranges=[(0, 4)])
        with self.assertRaisesRegex(ValueError, "protected"):
            patch_image(**{**args, "edits": []}, protected_ranges=[(18, 22)])
        with self.assertRaisesRegex(ValueError, "protected"):
            patch_image(**{**args, "edits": []}, protected_ranges=[(10, 12)])
        for limit in (19, 0x100000001):
            with self.subTest(limit=limit), self.assertRaises(ValueError):
                patch_image(**{**args, "limit": limit})

    def test_known_bl_literal_and_exact_edits_preserve_all_other_bytes(self):
        original = bytearray(b"\xff" * 0x2400)
        original[0x2100:0x2104] = thumb_bl(0x2100, 0x2200)
        struct.pack_into("<I", original, 0x2104, 0x2201)
        original[0x2110:0x2112] = b"\x11\x22"
        original = bytes(original)
        config = fixture_config()
        config["base"]["sha256"] = sha256(original)
        config["hooks"] = [
            {"kind": "bl", "address": "0x2100", "expected_target": "0x2200",
             "symbol": "wrapper", "purpose": "call wrapper"},
            {"kind": "literal", "address": "0x2104", "expected": "0x2201",
             "symbol": "wrapper", "thumb": True, "purpose": "callback wrapper"},
            {"kind": "exact", "address": "0x2110", "before": "1122", "after": "3344",
             "purpose": "version-bound edit"},
        ]
        symbols = {"wrapper": {"address": 0x14004, "type": "T"}}
        result, records, _ = build.apply_payload(config, original, b"payload-code!", symbols)
        self.assertEqual(bl_target(0x2100, result[0x2100:0x2104]), 0x14004)
        self.assertEqual(struct.unpack_from("<I", result, 0x2104)[0], 0x14005)
        self.assertEqual(result[0x2110:0x2112], b"\x33\x44")
        allowed = set(range(0x2100, 0x2108)) | {0x2110, 0x2111}
        differences = {i for i, (before, after) in enumerate(zip(original, result)) if before != after}
        self.assertLessEqual(differences, allowed)
        self.assertEqual(result[len(original):0x14000], b"\xff" * (0x14000 - len(original)))
        self.assertEqual(records[0]["symbol"], "wrapper")
        self.assertEqual(records[1]["after"], "05400100")

    def test_wrong_original_bl_target_cannot_be_patched(self):
        config = fixture_config()
        config["hooks"] = [{"kind": "bl", "address": 0, "expected_target": 12,
                            "symbol": "wrapper", "purpose": "wrong base call"}]
        with self.assertRaisesRegex(ValueError, "Original BL target differs"):
            build.make_edits(config, thumb_bl(0, 8), {"wrapper": {"address": 0x14000, "type": "T"}}, 0x14004)

    def test_byte_mismatch_rejects_literal_and_exact(self):
        config = fixture_config()
        original = b"\xff" * 0x2400
        config["base"]["sha256"] = sha256(original)
        for hook in (
            {"kind": "literal", "expected": 42, "symbol": "wrapper", "thumb": True},
            {"kind": "exact", "before": "aabb", "after": "ccdd"},
        ):
            config["hooks"] = [{**hook, "address": "0x2100", "purpose": "wrong bytes"}]
            with self.subTest(kind=hook["kind"]), self.assertRaisesRegex(ValueError, "Original bytes differ"):
                build.apply_payload(config, original, b"code", {"wrapper": {"address": 0x14000, "type": "T"}})

    def test_dynamic_radio_length_uses_final_four_byte_alignment(self):
        config = fixture_config("radio")
        original = bytearray(b"\xff" * 0x18100)
        struct.pack_into("<I", original, 0x18010, 0x100)
        original = bytes(original)
        config["base"]["sha256"] = sha256(original)
        config["hooks"] = [{"kind": "image_end_minus", "address": "0x18010", "expected": "0x100",
                            "base": "0x18000", "purpose": "inferred app-length field"}]
        result, records, _ = build.apply_payload(config, original, b"abc", {})
        self.assertEqual(len(result), 0x20004)
        self.assertEqual(result[-4:], b"abc\xff")
        self.assertEqual(struct.unpack_from("<I", result, 0x18010)[0], len(result) - 0x18000)
        self.assertEqual(records[0]["confidence"], "inferred")
        self.assertEqual(result[:0x18010], original[:0x18010])
        self.assertEqual(result[0x18014:len(original)], original[0x18014:])
        config["hooks"][0]["expected"] = 0x104
        with self.assertRaisesRegex(ValueError, "does not match original"):
            build.apply_payload(config, original, b"abc", {})

    def test_radio_length_field_cannot_be_changed_by_an_exact_edit(self):
        config = fixture_config("radio")
        config["hooks"] = [{"kind": "exact", "address": "0x18010", "before": "14700000",
                            "after": "00000100", "purpose": "bypass inferred length contract"}]
        with self.assertRaisesRegex(ValueError, "only image_end_minus"):
            build.make_edits(config, b"", {}, 0x20000)

    def test_config_refuses_wrong_identity_or_physical_limits(self):
        config = fixture_config()
        config["hooks"] = [{"kind": "exact", "address": "0x2100", "before": "00",
                            "after": "01", "purpose": "test"}]
        path = self.root / "host-patches.json"
        path.write_text(json.dumps(config), encoding="utf-8")
        self.assertEqual(build.read_config(path, "host"), config)
        variants = []
        bad = copy.deepcopy(config)
        bad["base"]["sha256"] = "0" * 64
        variants.append(bad)
        bad = copy.deepcopy(config)
        bad["payload"]["limit"] = "0x20000"
        variants.append(bad)
        bad = copy.deepcopy(config)
        bad["ram"]["size"] = 4096
        variants.append(bad)
        for bad in variants:
            path.write_text(json.dumps(bad), encoding="utf-8")
            with self.subTest(config=bad), self.assertRaises(ValueError):
                build.read_config(path, "host")

    def test_nm_symbol_contract_and_thumb_address_resolution(self):
        symbols = build.parse_symbols("00014004 T wrapper\n00014020 R report_map\n10001fd0 B state\n")
        self.assertEqual(build.symbol_address(symbols, "wrapper", fixture_config(), function=True), 0x14004)
        for name in ("missing", "state", "report_map"):
            with self.subTest(name=name), self.assertRaises(ValueError):
                build.symbol_address(symbols, name, fixture_config(), function=True)
        with self.assertRaisesRegex(ValueError, "Ambiguous"):
            build.parse_symbols("00014000 T wrapper\n00014010 T wrapper\n")

    def test_layout_rejects_initialized_ram_overflow_and_wrong_text_origin(self):
        sections = build.parse_sections("""
Idx Name          Size      VMA       LMA       File off  Algn
  0 .text         00000004  00014000  00014000  00010000  2**2
                  CONTENTS, ALLOC, LOAD, READONLY, CODE
  1 .bss          00000008  10001fd0  10001fd0  00011fd0  2**2
                  ALLOC
""")
        config = fixture_config()
        self.assertEqual(build.validate_layout(config, sections, b"code"), [sections[1]])
        for field, value in (("size", 17), ("flags", ["ALLOC", "LOAD", "CONTENTS"])):
            bad = copy.deepcopy(sections)
            bad[1][field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                build.validate_layout(config, bad, b"code")
        bad = copy.deepcopy(sections)
        bad[0]["vma"] = 0x14004
        with self.assertRaisesRegex(ValueError, "disagree"):
            build.validate_layout(config, bad, b"code")

    def test_checksums_cover_every_deliverable_except_themselves(self):
        for name in build.ARTIFACTS:
            (self.root / name).write_bytes(name.encode("ascii"))
        build.write_checksums(self.root)
        lines = (self.root / "SHA256SUMS").read_text(encoding="ascii").splitlines()
        self.assertEqual(len(lines), 5)
        for line in lines:
            checksum, name = line.split("  ")
            self.assertEqual(checksum, sha256((self.root / name).read_bytes()))
            self.assertNotEqual(name, "SHA256SUMS")


class RealBuildTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.base_dir = build.ROOT / ".tools/base"
        cls.bin_dir = build.ROOT / ".tools/gcc/bin"
        required = [cls.base_dir / filename for filename in build.BASE_NAMES.values()]
        required += [build.ROOT / "config" / f"{name}-patches.json" for name in build.SOURCES]
        required += [build.ROOT / "src" / f for sources in build.SOURCES.values() for f in sources]
        required += [build.ROOT / "src" / f"{name}.ld" for name in build.SOURCES]
        missing = [str(path) for path in required if not path.is_file()]
        try:
            build.find_tools(cls.bin_dir)
        except ValueError as error:
            missing.append(str(error))
        if missing:
            message = "Real ARM build prerequisites missing: " + ", ".join(missing)
            if os.environ.get("XSC_REQUIRE_ARM_TESTS") == "1":
                raise RuntimeError(message)
            raise unittest.SkipTest(message)
        cls.temporary = tempfile.TemporaryDirectory(prefix="build-test-", dir=build.ROOT / ".tools")
        cls.addClassCleanup(cls.temporary.cleanup)
        cls.output = Path(cls.temporary.name) / "output"
        with contextlib.redirect_stdout(io.StringIO()):
            cls.manifest = build.build(cls.base_dir, cls.bin_dir, cls.output)

    def test_final_images_only_change_declared_hooks_and_appended_payload(self):
        for name in build.SOURCES:
            with self.subTest(mcu=name):
                original = (self.base_dir / build.BASE_NAMES[name]).read_bytes()
                result = (self.output / f"{name}.bin").read_bytes()
                metadata = self.manifest["images"][name]
                allowed = set()
                for hook in metadata["hooks"]:
                    start = int(hook["address"], 16)
                    end = start + hook["size"]
                    self.assertEqual(original[start:end].hex(), hook["before"])
                    self.assertEqual(result[start:end].hex(), hook["after"])
                    allowed.update(range(start, end))
                    if hook["kind"] == "bl":
                        self.assertEqual(bl_target(start, result[start:end]), int(hook["target"], 16))
                changes = {i for i, (a, b) in enumerate(zip(original, result)) if a != b}
                self.assertLessEqual(changes, allowed)
                origin = int(metadata["payload"]["origin"], 16)
                self.assertEqual(result[len(original):origin], b"\xff" * (origin - len(original)))
                self.assertEqual(len(result) % 4, 0)
                self.assertLessEqual(len(result), int(metadata["payload"]["limit"], 16))
                self.assertEqual(sha256(result), metadata["sha256"])
                end = int(metadata["payload"]["code_end"], 16)
                self.assertEqual(sha256(result[origin:end]), metadata["payload"]["sha256"])
                self.assertEqual(result[end:], b"\xff" * metadata["payload"]["padding_bytes"])
        radio = (self.output / "radio.bin").read_bytes()
        softdevice = (self.base_dir / build.BASE_NAMES["softdevice"]).read_bytes()
        self.assertEqual(radio[:len(softdevice)], softdevice)
        self.assertEqual(struct.unpack_from("<I", radio, 0x18010)[0], len(radio) - 0x18000)

    def test_manifest_and_checksum_attachment_contract(self):
        self.assertEqual({p.name for p in self.output.iterdir()}, {*build.ARTIFACTS, "SHA256SUMS"})
        self.assertTrue(self.manifest["experimental"])
        self.assertFalse(self.manifest["hardware_tested"])
        self.assertFalse(self.manifest["hardware_assumptions"]["radio_ficr_read"])
        self.assertEqual(json.loads((self.output / "manifest.json").read_text(encoding="utf-8")), self.manifest)
        for line in (self.output / "SHA256SUMS").read_text(encoding="ascii").splitlines():
            checksum, filename = line.split("  ")
            self.assertEqual(checksum, sha256((self.output / filename).read_bytes()))

    def test_corrupted_input_does_not_publish_or_replace_output(self):
        corrupted = Path(self.temporary.name) / "corrupted-base"
        shutil.copytree(self.base_dir, corrupted)
        path = corrupted / build.BASE_NAMES["host"]
        data = bytearray(path.read_bytes())
        data[-1] ^= 1
        path.write_bytes(data)
        before = {p.name: p.read_bytes() for p in self.output.iterdir()}
        with self.assertRaisesRegex(ValueError, "SHA-256 mismatch"):
            build.build(corrupted, self.bin_dir, self.output)
        self.assertEqual({p.name: p.read_bytes() for p in self.output.iterdir()}, before)


if __name__ == "__main__":
    unittest.main()
