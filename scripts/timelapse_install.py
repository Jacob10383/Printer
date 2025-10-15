#!/usr/bin/env python3

import os
import sys
import shutil
import subprocess
import argparse
from pathlib import Path

# Configuration
REPO_ROOT = Path(__file__).parent.parent.absolute()
BASE_CONFIG_DIR = "/mnt/UDISK/printer_data/config"
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

def add_include_to_main_cfg(include_line):
    """Ensure include line is the FIRST line of custom/main.cfg"""
    main_cfg = Path(CUSTOM_CONFIG_DIR) / "main.cfg"
    
    # Do not create files/dirs; fail fast if missing
    if not check_file_exists(main_cfg):
        log("custom/main.cfg not found - cannot add include line", "ERROR")
        return False

    try:
        with open(main_cfg, 'r') as f:
            content = f.read()

        lines = content.splitlines()
        # If already first line, nothing to do
        if lines and lines[0].strip() == include_line:
            log(f"{include_line} already present as the first line in main.cfg")
            return True

        # Remove any existing occurrences of the include line
        filtered_lines = [line for line in lines if line.strip() != include_line]
        # Insert include line at the very top
        new_lines = [include_line] + filtered_lines
        new_content = '\n'.join(new_lines)
        if not new_content.endswith('\n'):
            new_content += '\n'

        with open(main_cfg, 'w') as f:
            f.write(new_content)
        log("Added timelapse include to main.cfg")
        return True
    except Exception as e:
        log(f"Failed to update main.cfg: {e}", "ERROR")
        return False

def add_timelapse_to_moonraker_conf():
    """Ensure [timelapse] exists and contains desired output_path in moonraker.conf"""
    moonraker_conf = Path(BASE_CONFIG_DIR) / "moonraker.conf"
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

def apply_mjpeg_patch(tl_content):
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

    return tl_content, tl_content != original_content


def apply_h264_patch(tl_content):
    original_content = tl_content

    # Replace any mjpeg or libx264 usage with desired h264 command shape
    # Normalize both -vcodec and -c:v flags to libx264
    tl_content = tl_content.replace("-vcodec mjpeg", "-vcodec libx264")
    tl_content = tl_content.replace("-c:v mjpeg", "-c:v libx264")
    # Ensure -c:v libx264 exists
    tl_content = tl_content.replace("-vcodec libx264", "-c:v libx264")

    # Set preset and tune stillimage
    # Insert/replace preset and tune after codec declaration when present
    tl_content = tl_content.replace("-c:v libx264", "-c:v libx264 -preset ultrafast -tune stillimage")

    # Set GOP to fps: replace occurrences of " -threads 2 -g 5" or " -g 5" with dynamic fps placeholder
    # We cannot know fps at install time, so we replace any fixed -g 5 with " -g " + str(fps) at runtime pattern.
    # The original code builds: " -g " + str(fps) when using our desired form; ensure we remove hard-coded 5.
    tl_content = tl_content.replace(" -g 5", "")

    # Ensure CRF remains CRF for h264
    tl_content = tl_content.replace(" -q:v ", " -crf ")

    # Keep pix_fmt, an, extra params and output as-is
    return tl_content, tl_content != original_content


def install_timelapse(encoder="mjpeg"):
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
    
    # After copying, patch the component encoder based on selection
    try:
        with open(timelapse_dst, 'r') as f:
            tl_content = f.read()

        if encoder == "h264":
            tl_content, changed = apply_h264_patch(tl_content)
        else:
            tl_content, changed = apply_mjpeg_patch(tl_content)

        if changed:
            with open(timelapse_dst, 'w') as f:
                f.write(tl_content)
            log(f"Patched timelapse.py to use {encoder.upper()} encoding for timelapse videos")
        else:
            log("timelapse.py did not contain expected codec strings; no codec patch applied", "INFO")
    except Exception as e:
        log(f"Failed to patch timelapse.py for {encoder.upper()}: {e}", "ERROR")
        return False
        
    # Copy timelapse.cfg to custom config directory
    timelapse_cfg_src = Path(temp_dir) / "klipper_macro" / "timelapse.cfg"
    timelapse_cfg_dst = Path(CUSTOM_CONFIG_DIR) / "timelapse.cfg"
    
    if not check_file_exists(timelapse_cfg_src):
        log(f"Source file not found: {timelapse_cfg_src}", "ERROR")
        return False
        
    # Do not create destination directory; require it to exist
    if not os.path.isdir(CUSTOM_CONFIG_DIR):
        log(f"Custom config directory not found: {CUSTOM_CONFIG_DIR}", "ERROR")
        return False
    if not copy_file(timelapse_cfg_src, timelapse_cfg_dst):
        return False
        
    # Add include to the FIRST line of custom/main.cfg
    if not add_include_to_main_cfg('[include timelapse.cfg]'):
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
    parser.add_argument("--encoder", choices=["mjpeg", "h264"], default="mjpeg", help="Select encoder to patch in")
    
    args = parser.parse_args()
    
    # Check if running as root
    if os.geteuid() != 0:
        log("This installer must be run as root (use sudo)", "ERROR")
        sys.exit(1)
    
    try:
        success = install_timelapse(encoder=args.encoder)
        sys.exit(0 if success else 1)
    except KeyboardInterrupt:
        log("Installation interrupted by user", "ERROR")
        sys.exit(1)
    except Exception as e:
        log(f"Installation failed with error: {e}", "ERROR")
        sys.exit(1)

if __name__ == "__main__":
    main()
