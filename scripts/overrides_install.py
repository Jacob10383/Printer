#!/usr/bin/env python3

import os
import re
import shutil
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from lib.file_ops import copy_file, ensure_directory  # noqa: E402
from lib.logging_utils import get_logger  # noqa: E402
from lib.config_editors import ensure_include_block  # noqa: E402
from lib.paths import CONFIGS_DIR, CUSTOM_CONFIG_DIR  # noqa: E402

logger = get_logger("overrides")
CONFIG_DIR = CUSTOM_CONFIG_DIR.parent


def _replace_symlink_if_needed(path: Path) -> None:
    if path.is_symlink():
        logger.info("Removing symlink at %s", path)
        path.unlink()


def install_custom_configs() -> bool:
    logger.info("Installing custom config files...")
    ensure_directory(CUSTOM_CONFIG_DIR)

    targets = ["macros.cfg", "start_print.cfg", "overrides.cfg"]
    success = True
    for filename in targets:
        src = CONFIGS_DIR / filename
        dst = CUSTOM_CONFIG_DIR / filename
        if not src.exists():
            logger.error("Source file missing: %s", src)
            success = False
            continue
        _replace_symlink_if_needed(dst)
        try:
            copy_file(src, dst)
            logger.info("Installed %s", dst)
        except Exception as exc:
            logger.error("Failed to copy %s -> %s: %s", src, dst, exc)
            success = False
    return success


def update_custom_main_cfg() -> bool:
    logger.info("Ensuring custom/main.cfg includes macros/start_print/overrides...")
    main_cfg = CUSTOM_CONFIG_DIR / "main.cfg"
    includes = ["macros.cfg", "start_print.cfg", "overrides.cfg"]
    return ensure_include_block(main_cfg, includes)


def update_bed_mesh_minval() -> bool:
    target_file = Path("/usr/share/klipper/klippy/extras/bed_mesh.py")
    if not target_file.exists():
        logger.error("Target file not found: %s", target_file)
        return False

    content = target_file.read_text()
    # If already patched, exit early
    already_patched = re.search(
        r"getfloat\(\s*['\"]move_check_distance['\"][^)]*?\bminval\s*=\s*1(?:\.0*)?",
        content,
        flags=re.DOTALL,
    )
    if already_patched:
        logger.info("bed_mesh.py already patched; nothing to do")
        return True

    # First try: replace any existing minval assignment inside the move_check_distance getfloat call
    replace_minval = re.compile(
        r"(getfloat\(\s*['\"]move_check_distance['\"][^)]*?\bminval\s*=\s*)([0-9]+(?:\.[0-9]*)?)",
        flags=re.DOTALL,
    )
    new_content, num_subs = replace_minval.subn(r"\g<1>1", content, count=1)

    # Second try: if there was no minval argument, insert one before the closing paren
    if num_subs == 0:
        def _add_minval(match: re.Match) -> str:
            args_part = match.group(1).rstrip()
            suffix = match.group(2)
            if args_part.endswith("("):
                sep = ""
            elif args_part.endswith(","):
                sep = " "
            else:
                sep = ", "
            return f"{args_part}{sep}minval=1{suffix}"

        add_minval = re.compile(
            r"(getfloat\(\s*['\"]move_check_distance['\"][^)]*)(\))",
            flags=re.DOTALL,
        )
        new_content, num_subs = add_minval.subn(_add_minval, content, count=1)

    if num_subs == 0:
        logger.error("Move check distance call not found in %s", target_file)
        return False

    verify_pattern = re.compile(
        r"getfloat\(\s*['\"]move_check_distance['\"][^)]*?\bminval\s*=\s*1(?:\.0*)?",
        flags=re.DOTALL,
    )
    if not verify_pattern.search(new_content):
        logger.error("Failed to set minval=1 in %s", target_file)
        return False

    backup_path = target_file.with_suffix(target_file.suffix + ".bak")
    if not backup_path.exists():
        shutil.copy2(target_file, backup_path)
    target_file.write_text(new_content)
    logger.info("Updated %s to set minval=1 for move_check_distance", target_file)
    return True


def main() -> None:
    if os.geteuid() != 0:
        logger.error("This installer must be run as root (use sudo)")
        sys.exit(1)

    success_configs = install_custom_configs()
    success_main_cfg = update_custom_main_cfg()
    success_bed_mesh = update_bed_mesh_minval()
    success = success_configs and success_main_cfg and success_bed_mesh
    if not success:
        sys.exit(1)


if __name__ == "__main__":
    main()
