#!/usr/bin/env python3

import os
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from lib import moonraker, shell  # noqa: E402
from lib.file_ops import atomic_copy, copy_file, ensure_directory  # noqa: E402
from lib.logging_utils import get_logger  # noqa: E402
from lib.paths import BINARIES_DIR, CONFIGS_DIR, GUPPY_DIR, MOONRAKER_ASVC, SERVICES_DIR  # noqa: E402

BINARY_SRC = BINARIES_DIR / "guppyscreen"
CONFIG_SRC = CONFIGS_DIR / "guppyconfig.json"
BINARY_DST = GUPPY_DIR / "guppyscreen"
CONFIG_DST = GUPPY_DIR / "guppyconfig.json"
SERVICE_SRC = SERVICES_DIR / "guppyscreen-service"
SERVICE_DST = Path("/etc/init.d/guppyscreen")

logger = get_logger("guppyscreen")


def stop_existing_service() -> None:
    shell.run("/etc/init.d/guppyscreen stop 2>/dev/null || true")
    shell.run("killall -9 guppyscreen 2>/dev/null || true")


def disable_original_display_server() -> bool:
    logger.info("Ensuring original display server is disabled...")
    display_bin = Path("/usr/bin/display-server")
    disabled_bin = Path("/usr/bin/display-server.disabled")

    if disabled_bin.exists():
        logger.info("display-server already disabled")
        return True

    if not display_bin.exists():
        logger.info("display-server binary not found; assuming disabled")
        return True

    shell.run("killall -9 display-server 2>/dev/null || true")

    try:
        os.replace(display_bin, disabled_bin)
        logger.info("Disabled original display server")
        return True
    except Exception as exc:  # pragma: no cover - best effort
        logger.error("Failed to disable display-server: %s", exc)
        return False


def install_files() -> bool:
    logger.info("Installing GuppyScreen files...")
    ensure_directory(GUPPY_DIR)

    if not BINARY_SRC.exists():
        logger.error("GuppyScreen binary not found at %s", BINARY_SRC)
        return False

    success = True
    try:
        atomic_copy(BINARY_SRC, BINARY_DST, mode=0o755)
        logger.info("Installed GuppyScreen binary to %s", BINARY_DST)
    except Exception as exc:
        logger.error("Failed to install GuppyScreen binary: %s", exc)
        success = False

    if not CONFIG_SRC.exists():
        logger.error("GuppyScreen config not found at %s", CONFIG_SRC)
        return False

    try:
        copy_file(CONFIG_SRC, CONFIG_DST, mode=0o644)
        logger.info("Installed GuppyScreen config to %s", CONFIG_DST)
    except Exception as exc:
        logger.error("Failed to install guppyconfig.json: %s", exc)
        success = False

    return success


def install_service() -> bool:
    logger.info("Installing GuppyScreen service...")
    if not SERVICE_SRC.exists():
        logger.error("GuppyScreen service template not found at %s", SERVICE_SRC)
        return False

    try:
        copy_file(SERVICE_SRC, SERVICE_DST, mode=0o755)
        logger.info("Installed init script to %s", SERVICE_DST)
    except Exception as exc:
        logger.error("Failed to install GuppyScreen service: %s", exc)
        return False

    enable_ok = shell.run("/etc/init.d/guppyscreen enable")
    restart_ok = shell.run("/etc/init.d/guppyscreen restart")

    if enable_ok.ok:
        logger.info("Enabled GuppyScreen service")
    else:
        logger.error("Failed to enable GuppyScreen service")

    if restart_ok.ok:
        logger.info("Started GuppyScreen service")
    else:
        logger.error("Failed to start GuppyScreen service")

    return enable_ok.ok and restart_ok.ok


def register_with_moonraker() -> bool:
    logger.info("Registering GuppyScreen with Moonraker service list...")
    return moonraker.register_service("guppyscreen", asvc_path=MOONRAKER_ASVC)


def main() -> None:
    overall_success = True
    stop_existing_service()

    if not disable_original_display_server():
        overall_success = False

    if not install_files():
        overall_success = False

    if not register_with_moonraker():
        overall_success = False

    if not install_service():
        overall_success = False

    if not overall_success:
        sys.exit(1)


if __name__ == "__main__":
    main()
