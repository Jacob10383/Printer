#!/usr/bin/env python3

import os
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from lib.file_ops import copy_file  # noqa: E402
from lib.logging_utils import get_logger  # noqa: E402
from lib.moonraker import register_service  # noqa: E402
from lib.paths import MOONRAKER_ASVC, SERVICES_DIR  # noqa: E402

logger = get_logger("cleanup")
SERVICE_NAME = "cleanup_printer_backups"
INIT_D_DIR = Path("/etc/init.d")


def install_cleanup_service() -> bool:
    logger.info("Installing cleanup service...")
    service_src = SERVICES_DIR / SERVICE_NAME
    service_dst = INIT_D_DIR / SERVICE_NAME

    if not service_src.exists():
        logger.error("Service source missing: %s", service_src)
        return False

    try:
        copy_file(service_src, service_dst, mode=0o755)
    except Exception as exc:
        logger.error("Failed to copy service: %s", exc)
        return False

    register_service(SERVICE_NAME, asvc_path=MOONRAKER_ASVC)
    return True


def main() -> None:
    if os.geteuid() != 0:
        logger.error("This installer must be run as root (use sudo)")
        sys.exit(1)

    if not install_cleanup_service():
        sys.exit(1)


if __name__ == "__main__":
    main()
