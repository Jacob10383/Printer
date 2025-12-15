#!/usr/bin/env python3

import os
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from lib import moonraker, shell  # noqa: E402
from lib.config_editors import ensure_include_block  # noqa: E402
from lib.file_ops import atomic_copy, copy_file, ensure_directory, write_text  # noqa: E402
from lib.logging_utils import get_logger  # noqa: E402
from lib.paths import BINARIES_DIR, CONFIGS_DIR, CUSTOM_CONFIG_DIR, GUPPY_DIR, MOONRAKER_ASVC, SCRIPTS_DIR, SERVICES_DIR  # noqa: E402


BINARY_SRC = BINARIES_DIR / "guppyscreen"
CONFIG_SRC = CONFIGS_DIR / "guppyconfig.json"
BINARY_DST = GUPPY_DIR / "guppyscreen"
CONFIG_DST = GUPPY_DIR / "guppyconfig.json"
SERVICE_SRC = SERVICES_DIR / "guppyscreen-service"
SERVICE_DST = Path("/etc/init.d/guppyscreen")
BOOT_PLAY_BIN = Path("/sbin/boot-play")
BOOT_PLAY_DISABLED = BOOT_PLAY_BIN.with_suffix(BOOT_PLAY_BIN.suffix + ".disabled")

logger = get_logger("guppyscreen")


def stop_existing_service() -> None:
    shell.run("/etc/init.d/guppyscreen stop 2>/dev/null || true")
    shell.run("killall -9 guppyscreen 2>/dev/null || true")


def kill_display_server(reason: str) -> None:
    """Force-stop the legacy display-server process with context for logging."""
    logger.info("Stopping display-server (%s)", reason)
    shell.run("killall -9 display-server 2>/dev/null || true")


def disable_original_display_server() -> bool:
    logger.info("Ensuring original display server is disabled...")
    display_bin = Path("/usr/bin/display-server")
    disabled_bin = Path("/usr/bin/display-server.disabled")

    if disabled_bin.exists():
        logger.info("display-server already disabled")
        kill_display_server("already disabled check")
        return True

    if not display_bin.exists():
        logger.info("display-server binary not found; assuming disabled")
        return True

    kill_display_server("pre-disable")

    try:
        os.replace(display_bin, disabled_bin)
        logger.info("Disabled original display server")
    except Exception as exc:  # pragma: no cover - best effort
        logger.error("Failed to disable display-server: %s", exc)
        return False

    kill_display_server("post-disable verification")
    return True


def disable_boot_play_binary() -> bool:
    """Rename /sbin/boot-play so it cannot be started."""
    logger.info("Disabling boot-play binary...")
    if BOOT_PLAY_DISABLED.exists():
        logger.info("boot-play already disabled")
        return True
    if not BOOT_PLAY_BIN.exists():
        logger.info("boot-play binary not found; assuming disabled")
        return True

    try:
        os.replace(BOOT_PLAY_BIN, BOOT_PLAY_DISABLED)
        logger.info("Renamed %s to %s", BOOT_PLAY_BIN, BOOT_PLAY_DISABLED)
    except Exception as exc:  # pragma: no cover - best effort
        logger.error("Failed to disable boot-play: %s", exc)
        return False

    shell.run("killall -9 boot-play 2>/dev/null || true")
    return True


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


def install_macros() -> bool:
    logger.info("Installing GuppyScreen macros...")
    ensure_directory(CUSTOM_CONFIG_DIR)
    
    guppy_cfg_path = CUSTOM_CONFIG_DIR / "guppy.cfg"
    
    macro_content = """[gcode_shell_command disable_guppy]
command: switch-to-creality.sh
timeout: 20.0
verbose: True

[gcode_macro DISABLE_GUPPY]
gcode:
    RUN_SHELL_COMMAND CMD=disable_guppy

[gcode_shell_command enable_guppy]
command: switch-to-guppy.sh
timeout: 20.0
verbose: True

[gcode_macro ENABLE_GUPPY]
gcode:
    RUN_SHELL_COMMAND CMD=enable_guppy
"""
    try:
        write_text(guppy_cfg_path, macro_content)
        logger.info("Created %s", guppy_cfg_path)
    except Exception as exc:
        logger.error("Failed to create guppy.cfg: %s", exc)
        return False

    # Verify/Add include in main.cfg
    main_cfg_path = CUSTOM_CONFIG_DIR / "main.cfg"
    try:
        if ensure_include_block(main_cfg_path, ["guppy.cfg"]):
            logger.info("Added [include guppy.cfg] to %s", main_cfg_path)
    except Exception as exc:
        logger.error("Failed to update main.cfg includes: %s", exc)
        return False
        
    return True


def install_gesture_daemon() -> bool:
    logger.info("Installing gesture daemon...")
    
    gesture_binary_src = BINARIES_DIR / "guppy-gesture-daemon"
    gesture_binary_dst = Path("/usr/bin/guppy-gesture-daemon")
    gesture_service_src = SERVICES_DIR / "gesture-daemon"
    gesture_service_dst = Path("/etc/init.d/gesture-daemon")
    
    switch_guppy_src = SCRIPTS_DIR / "gesture" / "switch-to-guppy.sh"
    switch_guppy_dst = Path("/usr/bin/switch-to-guppy.sh")
    switch_creality_src = SCRIPTS_DIR / "gesture" / "switch-to-creality.sh"
    switch_creality_dst = Path("/usr/bin/switch-to-creality.sh")
    
    success = True
    
    # Install gesture daemon binary
    if not gesture_binary_src.exists():
        logger.warning("Gesture daemon binary not found at %s, skipping", gesture_binary_src)
    else:
        try:
            copy_file(gesture_binary_src, gesture_binary_dst, mode=0o755)
            logger.info("Installed gesture daemon binary to %s", gesture_binary_dst)
        except Exception as exc:
            logger.error("Failed to install gesture daemon binary: %s", exc)
            success = False
    
    # Install gesture daemon init script
    if not gesture_service_src.exists():
        logger.warning("Gesture daemon service not found at %s, skipping", gesture_service_src)
    else:
        try:
            copy_file(gesture_service_src, gesture_service_dst, mode=0o755)
            logger.info("Installed gesture daemon service to %s", gesture_service_dst)
        except Exception as exc:
            logger.error("Failed to install gesture daemon service: %s", exc)
            success = False
    
    # Install switch scripts
    for src, dst, name in [
        (switch_guppy_src, switch_guppy_dst, "switch-to-guppy.sh"),
        (switch_creality_src, switch_creality_dst, "switch-to-creality.sh"),
    ]:
        if not src.exists():
            logger.warning("%s not found at %s, skipping", name, src)
        else:
            try:
                copy_file(src, dst, mode=0o755)
                logger.info("Installed %s to %s", name, dst)
            except Exception as exc:
                logger.error("Failed to install %s: %s", name, exc)
                success = False
    
    # Enable and start gesture daemon service
    if gesture_service_dst.exists():
        enable_ok = shell.run("/etc/init.d/gesture-daemon enable")
        if enable_ok.ok:
            logger.info("Enabled gesture daemon service")
        else:
            logger.error("Failed to enable gesture daemon service")
            success = False
        
        start_ok = shell.run("/etc/init.d/gesture-daemon start")
        if start_ok.ok:
            logger.info("Started gesture daemon service")
        else:
            logger.warning("Failed to start gesture daemon service (may be normal if display-server not running)")
    
    return success


def register_with_moonraker() -> bool:
    logger.info("Registering GuppyScreen with Moonraker service list...")
    return moonraker.register_service("guppyscreen", asvc_path=MOONRAKER_ASVC)


def main() -> None:
    overall_success = True
    stop_existing_service()

    if not disable_original_display_server():
        overall_success = False

    if not disable_boot_play_binary():
        overall_success = False

    if not install_files():
        overall_success = False

    if not register_with_moonraker():
        overall_success = False

    if not install_service():
        overall_success = False

    if not install_macros():
        overall_success = False

    if not install_gesture_daemon():
        overall_success = False

    if not overall_success:
        sys.exit(1)


if __name__ == "__main__":
    main()
