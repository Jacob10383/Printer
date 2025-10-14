from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Optional

from .logging_utils import get_logger


def ensure_directory(path: Path | str) -> None:
    Path(path).mkdir(parents=True, exist_ok=True)


def copy_file(src: Path | str, dst: Path | str, *, mode: Optional[int] = None, make_parents: bool = True) -> Path:
    """Copy `src` to `dst`, optionally setting permissions."""
    src_path = Path(src)
    dst_path = Path(dst)
    if make_parents:
        ensure_directory(dst_path.parent)
    shutil.copy2(src_path, dst_path)
    if mode is not None:
        os.chmod(dst_path, mode)
    return dst_path


def atomic_copy(src: Path | str, dst: Path | str, *, mode: Optional[int] = None) -> Path:
    """Copy using a temporary file then move into place."""
    dst_path = Path(dst)
    ensure_directory(dst_path.parent)
    tmp_path = dst_path.with_suffix(dst_path.suffix + ".tmp")
    shutil.copy2(src, tmp_path)
    if mode is not None:
        os.chmod(tmp_path, mode)
    os.replace(tmp_path, dst_path)
    return dst_path


def copy_with_backup(src: Path | str, dst: Path | str, *, mode: Optional[int] = None) -> Path:
    """Copy `src` to `dst`, creating `dst.bak` if destination exists."""
    dst_path = Path(dst)
    if dst_path.exists():
        backup_path = dst_path.with_suffix(dst_path.suffix + ".bak")
        if not backup_path.exists():
            shutil.copy2(dst_path, backup_path)
    return copy_file(src, dst, mode=mode)


def append_unique_line(path: Path | str, line: str) -> bool:
    """Append a line if it is missing. Returns True if file modified."""
    path = Path(path)
    ensure_directory(path.parent)
    existing = path.read_text().splitlines() if path.exists() else []
    if line in (ln.strip() for ln in existing):
        return False
    with path.open("a") as fh:
        if existing and not existing[-1].endswith("\n"):
            fh.write("\n")
        fh.write(f"{line}\n")
    return True


def write_text(path: Path | str, data: str) -> None:
    path = Path(path)
    ensure_directory(path.parent)
    path.write_text(data)
