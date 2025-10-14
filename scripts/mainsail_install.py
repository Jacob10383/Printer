#!/usr/bin/env python3

import os
import shutil
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from lib import shell  # noqa: E402
from lib.file_ops import copy_file, ensure_directory  # noqa: E402
from lib.logging_utils import get_logger  # noqa: E402
from lib.config_editors import ensure_section_entry  # noqa: E402
from lib.paths import MAINTENANCE_DIR, PATCHES_DIR, PRINTER_DATA_DIR  # noqa: E402

logger = get_logger("mainsail")
MOONRAKER_CONF = PRINTER_DATA_DIR / "config" / "moonraker.conf"


def download_and_extract() -> bool:
    target_dir = MAINTENANCE_DIR
    ensure_directory(target_dir)
    if target_dir.exists():
        shutil.rmtree(target_dir)
    os.makedirs(target_dir, exist_ok=True)

    os.chdir(target_dir)
    wget = "/opt/bin/wget" if Path("/opt/bin/wget").exists() else "wget"
    unzip = "/opt/bin/unzip" if Path("/opt/bin/unzip").exists() else "unzip"

    if not shell.run_logged(f"{wget} -q -O mainsail.zip https://github.com/Jacob10383/mainsail/releases/latest/download/mainsail.zip", logger_name="mainsail").ok:
        return False
    if not shell.run_logged(f"{unzip} -o -q mainsail.zip", logger_name="mainsail").ok:
        return False
    os.remove("mainsail.zip")
    return True


def create_symlink() -> bool:
    target = Path("/usr/share/mainsail")
    if target.is_symlink() or target.exists():
        if target.is_symlink():
            target.unlink()
        else:
            shutil.rmtree(target)
    os.symlink(MAINTENANCE_DIR, target)
    logger.info("Symlinked %s -> %s", target, MAINTENANCE_DIR)
    return True


def replace_nginx_config() -> bool:
    src = PATCHES_DIR / "nginx.conf"
    dst = Path("/etc/nginx/nginx.conf")
    if not src.exists():
        logger.error("nginx.conf patch missing: %s", src)
        return False
    copy_file(src, dst)
    logger.info("Replaced nginx configuration")
    return True


def ensure_update_manager() -> bool:
    if not MOONRAKER_CONF.exists():
        logger.error("moonraker.conf not found: %s", MOONRAKER_CONF)
        return False
    ensure_section_entry(
        MOONRAKER_CONF,
        "update_manager mainsail",
        "repo",
        "mainsail-crew/mainsail",
    )
    ensure_section_entry(MOONRAKER_CONF, "update_manager mainsail", "type", "web")
    ensure_section_entry(MOONRAKER_CONF, "update_manager mainsail", "channel", "stable")
    ensure_section_entry(MOONRAKER_CONF, "update_manager mainsail", "path", "~root/mainsail")
    logger.info("Ensured update_manager mainsail section exists")
    return True


def restart_nginx() -> bool:
    return shell.run_logged("/etc/init.d/nginx restart", logger_name="mainsail").ok


def main() -> None:
    if os.geteuid() != 0:
        logger.error("This installer must be run as root (use sudo)")
        sys.exit(1)

    steps = [
        download_and_extract,
        create_symlink,
        replace_nginx_config,
        ensure_update_manager,
        restart_nginx,
    ]

    for step in steps:
        if not step():
            sys.exit(1)


if __name__ == "__main__":
    main()
