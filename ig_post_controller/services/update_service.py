from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class UpdateAsset:
    installer_url: str
    sha256: str | None = None
    size: int | None = None


@dataclass(frozen=True)
class UpdateCheckResult:
    available: bool
    current_version: str
    version: str | None = None
    channel: str | None = None
    release_notes_url: str | None = None
    mandatory: bool = False
    asset: UpdateAsset | None = None
    raw_manifest: dict[str, Any] | None = None


def _version_parts(version: str) -> tuple[tuple[int, ...], int, str]:
    cleaned = (version or "0").strip().lower().lstrip("v")
    match = re.match(r"^(\d+(?:\.\d+)*)(?:[-.]?([a-z]+)(\d+)?)?$", cleaned)
    if not match:
        numbers = tuple(int(part) for part in re.findall(r"\d+", cleaned)) or (0,)
        return numbers, -1, cleaned
    numbers = tuple(int(part) for part in match.group(1).split("."))
    label = match.group(2) or ""
    label_number = int(match.group(3) or 0)
    prerelease_rank = {"alpha": 0, "a": 0, "beta": 1, "b": 1, "rc": 2}.get(label, 3)
    return numbers, prerelease_rank, f"{label_number:08d}"


def compare_versions(left: str, right: str) -> int:
    left_numbers, left_rank, left_suffix = _version_parts(left)
    right_numbers, right_rank, right_suffix = _version_parts(right)
    width = max(len(left_numbers), len(right_numbers))
    left_numbers = left_numbers + (0,) * (width - len(left_numbers))
    right_numbers = right_numbers + (0,) * (width - len(right_numbers))
    left_key = (left_numbers, left_rank, left_suffix)
    right_key = (right_numbers, right_rank, right_suffix)
    if left_key < right_key:
        return -1
    if left_key > right_key:
        return 1
    return 0


class UpdateService:
    def __init__(self, current_version: str, manifest_url: str, *, timeout_seconds: int = 20) -> None:
        self.current_version = current_version
        self.manifest_url = manifest_url
        self.timeout_seconds = timeout_seconds

    def check_for_update(self) -> UpdateCheckResult:
        if not self.manifest_url:
            return UpdateCheckResult(available=False, current_version=self.current_version)
        manifest = json.loads(self._read_url_text(self.manifest_url))
        latest_version = str(manifest.get("version") or "").strip()
        windows = manifest.get("windows") or {}
        installer_url = str(windows.get("installer_url") or "").strip()
        if not latest_version or not installer_url:
            return UpdateCheckResult(
                available=False,
                current_version=self.current_version,
                version=latest_version or None,
                raw_manifest=manifest,
            )
        available = compare_versions(self.current_version, latest_version) < 0
        asset = UpdateAsset(
            installer_url=installer_url,
            sha256=(str(windows.get("sha256")).strip() if windows.get("sha256") else None),
            size=int(windows["size"]) if windows.get("size") is not None else None,
        )
        return UpdateCheckResult(
            available=available,
            current_version=self.current_version,
            version=latest_version,
            channel=str(manifest.get("channel") or "stable"),
            release_notes_url=str(manifest.get("release_notes_url") or ""),
            mandatory=bool(manifest.get("mandatory", False)),
            asset=asset,
            raw_manifest=manifest,
        )

    def download_installer(self, installer_url: str, destination: Path, expected_sha256: str | None = None) -> Path:
        data = self._download_url_bytes(installer_url)
        if expected_sha256:
            actual = hashlib.sha256(data).hexdigest().upper()
            if actual != expected_sha256.upper():
                raise ValueError("Downloaded update installer failed SHA256 verification")
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(data)
        return destination

    def launch_installer(self, installer_path: Path) -> subprocess.Popen:
        args = [str(installer_path)]
        if sys.platform.startswith("win"):
            args.extend(["/CURRENTUSER"])
        return subprocess.Popen(args, close_fds=True)

    def _read_url_text(self, url: str) -> str:
        with urllib.request.urlopen(url, timeout=self.timeout_seconds) as response:
            return response.read().decode("utf-8")

    def _download_url_bytes(self, url: str) -> bytes:
        with urllib.request.urlopen(url, timeout=self.timeout_seconds) as response:
            return response.read()
