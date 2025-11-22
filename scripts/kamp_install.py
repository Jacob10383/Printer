#!/usr/bin/env python3

import os
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import shutil

from lib.file_ops import copy_file, ensure_directory  # noqa: E402
from lib.logging_utils import get_logger  # noqa: E402
from lib.config_editors import ensure_include_block  # noqa: E402
from lib.paths import CONFIGS_DIR, PRINTER_DATA_DIR  # noqa: E402

logger = get_logger("kamp")
CONFIG_DIR = PRINTER_DATA_DIR / "config"


def install_kamp() -> bool:
    logger.info("Installing KAMP configuration...")

    kamp_src = CONFIGS_DIR / "KAMP"
    kamp_dst = CONFIG_DIR / "KAMP"

    if not kamp_src.exists():
        logger.error("KAMP source directory not found at %s", kamp_src)
        return False

    ensure_directory(kamp_dst.parent)
    if kamp_dst.exists():
        logger.info("Removing existing KAMP directory at %s", kamp_dst)
        shutil.rmtree(kamp_dst)

    try:
        shutil.copytree(kamp_src, kamp_dst)
    except Exception as exc:
        logger.error("Failed to copy KAMP directory: %s", exc)
        return False
    logger.info("Copied KAMP directory to %s", kamp_dst)

    main_printer_cfg = CONFIG_DIR / "printer.cfg"
    if not main_printer_cfg.exists():
        logger.error("printer.cfg not found at %s; cannot add include", main_printer_cfg)
        return False

    ensure_include_block(main_printer_cfg, ["KAMP/KAMP_Settings.cfg"])
    logger.info("Ensured printer.cfg includes KAMP/KAMP_Settings.cfg")
    return True


def main() -> None:
    if not install_kamp():
        sys.exit(1)


if __name__ == "__main__":
    if os.geteuid() != 0:
        print("This installer must be run as root (use sudo)")
        sys.exit(1)
    main()
