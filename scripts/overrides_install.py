#!/usr/bin/env python3

import os
import sys
import shutil
import argparse
from pathlib import Path
import re

# Configuration
REPO_ROOT = Path(__file__).parent.parent.absolute()
CONFIG_DIR = "/mnt/UDISK/printer_data/config"
CUSTOM_CONFIG_DIR = "/mnt/UDISK/printer_data/config/custom"

def log(message, level="INFO"):
    print(f"[{level}] {message}")

def check_file_exists(path):
    return os.path.exists(path)

def copy_file(src, dst):
    if not check_file_exists(src):
        log(f"Source file not found: {src}", "ERROR")
        return False
        
    try:
        # If destination is a symlink, remove it so we replace with a regular file
        if os.path.islink(dst):
            try:
                os.unlink(dst)
                log(f"Removed existing symlink: {dst}")
            except Exception as e:
                log(f"Failed to remove existing symlink {dst}: {e}", "ERROR")
                return False
        shutil.copy2(src, dst)
        log(f"Successfully copied {src} to {dst}")
        return True
    except Exception as e:
        log(f"Failed to copy {src}: {e}", "ERROR")
        return False

def install_overrides():
    """Install overrides.cfg to custom config directory"""
    log("Installing overrides.cfg...")
    
    # Ensure custom directory exists
    os.makedirs(CUSTOM_CONFIG_DIR, exist_ok=True)
        
    # Copy overrides.cfg (will overwrite existing)
    overrides_src = REPO_ROOT / "configs" / "overrides.cfg"
    overrides_dst = Path(CUSTOM_CONFIG_DIR) / "overrides.cfg"
    if not copy_file(overrides_src, overrides_dst):
        return False
        
    log("overrides.cfg installed successfully")
    return True

def install_custom_configs():
    """Install macros.cfg, start_print.cfg, and overrides.cfg to the custom config directory"""
    # Ensure custom directory exists
    os.makedirs(CUSTOM_CONFIG_DIR, exist_ok=True)

    install_map = [
        (REPO_ROOT / "configs" / "macros.cfg", Path(CUSTOM_CONFIG_DIR) / "macros.cfg", "macros.cfg"),
        (REPO_ROOT / "configs" / "start_print.cfg", Path(CUSTOM_CONFIG_DIR) / "start_print.cfg", "start_print.cfg"),
        (REPO_ROOT / "configs" / "overrides.cfg", Path(CUSTOM_CONFIG_DIR) / "overrides.cfg", "overrides.cfg"),
    ]

    all_ok = True
    for src, dst, label in install_map:
        log(f"Installing {label}...")
        ok = copy_file(src, dst)
        all_ok = all_ok and ok
    return all_ok

def update_custom_main_cfg(dry_run: bool = False) -> bool:
    """Ensure custom/main.cfg includes our config files in the correct order after other imports.

    Desired order:
    [include macros.cfg]
    [include start_print.cfg]
    [include overrides.cfg]
    """
    main_cfg_path = Path(CUSTOM_CONFIG_DIR) / "main.cfg"

    desired_includes = [
        "[include macros.cfg]",
        "[include start_print.cfg]",
        "[include overrides.cfg]",
    ]

    # If file doesn't exist, create it with only our includes
    if not check_file_exists(main_cfg_path):
        content = ""
    else:
        try:
            with open(main_cfg_path, "r", encoding="utf-8") as f:
                content = f.read()
        except Exception as e:
            log(f"Failed to read {main_cfg_path}: {e}", "ERROR")
            return False

    # Remove any existing occurrences of our include lines (dedupe/reorder)
    include_regex = re.compile(r"^\s*\[include\s+(macros\.cfg|start_print\.cfg|overrides\.cfg)\s*\]\s*$", re.MULTILINE)
    cleaned_content = re.sub(include_regex, "", content)
    # Also trim excessive blank lines caused by removals
    cleaned_content = re.sub(r"\n{3,}", "\n\n", cleaned_content)

    new_block = "\n".join(desired_includes) + "\n"

    new_content = cleaned_content
    if not new_content.endswith("\n"):
        new_content += "\n"
    new_content += new_block

    if dry_run:
        if content == new_content:
            log("DRY RUN: main.cfg already up to date; no changes needed")
        else:
            log("DRY RUN: Would update custom/main.cfg with ordered includes: macros, start_print, overrides")
        return True

    # If no change, skip write
    if content == new_content:
        log("custom/main.cfg already contains the desired includes in the correct order; no change needed")
        return True

    try:
        with open(main_cfg_path, "w", encoding="utf-8") as f:
            f.write(new_content)
        log("Updated custom/main.cfg with ordered includes for macros, start_print, and overrides")
        return True
    except Exception as e:
        log(f"Failed to write updated contents to {main_cfg_path}: {e}", "ERROR")
        return False

def backup_file(file_path: str) -> str:
    """Create a simple .bak backup of the given file and return the backup path, or empty string on failure."""
    try:
        backup_path = f"{file_path}.bak"
        if os.path.exists(backup_path):
            log(f"Backup already exists, skipping: {backup_path}")
            return backup_path
        shutil.copy2(file_path, backup_path)
        log(f"Created backup: {backup_path}")
        return backup_path
    except Exception as e:
        log(f"Failed to create backup for {file_path}: {e}", "ERROR")
        return ""

def update_bed_mesh_minval(dry_run: bool = False) -> bool:
    """Ensure bed_mesh.py uses minval=1 for the 'move_check_distance' option."""
    target_file = "/usr/share/klipper/klippy/extras/bed_mesh.py"

    if not check_file_exists(target_file):
        log(f"Target file not found: {target_file}", "ERROR")
        return False

    try:
        with open(target_file, "r", encoding="utf-8") as f:
            content = f.read()
    except Exception as e:
        log(f"Failed to read {target_file}: {e}", "ERROR")
        return False

    # Detect if already set to 1
    already_ok = re.search(r"['\"]move_check_distance['\"]\s*,\s*5(?:\.0*)?\s*,\s*minval\s*=\s*1(?:\.0*)?", content)
    if already_ok:
        log("bed_mesh.py already has minval=1 for 'move_check_distance'; no change needed")
        return True

    # Pattern to capture the prefix up to the numeric value of minval
    pattern = r"(?P<prefix>['\"]move_check_distance['\"]\s*,\s*5(?:\.0*)?\s*,\s*minval\s*=\s*)(?P<val>[0-9]+(?:\.[0-9]*)?)"

    if dry_run:
        if re.search(pattern, content):
            log(f"DRY RUN: Would update minval to 1 in {target_file}")
            return True
        else:
            log("DRY RUN: Target pattern not found in bed_mesh.py; no changes would be made", "ERROR")
            return False

    # Perform the replacement once
    new_content, num_subs = re.subn(pattern, r"\g<prefix>1", content, count=1)
    if num_subs == 0:
        log("Target pattern not found in bed_mesh.py; no changes made", "ERROR")
        return False

    # Backup before writing
    if not backup_file(target_file):
        log("Backup failed; aborting update to prevent data loss", "ERROR")
        return False

    try:
        with open(target_file, "w", encoding="utf-8") as f:
            f.write(new_content)
        log("Updated bed_mesh.py: set minval=1 for 'move_check_distance'")
        return True
    except Exception as e:
        log(f"Failed to write updated contents to {target_file}: {e}", "ERROR")
        return False

def main():
    parser = argparse.ArgumentParser(description="Overrides Configuration Installer")
    parser.parse_args()
    
    # Check if running as root
    if os.geteuid() != 0:
        log("This installer must be run as root (use sudo)", "ERROR")
        sys.exit(1)
    
    try:
        success_configs = install_custom_configs()
        success_main_cfg = update_custom_main_cfg(dry_run=False)
        success_bed_mesh = update_bed_mesh_minval(dry_run=False)
        success = success_configs and success_main_cfg and success_bed_mesh
        sys.exit(0 if success else 1)
    except KeyboardInterrupt:
        log("Installation interrupted by user", "ERROR")
        sys.exit(1)
    except Exception as e:
        log(f"Installation failed with error: {e}", "ERROR")
        sys.exit(1)

if __name__ == "__main__":
    main()
