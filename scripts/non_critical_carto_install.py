#!/usr/bin/env python3

import os
import sys
from pathlib import Path

# Ensure our shared helper modules are importable
SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from lib.file_ops import copy_file, ensure_directory
from lib.logging_utils import get_logger
from lib.paths import PATCHES_DIR
from lib import shell

logger = get_logger("non_critical_carto")

# Define source patches (inside patches/nonCriticalCarto)
PATCH_SOURCE_DIR = PATCHES_DIR / "nonCriticalCarto"

# Define destination paths
KLIPPER_DIR = Path("/usr/share/klipper/klippy")
EXTRAS_DIR = KLIPPER_DIR / "extras"
UDISK_CARTO_DIR = Path("/mnt/UDISK/root/cartographer-klipper")

def has_native_acm_support() -> bool:
    """Check if kernel has native USB ACM support compiled in."""
    config_gz = Path("/proc/config.gz")
    
    if not config_gz.exists():
        logger.warning("Kernel config not available at /proc/config.gz, assuming stock kernel")
        return False
    
    try:
        result = shell.run(["sh", "-c", "gunzip -c /proc/config.gz | grep '^CONFIG_USB_ACM='"])
        if result.ok and "CONFIG_USB_ACM=y" in result.stdout:
            logger.info("Native USB ACM support detected (CONFIG_USB_ACM=y)")
            return True
        else:
            logger.info("No native USB ACM support detected")
            return False
    except Exception as exc:
        logger.warning("Failed to check kernel config: %s. Assuming stock kernel.", exc)
        return False


def install_usb_bridge_wrapper() -> bool:
    """Install USB bridge wrapper for systems without native ACM support."""
    logger.info("Installing USB bridge wrapper...")
    
    wrapper_src = PATCH_SOURCE_DIR / "usb_bridge_wrapper.sh"
    wrapper_dest = Path("/mnt/UDISK/root/cartographer_wrapper.sh")
    service_file = Path("/etc/init.d/cartographer")
    
    if not wrapper_src.exists():
        logger.error("USB bridge wrapper script not found: %s", wrapper_src)
        return False
    
    # Install the wrapper script
    try:
        copy_file(wrapper_src, wrapper_dest, mode=0o755)
        logger.info("Installed USB bridge wrapper to %s", wrapper_dest)
    except Exception as exc:
        logger.error("Failed to install USB bridge wrapper: %s", exc)
        return False
    
    # Modify the cartographer service to use the wrapper
    if not service_file.exists():
        logger.warning("Cartographer service not found at %s, skipping service modification", service_file)
        return True
    
    try:
        # Read the service file (follow symlink if needed)
        service_content = service_file.read_text()
        
        # Replace the PROG line
        old_prog = "PROG=/mnt/UDISK/bin/usb_bridge"
        new_prog = "PROG=/mnt/UDISK/root/cartographer_wrapper.sh"
        
        if old_prog in service_content:
            updated_content = service_content.replace(old_prog, new_prog)
            service_file.write_text(updated_content)
            logger.info("Modified cartographer service to use wrapper")
            
            # Restart the service to apply changes
            logger.info("Restarting cartographer service...")
            result = shell.run_logged(
                ["/etc/init.d/cartographer", "restart"],
                logger_name="non_critical_carto"
            )
            if result.ok:
                logger.info("Cartographer service restarted successfully")
            else:
                logger.warning("Failed to restart cartographer service")
        else:
            logger.warning("Expected PROG line not found in service file, skipping modification")
        
        return True
    except Exception as exc:
        logger.error("Failed to modify cartographer service: %s", exc)
        return False


def install_non_critical_carto() -> bool:
    logger.info("Installing Non-Critical Cartographer patches...")

    if not PATCH_SOURCE_DIR.exists():
        logger.error("Patch directory not found: %s", PATCH_SOURCE_DIR)
        return False

    file_mapping = [
        ("mcu.py", KLIPPER_DIR / "mcu.py"),
        ("serialhdl.py", KLIPPER_DIR / "serialhdl.py"),
        ("clocksync.py", KLIPPER_DIR / "clocksync.py"),
        ("homing.py", EXTRAS_DIR / "homing.py"),
        ("scanner.py", UDISK_CARTO_DIR / "scanner.py"),
    ]

    success = True
    for filename, dest_path in file_mapping:
        src_path = PATCH_SOURCE_DIR / filename
        
        if not src_path.exists():
            logger.error("Source patch missing: %s", src_path)
            success = False
            continue

        if "cartographer-klipper" in str(dest_path) and not dest_path.parent.exists():
            logger.warning("Destination directory for %s does not exist: %s. Skipping this file.", filename, dest_path.parent)
            success = False
            continue

        try:
            logger.info("Installing %s to %s", filename, dest_path)
            copy_file(src_path, dest_path)
        except Exception as exc:
            logger.error("Failed to install %s: %s", filename, exc)
            success = False

    # Conditionally install USB bridge wrapper if needed
    if not has_native_acm_support():
        logger.info("Stock kernel detected, installing USB bridge wrapper")
        if not install_usb_bridge_wrapper():
            logger.warning("Failed to install USB bridge wrapper, but continuing")
    else:
        logger.info("Native USB ACM support available, skipping USB bridge wrapper")
    
    return success

def main() -> None:
    if os.geteuid() != 0:
        logger.error("This installer must be run as root (use sudo)")
        sys.exit(1)

    if not install_non_critical_carto():
        sys.exit(1)

if __name__ == "__main__":
    main()
