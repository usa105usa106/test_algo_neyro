from __future__ import annotations

import os
import shutil
from pathlib import Path


SOURCE = Path(os.getenv("LEGACY_DATA_ROOT", "/app/legacy_storage"))
TARGET = Path(os.getenv("DATA_ROOT", "/app/storage"))


def _atomic_copy(src: Path, dst: Path, *, overwrite: bool = False) -> bool:
    if not src.is_file():
        return False
    if dst.exists() and not overwrite:
        return False
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_name(f".{dst.name}.v62-migrate.tmp")
    shutil.copy2(src, tmp)
    tmp.replace(dst)
    return True


def _copy_missing_tree(source: Path, target: Path) -> tuple[int, int]:
    """Migrate only persistent secrets/state from the old Coolify volume.

    Encrypted files and ``fernet.key`` are treated as one inseparable set. This
    prevents copying a v61 encrypted credential next to an unrelated key that
    would make it unreadable.
    """
    copied_files = 0
    skipped_files = 0
    if not source.exists() or not source.is_dir():
        return copied_files, skipped_files
    target.mkdir(parents=True, exist_ok=True)

    legacy_secrets = source / "secrets"
    target_secrets = target / "secrets"
    legacy_encrypted = sorted(legacy_secrets.glob("*.enc.json")) if legacy_secrets.exists() else []
    target_encrypted = sorted(target_secrets.glob("*.enc.json")) if target_secrets.exists() else []

    # Only import the old encrypted set when the new fixed volume does not yet
    # contain credentials of its own. Import the matching key at the same time.
    if legacy_encrypted and not target_encrypted:
        for src in legacy_encrypted:
            if _atomic_copy(src, target_secrets / src.name):
                copied_files += 1
        legacy_key = source / "state" / "fernet.key"
        if legacy_key.exists() and _atomic_copy(legacy_key, target / "state" / "fernet.key", overwrite=True):
            copied_files += 1
    else:
        skipped_files += len(legacy_encrypted)

    # Copy non-encrypted state only when missing. This includes the Gmail sent
    # ledger and Intraday pending state, but not candles/logs/exports.
    legacy_state = source / "state"
    if legacy_state.exists():
        for src in legacy_state.rglob("*"):
            if not src.is_file() or src.name == "fernet.key":
                continue
            dst = target / src.relative_to(source)
            if _atomic_copy(src, dst):
                copied_files += 1
            else:
                skipped_files += 1

    return copied_files, skipped_files


if __name__ == "__main__":
    copied, skipped = _copy_missing_tree(SOURCE, TARGET)
    print(
        f"v62 storage migration: source={SOURCE} target={TARGET} "
        f"copied={copied} skipped_existing={skipped}",
        flush=True,
    )
