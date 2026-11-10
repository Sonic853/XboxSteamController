"""Offline date release tests; GitHub publication is mocked."""

from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from tools import build, release
from tools.release_version import next_version, release_day, validate_version

COMMIT = "a" * 40
VERSION = "2026.11.10"


class VersionTests(unittest.TestCase):
    def test_beijing_midnight_and_numeric_same_day_suffix(self):
        self.assertEqual(release_day(datetime(2026, 11, 9, 16, tzinfo=timezone.utc)), VERSION)
        self.assertEqual(release_day(datetime(2026, 11, 9, 15, 59, tzinfo=timezone.utc)), "2026.11.09")
        self.assertEqual(next_version(VERSION, []), VERSION)
        self.assertEqual(next_version(VERSION, [VERSION]), VERSION + "p1")
        self.assertEqual(next_version(VERSION, [VERSION + "p9", VERSION + "p10"]), VERSION + "p11")
        self.assertEqual(next_version(VERSION, [VERSION + "p3"]), VERSION + "p4")
        self.assertEqual(next_version(VERSION, ["v" + VERSION, "2026.11.09p7", VERSION + "-rc1"]), VERSION)

    def test_invalid_dates_and_suffixes(self):
        for value in (None, "", "2026.2.10", "2026.02.29", VERSION + "p0", VERSION + "p01",
                      VERSION + "p-1", VERSION + "\n", "../" + VERSION):
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_version(value)
        self.assertEqual(validate_version("2028.02.29p12"), "2028.02.29p12")
        with self.assertRaises(ValueError):
            next_version(VERSION + "p1", [])

    def test_all_tag_and_release_pages_including_drafts_reserve_versions(self):
        with patch.object(release, "gh", side_effect=[
                json.dumps([[{"name": VERSION}], [{"name": VERSION + "p2"}]]),
                json.dumps([[{"tag_name": VERSION + "p3", "draft": True}]])]) as gh:
            self.assertEqual(next_version(VERSION, release.existing_versions("owner/repo")), VERSION + "p4")
            self.assertEqual(gh.call_count, 2)
            for call in gh.call_args_list:
                self.assertIn("--paginate", call.args)
                self.assertIn("--slurp", call.args)
        with patch.object(release, "gh", side_effect=subprocess.CalledProcessError(1, "gh")):
            with self.assertRaises(subprocess.CalledProcessError):
                release.existing_versions("owner/repo")
        for bad in ('{}', '[[{}]]', '[null]'):
            with patch.object(release, "gh", return_value=bad), self.assertRaises(ValueError):
                release.existing_versions("owner/repo")

    def test_release_build_requires_valid_version_and_commit_before_fetching_inputs(self):
        for version, commit in ((VERSION, None), (VERSION, "main"), ("2026.02.29", COMMIT)):
            with self.subTest(version=version, commit=commit), \
                    patch.object(build, "verify_bases") as verify, self.assertRaises(ValueError):
                build.build(Path("absent"), Path("absent"), Path("absent"),
                            release_version=version, source_commit=commit)
            verify.assert_not_called()


class ReleaseTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="xsc-release-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.directory = self.root / "build"
        self.directory.mkdir()
        self.output = self.directory / "release"
        (self.root / "src").mkdir()
        (self.root / "src/abi.h").write_bytes(b"// synthetic header\n")
        self.manifest = {"schema_version": 1, "project": "XboxSteamController",
                         "release_version": VERSION, "source_commit": COMMIT,
                         "softdevice_prefix_verified": True, "images": {}}
        for name in build.SOURCES:
            for path in build.source_files(name, self.root):
                path.parent.mkdir(exist_ok=True)
                path.write_bytes(f"// fixture {path.name}\n".encode("ascii"))
            filename, elf = f"{name}.bin", f"{name}-payload.elf"
            (self.directory / filename).write_bytes(name.encode("ascii") * 10)
            (self.directory / elf).write_bytes(b"\x7fELF" + name.encode("ascii"))
            self.manifest["images"][name] = {
                "filename": filename, "sha256": release.sha256(self.directory / filename),
                "size": (self.directory / filename).stat().st_size,
                "elf": {"filename": elf, "sha256": release.sha256(self.directory / elf)},
                "source_sha256": {p.relative_to(self.root).as_posix(): release.sha256(p)
                                  for p in build.source_files(name, self.root)},
            }
        self.save_manifest()

    def save_manifest(self):
        (self.directory / "manifest.json").write_text(json.dumps(self.manifest), encoding="utf-8")
        build.write_checksums(self.directory)

    def pack(self):
        return release.package(self.root, self.output, VERSION, COMMIT)

    def publish(self):
        return release.publish(self.root, self.output, VERSION, COMMIT, "owner/repo")

    def test_versioned_assets_manifest_and_download_checksums(self):
        result = self.pack()
        expected = {f"SC2015-{VERSION}-{name}" for name in build.ARTIFACTS} | {"SHA256SUMS"}
        self.assertEqual(set(result["assets"]), expected)
        for name, digest in result["assets"].items():
            self.assertEqual(release.sha256(self.output / name), digest)
        checksums = (self.output / "SHA256SUMS").read_text().splitlines()
        self.assertEqual(len(checksums), 5)
        for row in checksums:
            digest, name = row.split("  ")
            self.assertEqual(release.sha256(self.output / name), digest)
        manifest = json.loads((self.output / f"SC2015-{VERSION}-manifest.json").read_text())
        for name, image in manifest["images"].items():
            self.assertEqual(image["filename"], f"SC2015-{VERSION}-{name}.bin")
            self.assertEqual(image["elf"]["filename"], f"SC2015-{VERSION}-{name}-payload.elf")
            self.assertEqual((self.output / image["filename"]).read_bytes(),
                             (self.directory / f"{name}.bin").read_bytes())
        self.assertEqual(json.loads((self.directory / "manifest.json").read_text()), self.manifest)
        with self.assertRaises(ValueError):
            self.pack()

    def test_wrong_project_version_commit_or_incomplete_mcu_pair_fails(self):
        for field, value in (("project", "Receiver"), ("release_version", VERSION + "p1"),
                             ("source_commit", "b" * 40), ("softdevice_prefix_verified", False),
                             ("images", {"host": self.manifest["images"]["host"]})):
            old = self.manifest[field]
            self.manifest[field] = value
            self.save_manifest()
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.pack()
            self.assertFalse(self.output.exists())
            self.manifest[field] = old

    def test_changed_binary_rejected_even_if_download_checksums_are_replaced(self):
        (self.directory / "radio.bin").write_bytes(b"changed")
        with self.assertRaisesRegex(ValueError, "digest mismatch"):
            self.pack()
        build.write_checksums(self.directory)
        with self.assertRaisesRegex(ValueError, "disagrees with manifest"):
            self.pack()
        self.assertFalse(self.output.exists())

    def test_changed_sources_or_checksum_coverage_rejected(self):
        sums = self.directory / "SHA256SUMS"
        original = sums.read_bytes()
        for content in (b"", original + original, original.replace(b"host.bin", b"../host.bin")):
            sums.write_bytes(content)
            with self.assertRaises(ValueError):
                self.pack()
        sums.write_bytes(original)
        (self.root / "src/host.c").write_bytes(b"changed")
        with self.assertRaisesRegex(ValueError, "source content changed"):
            self.pack()

    def test_publish_binds_exact_commit_then_uploads_draft_before_publication(self):
        self.pack()
        with patch.object(release.subprocess, "check_output", side_effect=[COMMIT + "\n", ""]), \
                patch.object(release, "existing_versions", return_value=set()), \
                patch.object(release, "gh", return_value="release-url") as gh:
            self.assertEqual(self.publish(), "release-url")
            calls = [call.args for call in gh.call_args_list]
            self.assertEqual(calls[0][:4], ("api", "--method", "POST", "repos/owner/repo/git/refs"))
            self.assertIn("sha=" + COMMIT, calls[0])
            self.assertIn("ref=refs/tags/" + VERSION, calls[0])
            self.assertEqual(calls[1][:3], ("release", "create", VERSION))
            self.assertIn("--draft", calls[1])
            self.assertIn("--verify-tag", calls[1])
            self.assertEqual(calls[2][:3], ("release", "edit", VERSION))
            self.assertIn("--draft=false", calls[2])
            self.assertNotIn("--clobber", str(calls))
            self.assertNotIn("--force", str(calls))

    def test_collision_or_failed_upload_cannot_publish(self):
        self.pack()
        with patch.object(release.subprocess, "check_output", side_effect=[COMMIT, ""]), \
                patch.object(release, "existing_versions", return_value={VERSION}), \
                patch.object(release, "gh") as gh:
            with self.assertRaisesRegex(ValueError, "claimed"):
                self.publish()
            gh.assert_not_called()
        for effects in ([subprocess.CalledProcessError(1, "gh")],
                        ["", subprocess.CalledProcessError(1, "gh")]):
            with patch.object(release.subprocess, "check_output", side_effect=[COMMIT, ""]), \
                    patch.object(release, "existing_versions", return_value=set()), \
                    patch.object(release, "gh", side_effect=effects) as gh:
                with self.assertRaises(subprocess.CalledProcessError):
                    self.publish()
                self.assertEqual(gh.call_count, len(effects))

    def test_wrong_checkout_or_modified_package_stops_before_remote_write(self):
        self.pack()
        for checkout in (["b" * 40, ""], [COMMIT, " M src/radio.c"]):
            with patch.object(release.subprocess, "check_output", side_effect=checkout), \
                    patch.object(release, "gh") as gh:
                with self.assertRaisesRegex(ValueError, "clean selected checkout"):
                    self.publish()
                gh.assert_not_called()
        for name in ("SHA256SUMS", "RELEASE_NOTES.md"):
            path = self.output / name
            original = path.read_bytes()
            path.write_bytes(b"changed")
            with patch.object(release.subprocess, "check_output", side_effect=[COMMIT, ""]), \
                    patch.object(release, "gh") as gh:
                with self.assertRaisesRegex(ValueError, "changed"):
                    self.publish()
                gh.assert_not_called()
            path.write_bytes(original)


if __name__ == "__main__":
    unittest.main()
