#!/usr/bin/env python3

import os
import sys
import shutil
import subprocess
import argparse
from pathlib import Path

# Configuration
REPO_ROOT = Path(__file__).parent.parent.absolute()
CONFIG_DIR = "/mnt/UDISK/printer_data/config"

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

def run_command(command):
    """Run a command and return the result"""
    try:
        result = subprocess.run(command, shell=True, capture_output=True, text=True)
        return result
    except Exception as e:
        log(f"Command failed: {e}", "ERROR")
        return None

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

def add_timelapse_to_moonraker_conf():
    """Ensure [timelapse] exists and contains desired output_path in moonraker.conf"""
    moonraker_conf = Path(CONFIG_DIR) / "moonraker.conf"
    if not check_file_exists(moonraker_conf):
        log("moonraker.conf not found - cannot add timelapse section", "ERROR")
        return False
        
    with open(moonraker_conf, 'r') as f:
        content = f.read()

    lines = content.splitlines()
    section_start_index = None
    desired_output_line = 'output_path: /mnt/UDISK/root/timelapse'

    # Locate the [timelapse] section header exactly
    for index, line in enumerate(lines):
        if line.strip() == '[timelapse]':
            section_start_index = index
            break

    # If section is missing, append it along with the desired output_path
    if section_start_index is None:
        if content and not content.endswith('\n'):
            content += '\n'
        content += '[timelapse]\n' + desired_output_line + '\n'
        with open(moonraker_conf, 'w') as f:
            f.write(content)
        log("Added [timelapse] section and output_path to moonraker.conf")
        return True

    # Find the end of the [timelapse] section (next section header or EOF)
    section_end_index = len(lines)
    for scan_index in range(section_start_index + 1, len(lines)):
        stripped = lines[scan_index].strip()
        if stripped.startswith('[') and stripped.endswith(']'):
            section_end_index = scan_index
            break

    # Check if output_path already exists in the section (support both ':' and '=')
    has_output_path = False
    for section_line_index in range(section_start_index + 1, section_end_index):
        stripped = lines[section_line_index].strip()
        if stripped.startswith('output_path:') or stripped.startswith('output_path ='):
            has_output_path = True
            break

    # Insert desired output_path if missing
    if not has_output_path:
        insert_index = section_start_index + 1
        lines.insert(insert_index, desired_output_line)
        new_content = '\n'.join(lines)
        if not new_content.endswith('\n'):
            new_content += '\n'
        with open(moonraker_conf, 'w') as f:
            f.write(new_content)
        log("Added output_path to existing [timelapse] in moonraker.conf")
        return True

    log("[timelapse] section already exists in moonraker.conf")
    return True

def install_timelapse():
    """Install moonraker-timelapse component"""
    log("Installing moonraker-timelapse component...")
    
    # Define paths
    moonraker_components_dir = "/mnt/UDISK/root/moonraker/moonraker/components"
    temp_dir = "/tmp/moonraker-timelapse"
    
    # Clone the repository
    # Remove existing temp directory if it exists
    if os.path.exists(temp_dir):
        shutil.rmtree(temp_dir)
        
    git_bin = "/opt/bin/git" if os.path.exists("/opt/bin/git") else "git"
    clone_command = f"{git_bin} clone --depth 1 --quiet https://github.com/mainsail-crew/moonraker-timelapse.git /tmp/moonraker-timelapse"
    result = run_command(clone_command)
    if not result or result.returncode != 0:
        log("Failed to clone moonraker-timelapse repository", "ERROR")
        return False
    log("Successfully cloned moonraker-timelapse repository")
    
    # Copy timelapse.py component
    timelapse_src = Path(temp_dir) / "component" / "timelapse.py"
    timelapse_dst = Path(moonraker_components_dir) / "timelapse.py"
    
    if not check_file_exists(timelapse_src):
        log(f"Source file not found: {timelapse_src}", "ERROR")
        return False
        
    if not copy_file(timelapse_src, timelapse_dst):
        return False
    
    # After copying, patch the component to use MJPEG for low CPU usage
    try:
        with open(timelapse_dst, 'r') as f:
            tl_content = f.read()

        original_content = tl_content

        # Prefer codec mjpeg over libx264
        tl_content = tl_content.replace("-vcodec libx264", "-vcodec mjpeg")
        tl_content = tl_content.replace("-c:v libx264", "-c:v mjpeg")

        # Map CRF (x264) to q:v (mjpeg quality). Keep the same numeric value.
        tl_content = tl_content.replace(" -crf ", " -q:v ")

        # Remove GOP size flag which is not applicable to MJPEG
        tl_content = tl_content.replace(" -threads 2 -g 5", " -threads 2")
        tl_content = tl_content.replace(" -g 5", "")

        # Improve MP4 playback start without re-encode cost
        tl_content = tl_content.replace(" -an", " -an -movflags +faststart")



        if tl_content != original_content:
            with open(timelapse_dst, 'w') as f:
                f.write(tl_content)
            log("Patched timelapse.py to use MJPEG encoding for timelapse videos")
        else:
            log("timelapse.py did not contain libx264/CRF strings; no codec patch applied", "INFO")
    except Exception as e:
        log(f"Failed to patch timelapse.py for MJPEG: {e}", "ERROR")
        return False
        
    # Copy timelapse.cfg to config directory
    timelapse_cfg_src = Path(temp_dir) / "klipper_macro" / "timelapse.cfg"
    timelapse_cfg_dst = Path(CONFIG_DIR) / "timelapse.cfg"
    
    if not check_file_exists(timelapse_cfg_src):
        log(f"Source file not found: {timelapse_cfg_src}", "ERROR")
        return False
        
    if not copy_file(timelapse_cfg_src, timelapse_cfg_dst):
        return False
        
    # Add include to printer.cfg
    if not add_include_to_printer_cfg('[include timelapse.cfg]'):
        return False
        
    # Add [timelapse] section to moonraker.conf
    if not add_timelapse_to_moonraker_conf():
        return False
        
    # Clean up temporary directory
    if os.path.exists(temp_dir):
        shutil.rmtree(temp_dir)
        log("Cleaned up temporary directory")
        
    # Restart moonraker and klipper to load the new component and config
    restart_command = "/etc/init.d/moonraker restart && /etc/init.d/klipper restart"
    result = run_command(restart_command)
    if result and result.returncode == 0:
        log("moonraker and klipper restarted successfully")
    else:
        log("Failed to restart moonraker and klipper", "ERROR")
        return False
        
    log("moonraker-timelapse component installed successfully")
    return True

def main():
    parser = argparse.ArgumentParser(description="Moonraker Timelapse Installer")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be done without actually doing it")
    
    args = parser.parse_args()
    
    # Check if running as root
    if os.geteuid() != 0:
        log("This installer must be run as root (use sudo)", "ERROR")
        sys.exit(1)
    
    if args.dry_run:
        log("DRY RUN: Would install moonraker-timelapse component")
        sys.exit(0)
    
    try:
        success = install_timelapse()
        sys.exit(0 if success else 1)
    except KeyboardInterrupt:
        log("Installation interrupted by user", "ERROR")
        sys.exit(1)
    except Exception as e:
        log(f"Installation failed with error: {e}", "ERROR")
        sys.exit(1)

if __name__ == "__main__":
    main()
