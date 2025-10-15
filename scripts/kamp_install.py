#!/usr/bin/env python3

import os
import sys
import shutil
import argparse
from pathlib import Path

# Configuration
REPO_ROOT = Path(__file__).parent.parent.absolute()
CONFIG_DIR = "/mnt/UDISK/printer_data/config"

def log(message, level="INFO"):
    print(f"[{level}] {message}")

def check_file_exists(path):
    return os.path.exists(path)

def check_dir_exists(path):
    return os.path.isdir(path)

def copy_file(src, dst):
    if not check_file_exists(src):
        log(f"Source file not found: {src}", "ERROR")
        return False
        
    try:
        shutil.copy2(src, dst)
        log(f"Successfully copied {src} to {dst}")
        return True
    except Exception as e:
        log(f"Failed to copy {src}: {e}", "ERROR")
        return False

def copy_dir(src, dst):
    if not check_dir_exists(src):
        log(f"Source directory not found: {src}", "ERROR")
        return False
        
    try:
        if os.path.exists(dst):
            shutil.rmtree(dst)
        shutil.copytree(src, dst)
        log(f"Successfully copied directory {src} to {dst}")
        return True
    except Exception as e:
        log(f"Failed to copy directory {src}: {e}", "ERROR")
        return False

def add_include_to_printer_cfg(include_line):
    """Add an include line to printer.cfg"""
    printer_cfg = Path(CONFIG_DIR) / "printer.cfg"
    if not check_file_exists(printer_cfg):
        log("printer.cfg not found - cannot add include line", "ERROR")
        return False
        
    with open(printer_cfg, 'r') as f:
        content = f.read()
        
    if include_line in content:
        log(f"{include_line} already included in printer.cfg")
        return True
        
    # Add the include line safely
    lines = content.split('\n')
    include_lines = []
    for i, line in enumerate(lines):
        if line.strip().startswith('[include'):
            include_lines.append(i)
    
    if include_lines:
        # Insert after the last include line
        insert_pos = include_lines[-1] + 1
        lines.insert(insert_pos, include_line)
    else:
        # If no include lines found, append at the end
        # Ensure file ends with a newline before appending
        if lines and lines[-1] != '':
            lines.append('')
        lines.append(include_line)
        
    # Write back to file
    with open(printer_cfg, 'w') as f:
        f.write('\n'.join(lines))
    log(f"Added {include_line} to printer.cfg")
    return True

def install_kamp():
    """Install KAMP configuration files"""
    log("Installing KAMP configuration...")
    
    # Copy KAMP folder
    kamp_src = REPO_ROOT / "configs" / "KAMP"
    kamp_dst = Path(CONFIG_DIR) / "KAMP"
    if not copy_dir(kamp_src, kamp_dst):
        return False
        
    # Copy KAMP_Settings.cfg
    kamp_settings_src = REPO_ROOT / "configs" / "KAMP_Settings.cfg"
    kamp_settings_dst = Path(CONFIG_DIR) / "KAMP_Settings.cfg"
    if not copy_file(kamp_settings_src, kamp_settings_dst):
        return False
        
    # Add include to printer.cfg
    if not add_include_to_printer_cfg('[include KAMP_Settings.cfg]'):
        return False
        
    log("KAMP configuration installed successfully")
    return True

def main():
    parser = argparse.ArgumentParser(description="KAMP Configuration Installer")
    parser.parse_args()
    
    # Check if running as root
    if os.geteuid() != 0:
        log("This installer must be run as root (use sudo)", "ERROR")
        sys.exit(1)
    
    try:
        success = install_kamp()
        sys.exit(0 if success else 1)
    except KeyboardInterrupt:
        log("Installation interrupted by user", "ERROR")
        sys.exit(1)
    except Exception as e:
        log(f"Installation failed with error: {e}", "ERROR")
        sys.exit(1)

if __name__ == "__main__":
    main()
