#!/usr/bin/env python3

import os
import sys
import shutil
import argparse
from pathlib import Path

# Configuration
REPO_ROOT = Path(__file__).parent.parent.absolute()
INIT_D_DIR = "/etc/init.d"
MOONRAKER_ASVC_FILE = "/mnt/UDISK/printer_data/moonraker.asvc"

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

def install_cleanup_service():
    """Install the cleanup service"""
    log("Installing cleanup service...")
    
    # Copy the service file
    service_src = REPO_ROOT / "services" / "cleanup_printer_backups"
    service_dst = Path(INIT_D_DIR) / "cleanup_printer_backups"
    if not copy_file(service_src, service_dst):
        return False
        
    # Make it executable
    try:
        os.chmod(service_dst, 0o755)
        log("Made cleanup service executable")
    except Exception as e:
        log(f"Failed to chmod service file: {e}", "ERROR")
        return False
        
    # Check if service is already in moonraker.asvc
    if not check_file_exists(MOONRAKER_ASVC_FILE):
        log("moonraker.asvc not found - cannot add service", "ERROR")
        return False
        
    with open(MOONRAKER_ASVC_FILE, 'r') as f:
        content = f.read()
        
    if 'cleanup_printer_backups' in content:
        log("cleanup_printer_backups already in moonraker.asvc")
    else:
        # Add service to moonraker.asvc
        try:
            with open(MOONRAKER_ASVC_FILE, 'a') as f:
                if not content.endswith('\n'):
                    f.write('\n')
                f.write('cleanup_printer_backups\n')
            log("Added cleanup_printer_backups to moonraker.asvc")
        except Exception as e:
            log(f"Failed to update moonraker.asvc: {e}", "ERROR")
            return False
            
    log("Cleanup service installed successfully")
    return True

def main():
    parser = argparse.ArgumentParser(description="Cleanup Service Installer")
    parser.parse_args()
    
    # Check if running as root
    if os.geteuid() != 0:
        log("This installer must be run as root (use sudo)", "ERROR")
        sys.exit(1)
    
    try:
        success = install_cleanup_service()
        sys.exit(0 if success else 1)
    except KeyboardInterrupt:
        log("Installation interrupted by user", "ERROR")
        sys.exit(1)
    except Exception as e:
        log(f"Installation failed with error: {e}", "ERROR")
        sys.exit(1)

if __name__ == "__main__":
    main()
