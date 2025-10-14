#!/usr/bin/env python3

import argparse
import os
import shutil
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from lib import shell  # noqa: E402
from lib.file_ops import copy_file  # noqa: E402
from lib.logging_utils import get_logger  # noqa: E402
from lib.config_editors import ensure_section_entry  # noqa: E402
from lib.paths import CUSTOM_CONFIG_DIR, PRINTER_DATA_DIR  # noqa: E402

logger = get_logger("timelapse")

BASE_CONFIG_DIR = PRINTER_DATA_DIR / "config"
MOONRAKER_COMPONENTS_DIR = PRINTER_DATA_DIR.parent / "root" / "moonraker" / "moonraker" / "components"
TEMP_DIR = Path("/tmp/moonraker-timelapse")


def run(command: str) -> bool:
    result = shell.run_logged(command, logger_name="timelapse")
    return result.ok


def clone_repo() -> bool:
    if TEMP_DIR.exists():
        shutil.rmtree(TEMP_DIR)
    git_bin = "/opt/bin/git" if Path("/opt/bin/git").exists() else "git"
    return run(f"{git_bin} clone --depth 1 --quiet https://github.com/mainsail-crew/moonraker-timelapse.git {TEMP_DIR}")


def patch_timelapse(encoder: str, dst: Path) -> bool:
    try:
        content = dst.read_text()
    except Exception as exc:
        logger.error("Failed to read timelapse.py: %s", exc)
        return False

    if encoder == "h264":
        content = content.replace("-vcodec mjpeg", "-c:v libx264 -preset ultrafast -tune stillimage")
        content = content.replace("-q:v ", " -crf ")
        content = content.replace(" -g 5", "")
    else:
        content = content.replace("-vcodec libx264", "-vcodec mjpeg")
        content = content.replace("-c:v libx264", "-c:v mjpeg")
        content = content.replace(" -crf ", " -q:v ")
        content = content.replace(" -g 5", "")
        content = content.replace(" -an", " -an -movflags +faststart")

    dst.write_text(content)
    logger.info("Patched timelapse.py for %s encoder", encoder.upper())
    return True


def ensure_timelapse_include() -> bool:
    main_cfg = CUSTOM_CONFIG_DIR / "main.cfg"
    if not main_cfg.exists():
        logger.error("custom/main.cfg not found: %s", main_cfg)
        return False

    lines = main_cfg.read_text().splitlines()
    include_line = "[include timelapse.cfg]"
    if lines and lines[0].strip() == include_line:
        return True

    lines = [line for line in lines if line.strip() != include_line]
    lines.insert(0, include_line)
    main_cfg.write_text("\n".join(lines) + "\n")
    logger.info("Ensured timelapse include is first line of %s", main_cfg)
    return True


def add_timelapse_section() -> bool:
    moonraker_conf = BASE_CONFIG_DIR / "moonraker.conf"
    if not moonraker_conf.exists():
        logger.error("moonraker.conf not found: %s", moonraker_conf)
        return False
    ensure_section_entry(moonraker_conf, "timelapse", "output_path", "/mnt/UDISK/root/timelapse")
    return True


def install_timelapse(encoder: str) -> bool:
    logger.info("Installing moonraker-timelapse (%s encoder)...", encoder)
    if not clone_repo():
        logger.error("Failed to clone moonraker-timelapse repository")
        return False

    src_component = TEMP_DIR / "component" / "timelapse.py"
    dst_component = MOONRAKER_COMPONENTS_DIR / "timelapse.py"
    if not src_component.exists():
        logger.error("Source timelapse.py missing: %s", src_component)
        return False

    copy_file(src_component, dst_component)
    patch_timelapse(encoder, dst_component)

    src_cfg = TEMP_DIR / "klipper_macro" / "timelapse.cfg"
    dst_cfg = CUSTOM_CONFIG_DIR / "timelapse.cfg"
    if not src_cfg.exists():
        logger.error("Source timelapse.cfg missing: %s", src_cfg)
        return False

    copy_file(src_cfg, dst_cfg)
    ensure_timelapse_include()
    add_timelapse_section()

    if TEMP_DIR.exists():
        shutil.rmtree(TEMP_DIR)
    run("/etc/init.d/moonraker restart && /etc/init.d/klipper restart")
    return True


def main() -> None:
    parser = argparse.ArgumentParser(description="Moonraker Timelapse Installer")
    parser.add_argument("--encoder", choices=["mjpeg", "h264"], default="mjpeg")
    args = parser.parse_args()

    if os.geteuid() != 0:
        logger.error("This installer must be run as root (use sudo)")
        sys.exit(1)

    if not install_timelapse(args.encoder):
        sys.exit(1)


if __name__ == "__main__":
    main()
