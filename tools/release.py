"""Plan, verify, package and publish SC2015 date releases via GitHub CLI."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

if __package__:
    from . import build
    from .release_version import next_version, release_day, validate_version
else:
    import build
    from release_version import next_version, release_day, validate_version

ROOT = Path(__file__).resolve().parents[1]


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def gh(*arguments):
    return subprocess.run(["gh", *arguments], check=True, text=True, encoding="utf-8",
                          capture_output=True).stdout.strip()


def repository_name(value):
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", value or ""):
        raise ValueError("Expected an explicit owner/repository")
    return value


def commit_id(value):
    if not re.fullmatch(r"[0-9a-f]{40}", value or ""):
        raise ValueError("Expected the exact 40-character source commit")
    return value


def existing_versions(repo):
    repository_name(repo)
    names = set()
    # Match Receiver: include every page, drafts and tags without releases.
    # API errors must not be treated as an empty version namespace.
    for collection, key in (("tags", "name"), ("releases", "tag_name")):
        pages = json.loads(gh("api", "--paginate", "--slurp", f"repos/{repo}/{collection}?per_page=100"))
        if not isinstance(pages, list) or not all(isinstance(page, list) for page in pages):
            raise ValueError("Unexpected GitHub pagination response")
        for page in pages:
            for row in page:
                if not isinstance(row, dict) or not isinstance(row.get(key), str):
                    raise ValueError("Unexpected GitHub version record")
                names.add(row[key])
    return names


def asset_name(version, filename):
    return f"SC2015-{version}-{filename}"


def checked_build(root, version, commit):
    directory = root / "build"
    checksums = {}
    for line in (directory / "SHA256SUMS").read_text(encoding="ascii").splitlines():
        match = re.fullmatch(r"([0-9a-f]{64})  (.+)", line)
        if not match or match[2] in checksums:
            raise ValueError("Invalid or duplicate build checksum entry")
        checksums[match[2]] = match[1]
    if set(checksums) != set(build.ARTIFACTS):
        raise ValueError("Build checksum list must cover all five artifacts")
    for name, digest in checksums.items():
        if sha256(directory / name) != digest:
            raise ValueError(f"Build artifact digest mismatch: {name}")
    manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    expected = {"schema_version": 1, "project": "XboxSteamController",
                "release_version": version, "source_commit": commit,
                "softdevice_prefix_verified": True}
    if any(manifest.get(key) != value for key, value in expected.items()):
        raise ValueError("Build manifest has wrong project, version, commit or SoftDevice audit")
    if set(manifest.get("images", {})) != set(build.SOURCES):
        raise ValueError("Release requires both host and radio images")
    for name in build.SOURCES:
        image = manifest["images"][name]
        filename, elf = f"{name}.bin", f"{name}-payload.elf"
        if (image.get("filename") != filename or image.get("sha256") != checksums[filename] or
                image.get("size") != (directory / filename).stat().st_size or
                image.get("elf") != {"filename": elf, "sha256": checksums[elf]}):
            raise ValueError(f"{name}: image or ELF disagrees with manifest")
        sources = {p.relative_to(root).as_posix(): sha256(p) for p in build.source_files(name, root)}
        if image.get("source_sha256") != sources:
            raise ValueError(f"{name}: source content changed since build")
    return manifest


def package(root, output, version, commit):
    validate_version(version)
    commit_id(commit)
    if output.exists():
        raise ValueError("Release output directory must be absent")
    manifest = checked_build(root, version, commit)
    output.mkdir(parents=True)
    assets = {}
    for filename in build.ARTIFACTS:
        name = asset_name(version, filename)
        if filename == "manifest.json":
            for image in manifest["images"].values():
                image["filename"] = asset_name(version, image["filename"])
                image["elf"]["filename"] = asset_name(version, image["elf"]["filename"])
            (output / name).write_text(json.dumps(manifest, indent=2, ensure_ascii=False,
                                                 sort_keys=True) + "\n", encoding="utf-8")
        else:
            shutil.copyfile(root / "build" / filename, output / name)
        assets[name] = sha256(output / name)
    (output / "SHA256SUMS").write_text(
        "".join(f"{digest}  {name}\n" for name, digest in sorted(assets.items())), encoding="ascii")
    assets["SHA256SUMS"] = sha256(output / "SHA256SUMS")
    notes = [f"# XboxSteamController {version}", "", f"Source commit: `{commit}`", "",
             "版本按北京时间（UTC+8）生成；同日后续版本使用 p1、p2……。", "",
             "| 文件 | 用途 |", "|---|---|",
             f"| `{asset_name(version, 'host.bin')}` | LPC 主控固件 |",
             f"| `{asset_name(version, 'radio.bin')}` | nRF51822 radio 固件（含原 S110） |",
             "| `*-payload.elf` | 两颗 MCU 的补丁符号 |",
             "| `*-manifest.json` | 日期版本、源码提交、构建记录及镜像校验 |",
             "| `SHA256SUMS` | 全部五项下载的 SHA-256 |", "",
             "两份 BIN 须成套使用。配对、配置及刷写说明见本版本源码中的 README.md。",
             "日期发布版本独立于固件诊断协议的数字修订号。"]
    (output / "RELEASE_NOTES.md").write_text("\n".join(notes) + "\n", encoding="utf-8")
    result = {"version": version, "source_commit": commit, "assets": assets,
              "notes_sha256": sha256(output / "RELEASE_NOTES.md")}
    (output / "package.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    return result


def publish(root, output, version, commit, repo):
    validate_version(version)
    commit_id(commit)
    repository_name(repo)
    actual_commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
    dirty = subprocess.check_output(["git", "status", "--porcelain", "--untracked-files=all"],
                                    cwd=root, text=True).strip()
    if actual_commit != commit or dirty:
        raise ValueError("Publication requires the exact clean selected checkout")
    packed = json.loads((output / "package.json").read_text(encoding="utf-8"))
    names = {asset_name(version, name) for name in build.ARTIFACTS} | {"SHA256SUMS"}
    if (packed.get("version") != version or packed.get("source_commit") != commit or
            set(packed.get("assets", {})) != names):
        raise ValueError("Release package identity or asset set mismatch")
    for name, digest in packed["assets"].items():
        if sha256(output / name) != digest:
            raise ValueError(f"Release asset changed: {name}")
    if sha256(output / "RELEASE_NOTES.md") != packed.get("notes_sha256"):
        raise ValueError("Release notes changed after packaging")
    if version in existing_versions(repo):
        raise ValueError("Version was claimed during the build; rerun for the next version")
    # Reserve atomically, then upload a draft before making the release visible.
    gh("api", "--method", "POST", f"repos/{repo}/git/refs", "-f", f"ref=refs/tags/{version}",
       "-f", f"sha={commit}")
    gh("release", "create", version, *[str(output / name) for name in sorted(names)],
       "--repo", repo, "--verify-tag", "--draft", "--title", version,
       "--notes-file", str(output / "RELEASE_NOTES.md"))
    gh("release", "edit", version, "--repo", repo, "--draft=false")
    return gh("release", "view", version, "--repo", repo, "--json", "url", "--jq", ".url")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("next", "package", "publish"))
    parser.add_argument("--repo", default=os.environ.get("GH_REPO"))
    parser.add_argument("--version", default=os.environ.get("RELEASE_VERSION"))
    parser.add_argument("--commit", default=os.environ.get("GITHUB_SHA"))
    parser.add_argument("--output", type=Path, default=ROOT / "build/release")
    args = parser.parse_args()
    if args.operation == "next":
        version = next_version(release_day(), existing_versions(args.repo))
        print(version)
        if os.environ.get("GITHUB_OUTPUT"):
            with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as output:
                output.write(f"version={version}\n")
    elif args.operation == "package":
        print(json.dumps(package(ROOT, args.output, args.version, args.commit), indent=2))
    else:
        url = publish(ROOT, args.output, args.version, args.commit, args.repo)
        print(url)
        if os.environ.get("GITHUB_STEP_SUMMARY"):
            with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as summary:
                summary.write(f"## Published {args.version}\n\n{url}\n")


if __name__ == "__main__":
    try:
        main()
    except subprocess.CalledProcessError as error:
        print(error.stderr or str(error), file=sys.stderr)
        raise SystemExit(error.returncode)
    except (OSError, ValueError, KeyError, TypeError) as error:
        print(f"Release failed: {error}", file=sys.stderr)
        raise SystemExit(1)
