from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
import uuid
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Callable


GITHUB_RELEASE_API = "https://api.github.com/repos/CaptainKeat/clip-maker/releases/latest"
GITHUB_RELEASES_URL = "https://github.com/CaptainKeat/clip-maker/releases"
MAX_UPDATE_BYTES = 750 * 1024 * 1024
MAX_EXTRACTED_BYTES = 2 * 1024 * 1024 * 1024
MAX_ARCHIVE_FILES = 20_000
VERSION_PATTERN = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)$")
_update_helper_process: subprocess.Popen | None = None


@dataclass(frozen=True)
class ReleaseAsset:
    name: str
    url: str
    size: int
    sha256: str


@dataclass(frozen=True)
class ReleaseInfo:
    version: str
    tag: str
    name: str
    notes: str
    published_at: str
    html_url: str
    asset: ReleaseAsset


@dataclass(frozen=True)
class StagedUpdate:
    release: ReleaseInfo
    archive_path: Path
    payload_root: Path
    receipt_path: Path


def version_tuple(value: str) -> tuple[int, int, int]:
    match = VERSION_PATTERN.fullmatch(str(value).strip())
    if not match:
        raise ValueError(f"Unsupported release version: {value}")
    return tuple(int(part) for part in match.groups())


def is_newer_version(candidate: str, current: str) -> bool:
    return version_tuple(candidate) > version_tuple(current)


def _open(request, timeout: int = 30):
    return urllib.request.urlopen(request, timeout=timeout)


def fetch_latest_release(open_url: Callable = _open) -> ReleaseInfo:
    request = urllib.request.Request(
        GITHUB_RELEASE_API,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "Clip-Maker-Updater",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    with open_url(request, timeout=30) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if payload.get("draft") or payload.get("prerelease"):
        raise ValueError("GitHub returned a non-stable release.")
    tag = str(payload.get("tag_name") or "")
    version_tuple(tag)
    version = tag.removeprefix("v")
    expected_name = f"Clip-Maker-v{version}-Windows.zip"
    asset_row = next((row for row in payload.get("assets", []) if row.get("name") == expected_name), None)
    if not asset_row:
        raise ValueError(f"Release {tag} does not contain {expected_name}.")
    digest = str(asset_row.get("digest") or "")
    if not re.fullmatch(r"sha256:[0-9a-fA-F]{64}", digest):
        raise ValueError("The Windows release asset does not have a GitHub SHA-256 digest.")
    size = int(asset_row.get("size") or 0)
    if size <= 0 or size > MAX_UPDATE_BYTES:
        raise ValueError("The Windows release asset has an invalid size.")
    url = str(asset_row.get("browser_download_url") or "")
    if not url.startswith("https://github.com/CaptainKeat/clip-maker/releases/download/"):
        raise ValueError("The Windows release asset URL is not from the official repository.")
    return ReleaseInfo(
        version=version,
        tag=tag,
        name=str(payload.get("name") or tag),
        notes=str(payload.get("body") or ""),
        published_at=str(payload.get("published_at") or ""),
        html_url=str(payload.get("html_url") or GITHUB_RELEASES_URL),
        asset=ReleaseAsset(expected_name, url, size, digest.split(":", 1)[1].lower()),
    )


def update_data_root(create: bool = True) -> Path:
    base = Path(os.environ.get("LOCALAPPDATA", tempfile.gettempdir()))
    root = Path(os.environ.get("CLIPMAKER_UPDATE_DATA", str(base / "ClipMaker" / "updates")))
    if create:
        root.mkdir(parents=True, exist_ok=True)
    return root


def application_install_dir() -> Path:
    if not getattr(sys, "frozen", False):
        raise RuntimeError("Automatic installation is available only in the packaged Windows app.")
    return Path(sys.executable).resolve().parent


def _safe_archive_members(archive: zipfile.ZipFile, destination: Path) -> list[zipfile.ZipInfo]:
    members = archive.infolist()
    if not members or len(members) > MAX_ARCHIVE_FILES:
        raise ValueError("The update archive has an invalid file count.")
    if sum(max(0, item.file_size) for item in members) > MAX_EXTRACTED_BYTES:
        raise ValueError("The update archive is too large after extraction.")
    root = destination.resolve()
    for item in members:
        path = PurePosixPath(item.filename)
        if path.is_absolute() or not path.parts or ".." in path.parts or any(":" in part for part in path.parts) or "\\" in item.filename:
            raise ValueError("The update archive contains an unsafe path.")
        if (item.external_attr >> 16) & 0o170000 == 0o120000:
            raise ValueError("The update archive contains an unsupported symbolic link.")
        target = (destination / Path(*path.parts)).resolve()
        if target != root and root not in target.parents:
            raise ValueError("The update archive escapes its staging directory.")
    return members


def _extract_verified_archive(archive_path: Path, destination: Path) -> Path:
    destination.mkdir(parents=True, exist_ok=False)
    with zipfile.ZipFile(archive_path) as archive:
        members = _safe_archive_members(archive, destination)
        archive.extractall(destination, members)
    payload = destination / "Clip Maker"
    if not (payload / "Clip Maker.exe").is_file() or not (payload / "_internal").is_dir():
        raise ValueError("The update archive does not contain a complete Clip Maker package.")
    return payload


def stage_update(
    release: ReleaseInfo,
    install_dir: Path,
    progress: Callable[[int, int], None] | None = None,
    open_url: Callable = _open,
) -> StagedUpdate:
    install_dir = install_dir.resolve()
    install_parent = install_dir.parent
    if not install_dir.is_dir() or not (install_dir / "Clip Maker.exe").is_file():
        raise ValueError("The current Clip Maker installation could not be identified.")
    probe = install_parent / f".clip-maker-update-write-test-{uuid.uuid4().hex}"
    try:
        probe.write_bytes(b"")
    finally:
        probe.unlink(missing_ok=True)
    attempt = update_data_root() / release.version / f"{int(time.time())}-{uuid.uuid4().hex[:8]}"
    attempt.mkdir(parents=True, exist_ok=False)
    partial = attempt / f"{release.asset.name}.part"
    archive_path = attempt / release.asset.name
    request = urllib.request.Request(
        release.asset.url,
        headers={"Accept": "application/octet-stream", "User-Agent": "Clip-Maker-Updater"},
    )
    digest = hashlib.sha256()
    received = 0
    with open_url(request, timeout=60) as response, partial.open("wb") as output:
        while True:
            chunk = response.read(1024 * 1024)
            if not chunk:
                break
            received += len(chunk)
            if received > release.asset.size or received > MAX_UPDATE_BYTES:
                raise ValueError("The update download exceeded its declared size.")
            digest.update(chunk)
            output.write(chunk)
            if progress:
                progress(received, release.asset.size)
    if received != release.asset.size:
        raise ValueError(f"The update download was incomplete ({received} of {release.asset.size} bytes).")
    if digest.hexdigest().lower() != release.asset.sha256:
        raise ValueError("The downloaded update did not match GitHub's SHA-256 digest.")
    partial.replace(archive_path)
    extracted = _extract_verified_archive(archive_path, attempt / "extracted")
    candidate = install_parent / f"{install_dir.name}.update-{release.version}-{uuid.uuid4().hex[:8]}"
    shutil.copytree(extracted, candidate)
    if not (candidate / "Clip Maker.exe").is_file():
        raise ValueError("The staged update copy is incomplete.")
    return StagedUpdate(release, archive_path, candidate, update_data_root() / "last-result.json")


def _helper_script() -> str:
    return r'''param(
    [Parameter(Mandatory=$true)][int]$ParentPid,
    [Parameter(Mandatory=$true)][string]$Payload,
    [Parameter(Mandatory=$true)][string]$Target,
    [Parameter(Mandatory=$true)][string]$Backup,
    [Parameter(Mandatory=$true)][string]$Receipt,
    [Parameter(Mandatory=$true)][string]$Version,
    [Parameter(Mandatory=$true)][string]$Ready,
    [Parameter(Mandatory=$true)][string]$AppReady
)
$ErrorActionPreference = "Stop"
$movedOld = $false
try {
    $targetParent = (Resolve-Path -LiteralPath (Split-Path -Parent $Target)).Path
    $payloadParent = (Resolve-Path -LiteralPath (Split-Path -Parent $Payload)).Path
    $backupParent = [IO.Path]::GetFullPath((Split-Path -Parent $Backup))
    if (-not $backupParent.Equals($targetParent, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "The backup is not beside the installation."
    }
    if (-not $targetParent.Equals($payloadParent, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "The staged package is not beside the current installation."
    }
    if (-not (Test-Path -LiteralPath (Join-Path $Payload "Clip Maker.exe"))) {
        throw "The staged package is incomplete."
    }
    if (Test-Path -LiteralPath $Backup) {
        throw "The rollback backup path already exists."
    }
    Set-Content -LiteralPath $Ready -Value "ready" -Encoding UTF8
    $parentProcess = Get-Process -Id $ParentPid -ErrorAction SilentlyContinue
    if ($parentProcess) {
        Wait-Process -Id $ParentPid -Timeout 120 -ErrorAction Stop
    }
    if (Get-Process -Id $ParentPid -ErrorAction SilentlyContinue) {
        throw "Clip Maker did not exit before the update deadline."
    }
    Move-Item -LiteralPath $Target -Destination $Backup
    $movedOld = $true
    try {
        Move-Item -LiteralPath $Payload -Destination $Target
    } catch {
        if (-not (Test-Path -LiteralPath $Target) -and (Test-Path -LiteralPath $Backup)) {
            Move-Item -LiteralPath $Backup -Destination $Target
            $movedOld = $false
        }
        throw
    }
    $newApp = Start-Process -FilePath (Join-Path $Target "Clip Maker.exe") -WindowStyle Normal -PassThru
    $deadline = (Get-Date).AddSeconds(40)
    $booted = $false
    while ((Get-Date) -lt $deadline) {
        if ($newApp.HasExited) { break }
        if (Test-Path -LiteralPath $AppReady) {
            try {
                $state = Get-Content -LiteralPath $AppReady -Raw | ConvertFrom-Json
                if ($state.pid -eq $newApp.Id -and $state.ui_ready -and $state.version -eq $Version) { $booted = $true; break }
            } catch {}
        }
        Start-Sleep -Milliseconds 200
        $newApp.Refresh()
    }
    if (-not $booted) {
        if (-not $newApp.HasExited) { Stop-Process -Id $newApp.Id }
        $failed = $Target + '.failed-' + $Version + '-' + (Get-Date -Format 'yyyyMMddHHmmss')
        if (-not [IO.Path]::GetFullPath((Split-Path -Parent $failed)).Equals($targetParent,[System.StringComparison]::OrdinalIgnoreCase)) { throw "Invalid failed-package path" }
        Move-Item -LiteralPath $Target -Destination $failed
        Move-Item -LiteralPath $Backup -Destination $Target
        $movedOld = $false
        throw "The new app did not become ready; the previous app was restored."
    }
    $uninstallKey = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\ClipMaker'
    if (Test-Path -LiteralPath $uninstallKey) {
        $registered = Get-ItemProperty -LiteralPath $uninstallKey -ErrorAction SilentlyContinue
        if ($registered.InstallLocation -and [IO.Path]::GetFullPath($registered.InstallLocation).Equals([IO.Path]::GetFullPath($Target),[StringComparison]::OrdinalIgnoreCase)) {
            Set-ItemProperty -LiteralPath $uninstallKey -Name DisplayVersion -Value $Version -ErrorAction SilentlyContinue
        }
    }
    @{success=$true;version=$Version;backup=$Backup;installed_at=(Get-Date).ToString("o");reported=$false} |
        ConvertTo-Json | Set-Content -LiteralPath $Receipt -Encoding UTF8
} catch {
    if ($movedOld -and (Test-Path -LiteralPath $Backup)) {
        if (Test-Path -LiteralPath $Target) {
            $failed = $Target + '.failed-' + $Version + '-' + (Get-Date -Format 'yyyyMMddHHmmss')
            if (-not [IO.Path]::GetFullPath((Split-Path -Parent $failed)).Equals($targetParent,[System.StringComparison]::OrdinalIgnoreCase)) { throw "Invalid rollback path" }
            Move-Item -LiteralPath $Target -Destination $failed
        }
        Move-Item -LiteralPath $Backup -Destination $Target
        $movedOld = $false
    }
    @{success=$false;version=$Version;error=$_.Exception.Message;reported=$false} |
        ConvertTo-Json | Set-Content -LiteralPath $Receipt -Encoding UTF8
    if (-not (Get-Process -Id $ParentPid -ErrorAction SilentlyContinue) -and
        (Test-Path -LiteralPath (Join-Path $Target "Clip Maker.exe"))) {
        Start-Process -FilePath (Join-Path $Target "Clip Maker.exe") -WindowStyle Normal
    }
    exit 1
}
'''


def _start_update_helper(command: list[str], ready: Path, log: Path) -> subprocess.Popen:
    # DETACHED_PROCESS causes Windows PowerShell 5.1 to exit successfully without
    # executing -File. CREATE_NO_WINDOW keeps it hidden and lets it outlive us.
    flags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
    environment = dict(os.environ, PYINSTALLER_RESET_ENVIRONMENT="1")
    with log.open("ab") as output:
        process = subprocess.Popen(
            command, close_fds=True, creationflags=flags, cwd=str(log.parent),
            stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.STDOUT,
            env=environment,
        )
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        result = process.poll()
        if result is not None:
            raise RuntimeError(f"The update helper exited before starting (code {result}). See {log}")
        if ready.is_file():
            return process
        time.sleep(0.05)
    process.terminate()
    process.wait(timeout=5)
    raise RuntimeError(f"The update helper did not become ready. Clip Maker will stay open. See {log}")


def launch_staged_update(staged: StagedUpdate, current_version: str) -> Path:
    global _update_helper_process
    install_dir = application_install_dir()
    if staged.payload_root.parent.resolve() != install_dir.parent.resolve():
        raise ValueError("The staged update is not beside the current installation.")
    powershell = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
    if not powershell.is_file():
        raise RuntimeError("Windows PowerShell is required to install the update safely.")
    stamp = time.strftime("%Y%m%d-%H%M%S")
    backup = install_dir.parent / f"{install_dir.name}.previous-{current_version}-{stamp}"
    if backup.exists():
        raise FileExistsError(f"Update backup already exists: {backup}")
    script = staged.archive_path.parent / "apply-update.ps1"
    ready = script.with_name(f"helper-ready-{uuid.uuid4().hex}.txt")
    log = script.with_name("apply-update.log")
    script.write_text(_helper_script(), encoding="utf-8")
    command = [
        str(powershell), "-NoLogo", "-NoProfile", "-NonInteractive",
        "-ExecutionPolicy", "Bypass", "-WindowStyle", "Hidden", "-File", str(script),
        "-ParentPid", str(os.getpid()), "-Payload", str(staged.payload_root),
        "-Target", str(install_dir), "-Backup", str(backup),
        "-Receipt", str(staged.receipt_path), "-Version", staged.release.version,
        "-Ready", str(ready),
        "-AppReady", str(Path(os.environ.get("CLIPMAKER_DATA", str(update_data_root().parent))) / "desktop-ready.json"),
    ]
    _update_helper_process = _start_update_helper(command, ready, log)
    return backup


def read_update_receipt() -> dict | None:
    path = update_data_root(create=False) / "last-result.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return None
    return payload if isinstance(payload, dict) and not payload.get("reported") else None


def mark_update_receipt_reported(payload: dict) -> None:
    path = update_data_root() / "last-result.json"
    updated = dict(payload)
    updated["reported"] = True
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(updated, indent=2), encoding="utf-8")
    temporary.replace(path)
