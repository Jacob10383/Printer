from __future__ import annotations

import re
from pathlib import Path
from typing import Iterable

from .file_ops import ensure_directory, write_text


def _normalize_lines(path: Path) -> list[str]:
    if not path.exists():
        return []
    return path.read_text().splitlines()


def ensure_include_block(path: Path | str, includes: Iterable[str], *, prepend: bool = False) -> bool:
    """Ensure a set of `[include foo.cfg]` lines exist (no duplicates).

    Returns True if the file was modified.
    """
    path = Path(path)
    ensure_directory(path.parent)
    lines = _normalize_lines(path)
    pattern = re.compile(r"^\s*\[include\s+(.+?)\s*\]\s*$")

    # Remove any existing references to our includes
    includes_set = {inc.strip() for inc in includes}
    filtered = [line for line in lines if (match := pattern.match(line)) is None or match.group(1).strip() not in includes_set]

    include_lines = [f"[include {item}]" for item in includes]
    new_lines = include_lines + filtered if prepend else filtered + include_lines

    if new_lines == lines:
        return True

    # Ensure newline at end
    content = "\n".join(new_lines)
    if not content.endswith("\n"):
        content += "\n"
    write_text(path, content)
    return True


def append_unique_line(path: Path | str, line: str) -> bool:
    """Ensure a line exists exactly once; append to the end if missing."""
    path = Path(path)
    lines = _normalize_lines(path)
    if any(existing.strip() == line.strip() for existing in lines):
        return False
    lines.append(line)
    content = "\n".join(lines)
    if not content.endswith("\n"):
        content += "\n"
    write_text(path, content)
    return True


def ensure_section_entry(path: Path | str, section: str, key: str, value: str, *, separator: str = ":") -> bool:
    """Ensure `key separator value` exists under `[section]` (Moonraker style)."""
    path = Path(path)
    ensure_directory(path.parent)
    lines = _normalize_lines(path)
    header = f"[{section}]"
    section_start = None
    for idx, line in enumerate(lines):
        if line.strip() == header:
            section_start = idx
            break

    modified = False
    if section_start is None:
        # Append new section
        if lines and lines[-1].strip():
            lines.append("")
        lines.append(header)
        section_start = len(lines) - 1
        modified = True

    # Locate section end (next header or EOF)
    section_end = len(lines)
    for idx in range(section_start + 1, len(lines)):
        stripped = lines[idx].strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            section_end = idx
            break

    entry = f"{key}{separator} {value}"
    for idx in range(section_start + 1, section_end):
        if lines[idx].strip().startswith(f"{key}{separator}"):
            if lines[idx].strip() == entry:
                break
            lines[idx] = entry
            modified = True
            break
    else:
        lines.insert(section_end, entry)
        modified = True

    if modified:
        content = "\n".join(lines)
        if not content.endswith("\n"):
            content += "\n"
        write_text(path, content)
    return modified
