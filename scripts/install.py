#!/usr/bin/env python3

import os
import sys
import subprocess
import shutil
import re
import argparse
from pathlib import Path

# Configuration - paths relative to the cloned repository
REPO_ROOT = Path(__file__).parent.parent.absolute()

class PrinterInstaller:
    def __init__(self):
        self.verbose = True  # Always verbose by default
        
    def log(self, message, level="INFO"):
        print(f"[{level}] {message}", flush=True)
        
    def run_command(self, command, capture_output=True):
        """Run a command locally on the printer"""
        if self.verbose:
            self.log(f"Running: {command}")
        
        try:
            result = subprocess.run(command, shell=True, capture_output=capture_output, text=True)
            if self.verbose and result.stdout:
                self.log(f"STDOUT: {result.stdout.strip()}")
            if result.stderr:
                self.log(f"STDERR: {result.stderr.strip()}")
            return result
        except Exception as e:
            self.log(f"Command failed: {e}", "ERROR")
            return None
            
    def check_file_exists(self, path):
        """Check if a file or path exists"""
        return os.path.exists(path)
        
    def check_dir_exists(self, path):
        """Check if a directory exists"""
        return os.path.isdir(path)
        

    def is_git_repository(self, path):
        """Return True if the provided path is a Git repository."""
        return os.path.isdir(os.path.join(path, ".git"))

    def get_current_git_branch(self, repo_path):
        """Get the current git branch for the repository."""
        result = self.run_command(f"git -C '{repo_path}' rev-parse --abbrev-ref HEAD")
        if result and result.returncode == 0:
            branch = (result.stdout or "").strip()
            return branch if branch else None
        return None

    def prompt_user_conflict_resolution(self):
        """Prompt the user for how to resolve git pull conflicts."""
        while True:
            print("\nGit pull encountered conflicts or failed.")
            print("Choose an option:")
            print("  [a] Abort install (do nothing)")
            print("  [f] Force pull (discard local changes) and install")
            print("  [i] Install without pulling")
            choice = input("Enter choice [a/f/i]: ").strip().lower()
            if choice in ("a", "f", "i"):
                return choice
            print("Invalid choice. Please enter 'a', 'f', or 'i'.")

    def update_repository(self):
        """Detect repo root and attempt to pull latest changes from origin.

        Returns True to continue installation, False to abort.
        """
        repo_path = str(REPO_ROOT)
        self.log(f"Detected repository path: {repo_path}")

        if not self.is_git_repository(repo_path):
            self.log("Not a Git repository. Skipping git pull.")
            return True

        current_branch = self.get_current_git_branch(repo_path)
        if not current_branch:
            self.log("Unable to determine current git branch. Skipping git pull.", "WARN")
            return True

        self.log(f"Current branch: {current_branch}")

        pull_result = self.run_command(f"git -C '{repo_path}' pull origin {current_branch}")

        if pull_result and pull_result.returncode == 0:
            self.log("Repository updated successfully.")
            return True

        # Handle conflicts or pull failures
        choice = self.prompt_user_conflict_resolution()
        if choice == "a":
            self.log("User chose to abort installation due to git conflicts.", "WARN")
            return False
        if choice == "i":
            self.log("Proceeding with installation without pulling updates.", "WARN")
            return True

        # Force pull path: discard local changes and match origin
        self.log("Forcing repository to match origin (discarding local changes).", "WARN")
        fetch_res = self.run_command(f"git -C '{repo_path}' fetch origin")
        if not fetch_res or fetch_res.returncode != 0:
            self.log("Failed to fetch from origin. Cannot force pull.", "ERROR")
            return False
        reset_res = self.run_command(f"git -C '{repo_path}' reset --hard origin/{current_branch}")
        if not reset_res or reset_res.returncode != 0:
            self.log("Failed to reset to origin. Cannot continue.", "ERROR")
            return False
        self.log("Repository forcibly updated to match origin.")
        return True

            
    def run_installer(self, component_name, script_name, extra_args=None):
        """Generic method to run any installer script"""
        installer_path = REPO_ROOT / "scripts" / script_name
        if not self.check_file_exists(installer_path):
            self.log(f"{component_name} install script not found", "ERROR")
            return False

        # Execute with live output (always verbose)
        try:
            # Force unbuffered output from child installers
            args_str = " " + " ".join(extra_args) if extra_args else ""
            command = f"PYTHONUNBUFFERED=1 python3 -u '{installer_path}'{args_str}"
            process = subprocess.Popen(
                command,
                shell=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                universal_newlines=True,
                bufsize=1,
            )
            for line in iter(process.stdout.readline, ''):
                if line:
                    print(line.rstrip())
            process.stdout.close()
            return_code = process.wait()

            if return_code == 0:
                self.log(f"{component_name} installation completed successfully")
                return True
            else:
                self.log(f"{component_name} installation failed with return code {return_code}", "ERROR")
                return False
        except Exception as e:
            self.log(f"Failed to run {component_name} installer: {e}", "ERROR")
            return False

    def install_ustreamer(self):
        """Install ustreamer using the existing script"""
        return self.run_installer("ustreamer", "ustreamer_install.py")
            
    def install_kamp(self):
        """Install KAMP configuration files"""
        return self.run_installer("KAMP", "kamp_install.py")
        
    def install_overrides(self):
        """Install overrides.cfg to custom config directory"""
        return self.run_installer("overrides", "overrides_install.py")
        
    def install_cleanup_service(self):
        """Install the cleanup service"""
        return self.run_installer("cleanup service", "cleanup_install.py")
        
    def install_resonance_tester(self):
        """Install the custom resonance tester"""
        return self.run_installer("resonance tester", "resonance_install.py")
        
    def install_timelapse(self):
        """Install moonraker-timelapse component"""
        return self.run_installer("timelapse", "timelapse_install.py")

    def install_timelapse_h264(self):
        """Install moonraker-timelapse component with H264 encoder"""
        return self.run_installer("timelapse (H264)", "timelapse_install.py", extra_args=["--encoder", "h264"])
        
    def modify_bed_mesh(self):
        """Modify bed_mesh.py to change minval parameter"""
        return self.run_installer("bed_mesh", "bed_mesh_install.py")
        
    def install_mainsail(self):
        """Install Mainsail web interface"""
        return self.run_installer("mainsail", "mainsail_install.py")
            
    def verify_installation(self, components=None):
        """Deprecated: central verification removed. Each installer now verifies itself."""
        return True
        
    def run_installation(self, components=None):
        """Run the complete installation"""
        if components is None:
            components = ['ustreamer', 'overrides', 'cleanup', 'resonance', 'bed_mesh', 'timelapse', 'mainsail']
            
        self.log("Starting 3D Printer Installation...")
        self.log(f"Components to install: {', '.join(components)}")
        
        results = {}
        
        if 'ustreamer' in components:
            print("\n" + ("#"*60) + "\n[INFO] Running ustreamer installer\n" + ("#"*60) + "\n", flush=True)
            results['ustreamer'] = self.install_ustreamer()
            
            
        if 'kamp' in components:
            print("\n" + ("#"*60) + "\n[INFO] Running kamp installer\n" + ("#"*60) + "\n", flush=True)
            results['kamp'] = self.install_kamp()
            
            
        if 'overrides' in components:
            print("\n" + ("#"*60) + "\n[INFO] Running overrides installer\n" + ("#"*60) + "\n", flush=True)
            results['overrides'] = self.install_overrides()
            
            
        if 'cleanup' in components:
            print("\n" + ("#"*60) + "\n[INFO] Running cleanup installer\n" + ("#"*60) + "\n", flush=True)
            results['cleanup'] = self.install_cleanup_service()
            
            
        if 'resonance' in components:
            print("\n" + ("#"*60) + "\n[INFO] Running resonance installer\n" + ("#"*60) + "\n", flush=True)
            results['resonance'] = self.install_resonance_tester()
            
            
        if 'timelapse' in components:
            print("\n" + ("#"*60) + "\n[INFO] Running timelapse installer\n" + ("#"*60) + "\n", flush=True)
            results['timelapse'] = self.install_timelapse()
        
        if 'timelapseh264' in components:
            print("\n" + ("#"*60) + "\n[INFO] Running timelapse (H264) installer\n" + ("#"*60) + "\n", flush=True)
            results['timelapseh264'] = self.install_timelapse_h264()
            
            
        if 'bed_mesh' in components:
            print("\n" + ("#"*60) + "\n[INFO] Running bed_mesh installer\n" + ("#"*60) + "\n", flush=True)
            results['bed_mesh'] = self.modify_bed_mesh()
            
            
        if 'mainsail' in components:
            print("\n" + ("#"*60) + "\n[INFO] Running mainsail installer\n" + ("#"*60) + "\n", flush=True)
            results['mainsail'] = self.install_mainsail()
            
            
            
        # Print summary
        self.log("\n" + "="*50)
        self.log("INSTALLATION SUMMARY")
        self.log("="*50)
        
        for component, success in results.items():
            status = "✓ SUCCESS" if success else "✗ FAILED"
            self.log(f"{component:15} : {status}")
            
        all_success = all(results.values())
        if all_success:
            self.log("\n🎉 All components installed successfully!")
        else:
            self.log("\n⚠️  Some components failed to install. Check the logs above.")
            
        return all_success

def main():
    parser = argparse.ArgumentParser(description="3D Printer Automated Installer")
    parser.add_argument("--components", nargs="+", 
                       choices=['ustreamer', 'kamp', 'overrides', 'cleanup', 'resonance', 'bed_mesh', 'timelapse', 'timelapseh264', 'mainsail'],
                       help="Specific components to install (default: all)")
    
    args = parser.parse_args()
    
    if os.geteuid() != 0:
        print("ERROR: This installer must be run as root (use sudo)")
        sys.exit(1)
    
    installer = PrinterInstaller()
    
    # Update repository first
    continue_install = installer.update_repository()
    if not continue_install:
        # User chose to abort; do nothing further
        sys.exit(0)
    
    try:
        success = installer.run_installation(components=args.components)
        sys.exit(0 if success else 1)
    except KeyboardInterrupt:
        print("\n\nInstallation interrupted by user")
        sys.exit(1)
    except Exception as e:
        print(f"\n\nInstallation failed with error: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()