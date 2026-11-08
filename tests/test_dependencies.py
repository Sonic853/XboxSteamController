"""Dependency integrity and archive-handling tests; no network or compiler needed."""

import hashlib
import io
from pathlib import Path
import stat
import tarfile
import tempfile
import unittest
from unittest.mock import patch
import warnings
import zipfile

from tools import dependencies as deps


def digest(data):
    return hashlib.sha256(data).hexdigest()


class Response(io.BytesIO):
    def geturl(self):
        return "https://example.test/archive.zip"


class DependencyTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def firmware_zip(self, payloads):
        archive = self.root / "firmware.zip"
        with zipfile.ZipFile(archive, "w") as package:
            for name, data in payloads.items():
                package.writestr(deps.FIRMWARE_PREFIX + name, data)
            package.writestr("ValveFirmwareUpdateApp.exe", b"must not be extracted")
            package.writestr("../outside.txt", b"must not be extracted")
        return archive

    def test_download_is_published_only_after_verification(self):
        destination = self.root / "downloads" / "archive.zip"
        with patch.object(deps, "urlopen", return_value=Response(b"archive")):
            deps.download_verified("https://example.test/a", destination, digest(b"archive"))
        self.assertEqual(destination.read_bytes(), b"archive")
        with patch.object(deps, "urlopen", side_effect=AssertionError("network called")):
            deps.download_verified("https://example.test/a", destination, digest(b"archive"))

    def test_bad_download_leaves_no_archive_or_partial_file(self):
        destination = self.root / "archive.zip"
        with patch.object(deps, "urlopen", return_value=Response(b"wrong")):
            with self.assertRaisesRegex(ValueError, "SHA-256 mismatch"):
                deps.download_verified("https://example.test/a", destination, digest(b"expected"))
        self.assertEqual(list(self.root.iterdir()), [])

    def test_corrupted_cache_is_not_trusted(self):
        destination = self.root / "archive.zip"
        destination.write_bytes(b"modified")
        with patch.object(deps, "urlopen", side_effect=AssertionError("network called")):
            with self.assertRaisesRegex(ValueError, "SHA-256 mismatch"):
                deps.download_verified("https://example.test/a", destination, digest(b"expected"))

    def test_non_https_download_is_rejected(self):
        for url in ("http://example.test/a", "file:///tmp/a", "https:///missing-host"):
            with self.subTest(url=url), self.assertRaisesRegex(ValueError, "HTTPS"):
                deps.download_verified(url, self.root / "a", digest(b"a"))

    def test_only_pinned_firmware_members_are_written(self):
        images = {"host.bin": b"host", "radio.bin": b"radio", "sd.bin": b"softdevice"}
        archive = self.firmware_zip(images)
        expected = {name: digest(data) for name, data in images.items()}
        with patch.object(deps, "BASE_IMAGES", expected), patch.object(
            deps, "FIRMWARE_ARCHIVE_SHA256", digest(archive.read_bytes())
        ):
            deps.prepare_base(self.root / "base", archive=archive)
        self.assertEqual({p.name: p.read_bytes() for p in (self.root / "base").iterdir()}, images)
        self.assertFalse((self.root / "outside.txt").exists())
        self.assertFalse((self.root / "ValveFirmwareUpdateApp.exe").exists())

    def test_bad_firmware_member_preserves_existing_output(self):
        source = self.root / "source"
        source.mkdir()
        (source / "host.bin").write_bytes(b"valid-host")
        (source / "radio.bin").write_bytes(b"wrong-radio")
        output = self.root / "base"
        output.mkdir()
        (output / "host.bin").write_bytes(b"previous")
        expected = {"host.bin": digest(b"valid-host"), "radio.bin": digest(b"valid-radio")}
        with patch.object(deps, "BASE_IMAGES", expected):
            with self.assertRaisesRegex(ValueError, "SHA-256 mismatch"):
                deps.prepare_base(output, source=source)
        self.assertEqual((output / "host.bin").read_bytes(), b"previous")
        self.assertFalse((output / "radio.bin").exists())

    def test_firmware_archive_hash_checked_before_extraction(self):
        archive = self.firmware_zip({"host.bin": b"host"})
        with self.assertRaisesRegex(ValueError, "SHA-256 mismatch"):
            deps.prepare_base(self.root / "base", archive=archive)
        self.assertFalse((self.root / "base").exists())

    def test_duplicate_firmware_member_is_rejected(self):
        archive = self.firmware_zip({"host.bin": b"host"})
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            with zipfile.ZipFile(archive, "a") as package:
                package.writestr(deps.FIRMWARE_PREFIX + "host.bin", b"host")
        with patch.object(deps, "BASE_IMAGES", {"host.bin": digest(b"host")}), patch.object(
            deps, "FIRMWARE_ARCHIVE_SHA256", digest(archive.read_bytes())
        ):
            with self.assertRaisesRegex(ValueError, "duplicate"):
                deps.prepare_base(self.root / "base", archive=archive)

    def test_toolchain_zip_rejects_traversal_and_symlinks(self):
        for name in ("../outside", "/absolute", "C:/absolute", "dir\\..\\outside", "link"):
            with self.subTest(name=name):
                archive = self.root / "unsafe.zip"
                with zipfile.ZipFile(archive, "w") as package:
                    member = zipfile.ZipInfo(name)
                    if name == "link":
                        member.external_attr = (stat.S_IFLNK | 0o777) << 16
                    package.writestr(member, b"target")
                with self.assertRaisesRegex(ValueError, "Unsafe ZIP"):
                    deps.unpack_toolchain(archive, self.root / "stage")

    def test_toolchain_tar_rejects_escape(self):
        archive = self.root / "unsafe.tar.bz2"
        with tarfile.open(archive, "w:bz2") as package:
            member = tarfile.TarInfo("../outside")
            member.size = 4
            package.addfile(member, io.BytesIO(b"data"))
        with self.assertRaises(tarfile.FilterError):
            deps.unpack_toolchain(archive, self.root / "stage")
        self.assertFalse((self.root / "outside").exists())

    def test_toolchain_tar_preserves_internal_hardlinks(self):
        archive = self.root / "compiler.tar.bz2"
        with tarfile.open(archive, "w:bz2") as package:
            member = tarfile.TarInfo("gcc/bin/compiler")
            member.size = 4
            member.mode = 0o755
            package.addfile(member, io.BytesIO(b"data"))
            link = tarfile.TarInfo("gcc/bin/alias")
            link.type = tarfile.LNKTYPE
            link.linkname = "gcc/bin/compiler"
            # Hard links share an inode; tar applies the link record's mode too.
            # Match the executable target instead of TarInfo's default 0o644.
            link.mode = member.mode
            package.addfile(link)
        deps.unpack_toolchain(archive, self.root / "stage")
        compiler = self.root / "stage/gcc/bin/compiler"
        alias = self.root / "stage/gcc/bin/alias"
        self.assertEqual(alias.read_bytes(), b"data")
        self.assertTrue(alias.samefile(compiler))
        if deps.platform.system() != "Windows":
            self.assertTrue(compiler.stat().st_mode & stat.S_IXUSR)
            self.assertTrue(alias.stat().st_mode & stat.S_IXUSR)

    def test_toolchain_reinstalled_from_verified_cache(self):
        downloads = self.root / "downloads"
        downloads.mkdir()
        archive = downloads / "compiler.zip"
        with zipfile.ZipFile(archive, "w") as package:
            for tool in ("gcc", "objcopy", "objdump"):
                package.writestr(f"compiler/bin/arm-none-eabi-{tool}.exe", b"verified-tool")
        spec = {"archive": archive.name, "sha256": digest(archive.read_bytes()),
                "root": "compiler", "suffix": ".exe"}
        with patch.object(deps, "TOOLCHAINS", {"windows-x86_64": spec}), patch.object(
            deps, "urlopen", side_effect=AssertionError("network called")
        ):
            bin_dir = deps.prepare_toolchain(self.root, "windows-x86_64")
            compiler = bin_dir / "arm-none-eabi-gcc.exe"
            compiler.write_bytes(b"modified")
            deps.prepare_toolchain(self.root, "windows-x86_64")
        self.assertEqual(compiler.read_bytes(), b"verified-tool")
        self.assertEqual(bin_dir, self.root / "gcc/bin")

    def test_host_selection_rejects_unsupported_architecture(self):
        with patch.object(deps.platform, "machine", return_value="aarch64"):
            with self.assertRaisesRegex(ValueError, "x86-64"):
                deps.host_toolchain()


if __name__ == "__main__":
    unittest.main()
