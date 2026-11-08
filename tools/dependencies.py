#!/usr/bin/env python3
"""Fetch the pinned Valve images and GNU Arm toolchain using only the stdlib."""

from __future__ import annotations

import argparse
import hashlib
import os
from pathlib import Path, PurePosixPath
import platform
import shutil
import stat
import sys
import tarfile
import tempfile
from urllib.parse import urlparse
from urllib.request import Request, urlopen
import zipfile


ROOT = Path(__file__).resolve().parents[1]
FIRMWARE_URL = (
    "https://shared.fastly.steamstatic.com/community_assets/images/"
    "steamworks_docs/english/Steam_Controller_FW_Update_Tool_5b0f21bd.zip"
)
FIRMWARE_ARCHIVE_SHA256 = (
    "36bf77d770a81b99f43c4b78f825f44c02e732630cb00229828bdfb29332c1c1"
)
FIRMWARE_PREFIX = "Steam Controller FW Update Tool/updatescripts/fw_images/ble/"
BASE_IMAGES = {
    "vcf_wired_controller_d0g_5b0f21bd.bin":
        "2c42adb211c9f209dae5ac223ed451229d0aa08b139acfbd56f36b0d8add6fc4",
    "vcf_wired_controller_d0g_5a0e3f348_radio.bin":
        "db45055fc5251a5800961337454a76fb7a0ccf30a09414f757df5b20d73b96bb",
    "s110_nrf51_8.0.0_softdevice.bin":
        "707f0a518f5228039fd49d1a3a7b5fd0b477fd6b07928d07974555f394d3e23c",
}
TOOLCHAIN_URL = (
    "https://github.com/adafruit/arduino-board-index/releases/download/build-tools/"
)
# These are the 9-2019q4 archives in Adafruit's published package index.
# SHA-256 was computed from the downloaded archives; see docs/BUILD_ACTION.md.
TOOLCHAINS = {
    "linux-x86_64": {
        "archive": "gcc-arm-none-eabi-9-2019-q4-major-x86_64-linux.tar.bz2",
        "sha256": "bcd840f839d5bf49279638e9f67890b2ef3a7c9c7a9b25271e83ec4ff41d177a",
        "root": "gcc-arm-none-eabi-9-2019-q4-major",
        "suffix": "",
    },
    "windows-x86_64": {
        "archive": "gcc-arm-none-eabi-9-2019-q4-major-win32.zip",
        "sha256": "54e0879b78937cf74a548f9fd242cdfd703bec9fe135d2bb4f56acf5c05d6d61",
        "root": "gcc-arm-none-eabi-9-2019-q4-major-win32",
        "suffix": ".exe",
    },
}
CHUNK_SIZE = 1024 * 1024
MAX_DOWNLOAD_BYTES = 512 * CHUNK_SIZE


def verify_file(path: Path, expected: str) -> None:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(CHUNK_SIZE), b""):
            digest.update(block)
    actual = digest.hexdigest()
    if actual != expected:
        raise ValueError(f"SHA-256 mismatch for {path}: expected {expected}, got {actual}")


def require_https(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.netloc:
        raise ValueError("Dependency URLs must use HTTPS")


def download_verified(url: str, destination: Path, expected: str) -> Path:
    """Reuse verified archives; publish new downloads only after hash validation."""
    require_https(url)
    if destination.exists():
        verify_file(destination, expected)
        return destination
    destination.parent.mkdir(parents=True, exist_ok=True)
    request = Request(url, headers={"User-Agent": "XboxSteamController-build/1"})
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=destination.parent, prefix=destination.name + ".", suffix=".part",
            delete=False,
        ) as output:
            temporary = Path(output.name)
            with urlopen(request, timeout=120) as response:
                require_https(response.geturl())
                total = 0
                while block := response.read(CHUNK_SIZE):
                    total += len(block)
                    if total > MAX_DOWNLOAD_BYTES:
                        raise ValueError("Dependency download exceeds 512 MiB")
                    output.write(block)
        verify_file(temporary, expected)
        temporary.replace(destination)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return destination


def prepare_base(
    destination: Path, *, archive: Path | None = None, source: Path | None = None,
) -> None:
    """Validate all three images before publishing any; never extract the updater."""
    if (archive is None) == (source is None):
        raise ValueError("Provide exactly one firmware archive or source directory")
    images = {}
    if source is not None:
        for name in BASE_IMAGES:
            images[name] = (source / name).read_bytes()
    else:
        verify_file(archive, FIRMWARE_ARCHIVE_SHA256)
        with zipfile.ZipFile(archive) as package:
            for name in BASE_IMAGES:
                matches = [m for m in package.infolist() if m.filename == FIRMWARE_PREFIX + name]
                if len(matches) != 1 or matches[0].file_size > CHUNK_SIZE:
                    raise ValueError(f"Missing, duplicate or oversized firmware member: {name}")
                images[name] = package.read(matches[0])
    for name, data in images.items():
        actual = hashlib.sha256(data).hexdigest()
        if actual != BASE_IMAGES[name]:
            raise ValueError(f"SHA-256 mismatch for {name}: expected {BASE_IMAGES[name]}, got {actual}")
    destination.mkdir(parents=True, exist_ok=True)
    for name, data in images.items():
        (destination / name).write_bytes(data)


def unpack_toolchain(archive: Path, destination: Path) -> None:
    """Extract inside a fresh staging directory, preserving Linux executable bits."""
    if archive.name.endswith(".zip"):
        with zipfile.ZipFile(archive) as package:
            for member in package.infolist():
                path = PurePosixPath(member.filename)
                mode = member.external_attr >> 16
                if (path.is_absolute() or ".." in path.parts
                        or "\\" in member.filename or ":" in member.filename
                        or stat.S_ISLNK(mode)):
                    raise ValueError(f"Unsafe ZIP member: {member.filename}")
                target = destination.joinpath(*path.parts)
                if member.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                else:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with package.open(member) as source, target.open("wb") as output:
                        shutil.copyfileobj(source, output)
    else:
        with tarfile.open(archive, "r:bz2") as package:
            # Explicit filter also works on Python versions where it is not the default.
            # The official Linux archive includes internal hard links.
            package.extractall(destination, filter="data")


def host_toolchain() -> str:
    if platform.machine().lower() not in {"amd64", "x86_64"}:
        raise ValueError("Pinned toolchains support x86-64 hosts only")
    host = {"Linux": "linux-x86_64", "Windows": "windows-x86_64"}.get(platform.system())
    if host is None:
        raise ValueError("Pinned toolchains support Linux and Windows only")
    return host


def prepare_toolchain(tools_dir: Path, host: str) -> Path:
    spec = TOOLCHAINS[host]
    archive = download_verified(
        TOOLCHAIN_URL + spec["archive"], tools_dir / "downloads" / spec["archive"],
        spec["sha256"],
    )
    destination = tools_dir / "gcc"
    marker = destination / ".source-sha256"
    if destination.exists() and (
        not marker.is_file()
        or marker.read_text(encoding="ascii").strip() not in
        {item["sha256"] for item in TOOLCHAINS.values()}
    ):
        raise ValueError(f"Refusing to replace an unmanaged toolchain directory: {destination}")
    with tempfile.TemporaryDirectory(prefix="gcc-stage-", dir=tools_dir) as temporary:
        staging = Path(temporary)
        unpack_toolchain(archive, staging)
        compiler = staging / spec["root"]
        for tool in ("gcc", "objcopy", "objdump"):
            executable = compiler / "bin" / ("arm-none-eabi-" + tool + spec["suffix"])
            if not executable.is_file():
                raise ValueError(f"Toolchain archive is missing {executable.name}")
        (compiler / ".source-sha256").write_text(spec["sha256"] + "\n", encoding="ascii")
        if destination.exists():
            shutil.rmtree(destination)
        compiler.rename(destination)
    return destination / "bin"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tools-dir", type=Path, default=ROOT / ".tools")
    parser.add_argument("--base-dir", type=Path, help="Copy and verify an existing BLE image directory")
    parser.add_argument(
        "--firmware-url", default=os.environ.get("FIRMWARE_ZIP_URL") or FIRMWARE_URL,
        help="HTTPS mirror of the exact pinned Valve ZIP (checksum cannot be overridden)",
    )
    parser.add_argument("--skip-toolchain", action="store_true", help="Only prepare the base images")
    args = parser.parse_args(argv)
    if sys.version_info < (3, 12):
        parser.error("Python 3.12 or newer is required")
    tools_dir = args.tools_dir.resolve()
    try:
        host = None if args.skip_toolchain else host_toolchain()
        if args.base_dir is not None:
            prepare_base(tools_dir / "base", source=args.base_dir)
        else:
            archive = download_verified(
                args.firmware_url,
                tools_dir / "downloads" / "Steam_Controller_FW_Update_Tool_5b0f21bd.zip",
                FIRMWARE_ARCHIVE_SHA256,
            )
            prepare_base(tools_dir / "base", archive=archive)
        print(f"Verified base images: {tools_dir / 'base'}")
        if host is not None:
            print(f"Verified toolchain: {prepare_toolchain(tools_dir, host)}")
    except (OSError, ValueError, tarfile.TarError, zipfile.BadZipFile) as error:
        print(f"Dependency preparation failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
