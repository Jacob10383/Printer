#!/usr/bin/env python3

import os
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from lib.file_ops import copy_file  # noqa: E402
from lib.logging_utils import get_logger  # noqa: E402
from lib.paths import PATCHES_DIR  # noqa: E402

logger = get_logger("resonance")
KLIPPER_EXTRAS_DIR = Path("/usr/share/klipper/klippy/extras")


def install_resonance_tester() -> bool:
    logger.info("Installing custom resonance tester...")
    source = PATCHES_DIR / "resonance_tester.py"
    destination = KLIPPER_EXTRAS_DIR / "resonance_tester.py"

    if not source.exists():
        logger.error("Source resonance_tester.py not found: %s", source)
        return False

    try:
        copy_file(source, destination)
    except Exception as exc:
        logger.error("Failed to install resonance_tester.py: %s", exc)
        return False
    return True


def main() -> None:
    if os.geteuid() != 0:
        logger.error("This installer must be run as root (use sudo)")
        sys.exit(1)

    if not install_resonance_tester():
        sys.exit(1)


if __name__ == "__main__":
    main()
