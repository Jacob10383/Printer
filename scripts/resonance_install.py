#!/usr/bin/env python3

import os
import sys
import shutil
import argparse
from pathlib import Path

# Configuration
REPO_ROOT = Path(__file__).parent.parent.absolute()
KLIPPER_EXTRAS_DIR = "/usr/share/klipper/klippy/extras"

def log(message, level="INFO"):
    print(f"[{level}] {message}")

def check_file_exists(path):
    return os.path.exists(path)

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

def install_resonance_tester():
    """Install the custom resonance tester"""
    log("Installing custom resonance tester...")
    
    resonance_src = REPO_ROOT / "patches" / "resonance_tester.py"
    resonance_dst = Path(KLIPPER_EXTRAS_DIR) / "resonance_tester.py"
    if not copy_file(resonance_src, resonance_dst):
        return False
        
    log("resonance_tester.py installed successfully")
    return True

def main():
    parser = argparse.ArgumentParser(description="Resonance Tester Installer")
    parser.parse_args()
    
    # Check if running as root
    if os.geteuid() != 0:
        log("This installer must be run as root (use sudo)", "ERROR")
        sys.exit(1)
    
    try:
        success = install_resonance_tester()
        sys.exit(0 if success else 1)
    except KeyboardInterrupt:
        log("Installation interrupted by user", "ERROR")
        sys.exit(1)
    except Exception as e:
        log(f"Installation failed with error: {e}", "ERROR")
        sys.exit(1)

if __name__ == "__main__":
    main()
