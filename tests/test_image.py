import hashlib
from pathlib import Path
import struct
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from image import Edit, bl_target, patch_image, thumb_bl


class ImageTests(unittest.TestCase):
    def test_known_original_calls(self):
        # Original shipped instructions, independently saved in the research exports.
        for source, target, raw in [(0x1D43C, 0x18394, "faf7aaff"),
                                    (0x19468, 0x184F4, "fff744f8"),
                                    (0x18EFC, 0x1B960, "02f030fd")]:
            self.assertEqual(thumb_bl(source, target).hex(), raw)
            self.assertEqual(bl_target(source, bytes.fromhex(raw)), target)

    def test_branch_limits(self):
        base = 0x2000000
        for displacement in [-(1 << 24), -2, 0, 2, (1 << 24) - 2]:
            target = base + 4 + displacement
            self.assertEqual(bl_target(base, thumb_bl(base, target)), target)
        for target in [base + 3, base + 4 + (1 << 24), base + 2 - (1 << 24)]:
            with self.assertRaises(ValueError):
                thumb_bl(base, target)
        with self.assertRaises(ValueError):
            bl_target(0, struct.pack("<HH", 0xBF00, 0xBF00))

    def test_patch_integrity_and_storage_bounds(self):
        original = bytes(range(32))
        digest = hashlib.sha256(original).hexdigest()
        edit = Edit(8, original[8:12], b"abcd", "test")
        result = patch_image(original, digest, [edit], b"payload", 64, 80)
        self.assertEqual(result[:8], original[:8])
        self.assertEqual(result[8:12], b"abcd")
        self.assertEqual(result[12:32], original[12:])
        self.assertEqual(result[32:64], b"\xff" * 32)
        for kwargs in [dict(expected_hash="0" * 64), dict(edits=[edit, edit]),
                       dict(edits=[Edit(8, b"wrong", b"right", "bad baseline")]),
                       dict(payload_address=16), dict(limit=70)]:
            args = dict(original=original, expected_hash=digest, edits=[edit],
                        payload=b"payload", payload_address=64, limit=80)
            args.update(kwargs)
            with self.assertRaises(ValueError):
                patch_image(**args)


if __name__ == "__main__":
    unittest.main()
