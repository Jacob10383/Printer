#!/usr/bin/env python3

import os
import sys
import subprocess
import argparse
from pathlib import Path

# Configuration
KLIPPER_EXTRAS_DIR = "/usr/share/klipper/klippy/extras"

def log(message, level="INFO"):
    print(f"[{level}] {message}")

def check_file_exists(path):
    return os.path.exists(path)

def run_command(command):
    """Run a command and return the result"""
    try:
        result = subprocess.run(command, shell=True, capture_output=True, text=True)
        return result
    except Exception as e:
        log(f"Command failed: {e}", "ERROR")
        return None

def modify_bed_mesh():
    """Modify bed_mesh.py to change minval parameter"""
    log("Modifying bed_mesh.py...")
    
    bed_mesh_path = Path(KLIPPER_EXTRAS_DIR) / "bed_mesh.py"
    if not check_file_exists(bed_mesh_path):
        log("bed_mesh.py not found", "ERROR")
        return False
        
    # Check if the modification is already applied
    try:
        with open(bed_mesh_path, 'r') as f:
            content = f.read()
    except Exception as e:
        log(f"Failed to read bed_mesh.py: {e}", "ERROR")
        return False
        
    if 'minval=1.' in content:
        log("bed_mesh.py already has minval=1. (modification not needed)")
        return True
        
    # Apply the sed modification
    sed_command = f"sed -i '/move_check_distance.*minval=3\\./s/minval=3\\./minval=1./' '{bed_mesh_path}'"
    
    result = run_command(sed_command)
    if result and result.returncode == 0:
        log("bed_mesh.py modified successfully")
        return True
    else:
        log("Failed to modify bed_mesh.py", "ERROR")
        return False

def main():
    parser = argparse.ArgumentParser(description="Bed Mesh Modifier")
    parser.parse_args()
    
    # Check if running as root
    if os.geteuid() != 0:
        log("This installer must be run as root (use sudo)", "ERROR")
        sys.exit(1)
    
    try:
        success = modify_bed_mesh()
        sys.exit(0 if success else 1)
    except KeyboardInterrupt:
        log("Installation interrupted by user", "ERROR")
        sys.exit(1)
    except Exception as e:
        log(f"Installation failed with error: {e}", "ERROR")
        sys.exit(1)

if __name__ == "__main__":
    main()
