from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Optional

from .logging_utils import get_logger


def ensure_directory(path: Path | str) -> None:
    Path(path).mkdir(parents=True, exist_ok=True)


def copy_file(src: Path | str, dst: Path | str, *, mode: Optional[int] = None, make_parents: bool = True, verify: bool = True, max_retries: int = 3) -> Path:
    """Copy `src` to `dst` with retry logic, optionally setting permissions and verifying the copy."""
    import time
    
    src_path = Path(src)
    dst_path = Path(dst)
    
    if not src_path.exists():
        raise FileNotFoundError(f"Source file does not exist: {src_path}")
    
    src_size = src_path.stat().st_size
    
    if make_parents:
        ensure_directory(dst_path.parent)
    
    # Check available disk space before attempting copy
    if verify and src_size > 0:
        dst_stat = os.statvfs(dst_path.parent)
        available_space = dst_stat.f_bavail * dst_stat.f_frsize
        # Require at least 1.5x the file size for safety margin
        required_space = int(src_size * 1.5)
        
        if available_space < required_space:
            raise IOError(
                f"Insufficient disk space. Required: {required_space} bytes, "
                f"Available: {available_space} bytes"
            )
    
    last_error = None
    for attempt in range(max_retries):
        try:
            # Remove any existing corrupted destination file
            if dst_path.exists():
                dst_path.unlink()
            
            # Perform the copy
            shutil.copy2(src_path, dst_path)
            
            # Sync to ensure data is written to disk
            if verify:
                with open(dst_path, 'rb') as f:
                    os.fsync(f.fileno())
            
            if mode is not None:
                os.chmod(dst_path, mode)
            
            # Verify the copy succeeded
            if verify:
                if not dst_path.exists():
                    raise IOError(f"Copy failed: destination file does not exist: {dst_path}")
                
                dst_size = dst_path.stat().st_size
                if dst_size != src_size:
                    raise IOError(
                        f"Copy failed: size mismatch for {dst_path}. "
                        f"Expected {src_size} bytes, got {dst_size} bytes"
                    )
                
                if src_size > 0 and dst_size == 0:
                    raise IOError(f"Copy failed: destination file is empty: {dst_path}")
            
            # Success!
            return dst_path
            
        except (IOError, OSError) as e:
            last_error = e
            
            # Clean up partial/corrupted file
            if dst_path.exists():
                dst_path.unlink()
            
            if attempt < max_retries - 1:
                # Exponential backoff: 0.5s, 1s, 2s
                sleep_time = 0.5 * (2 ** attempt)
                time.sleep(sleep_time)
                continue
            else:
                # Final attempt failed
                raise IOError(
                    f"Copy failed after {max_retries} attempts. Last error: {last_error}"
                ) from last_error
    
    # Should never reach here, but just in case
    raise IOError(f"Copy failed: {last_error}") from last_error


def atomic_copy(src: Path | str, dst: Path | str, *, mode: Optional[int] = None, verify: bool = True, max_retries: int = 3) -> Path:
    """Copy using a temporary file then move into place, with retry logic."""
    import time
    
    src_path = Path(src)
    dst_path = Path(dst)
    
    if not src_path.exists():
        raise FileNotFoundError(f"Source file does not exist: {src_path}")
    
    src_size = src_path.stat().st_size
    
    ensure_directory(dst_path.parent)
    
    # Check available disk space before attempting copy
    if verify and src_size > 0:
        dst_stat = os.statvfs(dst_path.parent)
        available_space = dst_stat.f_bavail * dst_stat.f_frsize
        required_space = int(src_size * 1.5)
        
        if available_space < required_space:
            raise IOError(
                f"Insufficient disk space. Required: {required_space} bytes, "
                f"Available: {available_space} bytes"
            )
    
    last_error = None
    for attempt in range(max_retries):
        tmp_path = dst_path.with_suffix(dst_path.suffix + f".tmp.{attempt}")
        
        try:
            # Clean up any existing temp file from previous attempts
            if tmp_path.exists():
                tmp_path.unlink()
            
            # Perform the copy to temp file
            shutil.copy2(src_path, tmp_path)
            
            # Sync temp file to disk
            if verify:
                with open(tmp_path, 'rb') as f:
                    os.fsync(f.fileno())
            
            if mode is not None:
                os.chmod(tmp_path, mode)
            
            # Verify the temporary copy before moving
            if verify:
                if not tmp_path.exists():
                    raise IOError(f"Copy failed: temporary file does not exist: {tmp_path}")
                
                tmp_size = tmp_path.stat().st_size
                if tmp_size != src_size:
                    raise IOError(
                        f"Copy failed: size mismatch for {tmp_path}. "
                        f"Expected {src_size} bytes, got {tmp_size} bytes"
                    )
                
                if src_size > 0 and tmp_size == 0:
                    raise IOError(f"Copy failed: temporary file is empty: {tmp_path}")
            
            # Move temp file to final destination
            os.replace(tmp_path, dst_path)
            
            # Final verification after move
            if verify:
                if not dst_path.exists():
                    raise IOError(f"Move failed: destination file does not exist: {dst_path}")
                
                dst_size = dst_path.stat().st_size
                if dst_size != src_size:
                    raise IOError(
                        f"Move failed: size mismatch for {dst_path}. "
                        f"Expected {src_size} bytes, got {dst_size} bytes"
                    )
            
            # Success!
            return dst_path
            
        except (IOError, OSError) as e:
            last_error = e
            
            # Clean up temp file and potentially corrupted destination
            if tmp_path.exists():
                tmp_path.unlink()
            if dst_path.exists() and verify:
                # Only remove destination if it's clearly corrupted
                try:
                    if dst_path.stat().st_size != src_size:
                        dst_path.unlink()
                except OSError:
                    pass
            
            if attempt < max_retries - 1:
                # Exponential backoff
                sleep_time = 0.5 * (2 ** attempt)
                time.sleep(sleep_time)
                continue
            else:
                # Final attempt failed
                raise IOError(
                    f"Atomic copy failed after {max_retries} attempts. Last error: {last_error}"
                ) from last_error
    
    # Should never reach here
    raise IOError(f"Atomic copy failed: {last_error}") from last_error


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
