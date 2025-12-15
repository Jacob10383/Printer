#!/usr/bin/env python3

import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from lib import shell
from lib.file_ops import atomic_copy, ensure_directory
from lib.logging_utils import get_logger
from lib.paths import KLIPPER_EXTRAS_DIR, PATCHES_DIR

logger = get_logger("gcode_shell_cmd")

SOURCE_FILE = PATCHES_DIR / "gcode_shell_command" / "gcode_shell_command.py"
DEST_FILE = KLIPPER_EXTRAS_DIR / "gcode_shell_command.py"


def install_extension() -> bool:
    logger.info("Installing GCode Shell Command extension...")
    
    if not SOURCE_FILE.exists():
        logger.error("Source file not found: %s", SOURCE_FILE)
        return False

    try:
        ensure_directory(KLIPPER_EXTRAS_DIR)
        atomic_copy(SOURCE_FILE, DEST_FILE, mode=0o644)
        logger.info("Installed extension to %s", DEST_FILE)
    except Exception as exc:
        logger.error("Failed to install extension: %s", exc)
        return False
        
    return True


def restart_klipper() -> bool:
    logger.info("Restarting Klipper...")
    return shell.run("systemctl restart klipper").ok


def main() -> None:
    if not install_extension():
        sys.exit(1)
    
    # We don't necessarily fail the install if restart fails, but good to try
    restart_klipper() 


if __name__ == "__main__":
    main()
