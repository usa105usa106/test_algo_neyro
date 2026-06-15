from __future__ import annotations

import hashlib
import json
import os
import shutil
import zipfile
from datetime import datetime, timezone
from pathlib import Path


def utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_UTC")


def file_sha256(path: Path, block_size: int = 1024 * 1024) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while True:
            chunk = f.read(block_size)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False, default=str), encoding="utf-8")


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def zip_directory(source_dir: Path, zip_path: Path) -> Path:
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    if zip_path.exists():
        zip_path.unlink()
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for file in sorted(source_dir.rglob("*")):
            if file.is_file():
                zf.write(file, file.relative_to(source_dir))
    return zip_path


def split_file(path: Path, max_part_bytes: int) -> list[Path]:
    if path.stat().st_size <= max_part_bytes:
        return [path]
    parts: list[Path] = []
    stem = path.name
    with path.open("rb") as f:
        idx = 1
        while True:
            chunk = f.read(max_part_bytes)
            if not chunk:
                break
            part_path = path.with_name(f"{stem}.part{idx:03d}")
            part_path.write_bytes(chunk)
            parts.append(part_path)
            idx += 1
    readme = path.with_name(f"{stem}.README_REASSEMBLE.txt")
    readme.write_text(
        "Файл был разделён на части из-за лимита Telegram Bot API.\n\n"
        f"Исходный файл: {path.name}\n"
        f"SHA256 исходника: {file_sha256(path)}\n"
        "Linux/macOS:\n"
        f"  cat {path.name}.part* > {path.name}\n\n"
        "Windows PowerShell:\n"
        f"  cmd /c copy /b {path.name}.part001+{path.name}.part002+... {path.name}\n\n"
        "После склейки проверь размер и SHA256.\n",
        encoding="utf-8",
    )
    parts.append(readme)
    return parts


def safe_rmtree(path: Path) -> None:
    if path.exists():
        shutil.rmtree(path)
    path.mkdir(parents=True, exist_ok=True)


def dir_size_bytes(path: Path) -> int:
    total = 0
    if not path.exists():
        return total
    for item in path.rglob("*"):
        if item.is_file():
            total += item.stat().st_size
    return total


def human_bytes(n: int) -> str:
    units = ["B", "KB", "MB", "GB", "TB"]
    value = float(n)
    for unit in units:
        if value < 1024 or unit == units[-1]:
            return f"{value:.2f} {unit}"
        value /= 1024
