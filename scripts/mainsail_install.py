#!/usr/bin/env python3

import os
import sys
import shutil
import subprocess
import argparse
from pathlib import Path

# Configuration
REPO_ROOT = Path(__file__).parent.parent.absolute()

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

def install_mainsail():
    """Install Mainsail web interface"""
    log("Installing Mainsail web interface...")
    
    # Step 1: Create directory and cd there
    mainsail_dir = "/mnt/UDISK/root/mainsail"
    log(f"Preparing directory: {mainsail_dir}")
    
    try:
        # If mainsail dir exists already, remove it to avoid unzip prompts/hangs
        if os.path.exists(mainsail_dir):
            log("Existing Mainsail directory found. Removing it before reinstall...")
            shutil.rmtree(mainsail_dir)
        os.makedirs(mainsail_dir, exist_ok=True)
        os.chdir(mainsail_dir)
        log(f"Changed to directory: {mainsail_dir}")
    except Exception as e:
        log(f"Failed to create/change to directory: {e}", "ERROR")
        return False
    
    # Step 2: Download and extract mainsail
    # Prefer absolute paths for wget/unzip to avoid PATH issues on non-interactive shells
    wget_bin = "/opt/bin/wget" if os.path.exists("/opt/bin/wget") else "wget"
    unzip_bin = "/opt/bin/unzip" if os.path.exists("/opt/bin/unzip") else "unzip"

    log("Downloading Mainsail...")
    download_command = f"{wget_bin} -q -O mainsail.zip https://github.com/Jacob10383/mainsail/releases/latest/download/mainsail.zip"
    result = run_command(download_command)
    if not result or result.returncode != 0:
        log("Failed to download Mainsail", "ERROR")
        return False
    
    log("Extracting Mainsail...")
    # -o overwrite without prompting, -q quiet to reduce noise in logs
    extract_command = f"{unzip_bin} -o -q mainsail.zip"
    result = run_command(extract_command)
    if not result or result.returncode != 0:
        log("Failed to extract Mainsail", "ERROR")
        return False
    
    log("Cleaning up zip file...")
    cleanup_command = "rm mainsail.zip"
    result = run_command(cleanup_command)
    if not result or result.returncode != 0:
        log("Failed to remove zip file", "ERROR")
        return False
    
    # Step 3: Create symlink to /usr/share/
    log("Creating symlink to /usr/share/mainsail...")
    
    # Remove existing symlink if it exists
    usr_share_mainsail = "/usr/share/mainsail"
    if os.path.exists(usr_share_mainsail):
        if os.path.islink(usr_share_mainsail):
            os.unlink(usr_share_mainsail)
            log("Removed existing symlink")
        else:
            shutil.rmtree(usr_share_mainsail)
            log("Removed existing directory")
    
    # Create new symlink
    try:
        os.symlink(mainsail_dir, usr_share_mainsail)
        log("Created symlink from /mnt/UDISK/root/mainsail to /usr/share/mainsail")
    except Exception as e:
        log(f"Failed to create symlink: {e}", "ERROR")
        return False
    
    # Step 4: Replace nginx config
    log("Replacing nginx configuration...")
    nginx_conf_src = REPO_ROOT / "patches" / "nginx.conf"
    nginx_conf_dst = "/etc/nginx/nginx.conf"
    
    if not check_file_exists(nginx_conf_src):
        log(f"Source nginx config not found: {nginx_conf_src}", "ERROR")
        return False
    
    try:
        shutil.copy2(nginx_conf_src, nginx_conf_dst)
        log("Replaced /etc/nginx/nginx.conf")
    except Exception as e:
        log(f"Failed to replace nginx config: {e}", "ERROR")
        return False
    
    # Step 4.5: Add update manager to moonraker.conf
    log("Adding Mainsail update manager to moonraker.conf...")
    moonraker_conf = "/mnt/UDISK/printer_data/config/moonraker.conf"
    
    if not check_file_exists(moonraker_conf):
        log("moonraker.conf not found - cannot add update manager", "ERROR")
        return False
    
    with open(moonraker_conf, 'r') as f:
        content = f.read()
        
    if '[update_manager mainsail]' in content:
        log("[update_manager mainsail] already exists in moonraker.conf")
    else:
        # Add the update manager section
        # Ensure file ends with a newline before appending
        if not content.endswith('\n'):
            content += '\n'
        content += '[update_manager mainsail]\n'
        content += 'type: web\n'
        content += 'channel: stable\n'
        content += 'repo: mainsail-crew/mainsail\n'
        content += 'path: ~root/mainsail\n'
            
        # Write back to file
        with open(moonraker_conf, 'w') as f:
            f.write(content)
        log("Added [update_manager mainsail] section to moonraker.conf")
    
    # Step 5: Restart nginx
    log("Restarting nginx...")
    restart_command = "/etc/init.d/nginx restart"
    result = run_command(restart_command)
    if not result or result.returncode != 0:
        log("Failed to restart nginx", "ERROR")
        return False
    
    log("Mainsail installation completed successfully")
    return True

def main():
    parser = argparse.ArgumentParser(description="Mainsail Web Interface Installer")
    parser.parse_args()
    
    # Check if running as root
    if os.geteuid() != 0:
        log("This installer must be run as root (use sudo)", "ERROR")
        sys.exit(1)
    
    try:
        success = install_mainsail()
        sys.exit(0 if success else 1)
    except KeyboardInterrupt:
        log("Installation interrupted by user", "ERROR")
        sys.exit(1)
    except Exception as e:
        log(f"Installation failed with error: {e}", "ERROR")
        sys.exit(1)

if __name__ == "__main__":
    main()
