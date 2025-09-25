#!/usr/bin/env python3
"""
Usage:
    python3 fullinstaller.py <printer_ip> [branch_name]
Example:
    python3 fullinstaller.py 192.168.1.4 jac
"""

import sys
import subprocess
import time
import argparse
import logging
import select
import socket
import shlex
import re
import os
from pathlib import Path
from typing import Optional
from datetime import datetime


class InstallerError(Exception):
    """Fatal installer error that should stop the flow."""


class PrinterInstaller:
    def __init__(self, printer_ip: str, branch: str, password: Optional[str] = None, reset: bool = False, preserve_stats: bool = False):
        self.printer_ip = printer_ip
        self.branch = branch
        if password is None:
            raise ValueError("Password is required")
        self.password = password
        self.reset = reset
        self.preserve_stats = preserve_stats
        self.username = "root"
        self.ssh_host = f"{self.username}@{self.printer_ip}"
        # Ensure Entware binaries are available
        self.remote_path_export = "export PATH=/opt/bin:/opt/sbin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin;"
        # non-interactive, resilient behavior (detect silent drops quickly)
        self.ssh_prefix = (
            f"ssh -o BatchMode=yes -o StrictHostKeyChecking=no -o ConnectTimeout=10 "
            f"-o ServerAliveInterval=3 -o ServerAliveCountMax=1 {self.ssh_host}"
        )
        self.bootstrap_path = Path(__file__).parent / "bootstrap"
        self.bootstrap_tar = Path(__file__).parent / "bootstrap.tar.gz"
        self.remote_bootstrap_path = "/mnt/UDISK/printer_data/config/bootstrap"
        self.log_file = f"printer_install_{printer_ip}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
        self.start_time = time.time()
        # Backup state
        self.moonraker_backup_dir: Optional[Path] = None
        self.moonraker_backup_files: dict[str, Path] = {}
        self.setup_logging()
        
    def setup_logging(self):
        """Setup logging to both console and file"""
        self.logger = logging.getLogger('printer_installer')
        self.logger.setLevel(logging.DEBUG)

        self.logger.handlers.clear()

        class _ConsoleFilter(logging.Filter):
            def filter(self, record):
                return getattr(record, 'to_console', True)

        class _FileFilter(logging.Filter):
            def filter(self, record):
                return getattr(record, 'to_file', True)

        class _ColorFormatter(logging.Formatter):
            def format(self, record):
                msg = super().format(record)
                if getattr(record, 'is_step', False):
                    return f"\033[96m{msg}\033[0m"  # cyan
                return msg

        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setLevel(logging.INFO)
        console_handler.addFilter(_ConsoleFilter())
        console_handler.setFormatter(_ColorFormatter('%(message)s'))
        self.logger.addHandler(console_handler)

        file_handler = logging.FileHandler(self.log_file)
        file_handler.setLevel(logging.DEBUG)
        file_handler.addFilter(_FileFilter())
        file_format = logging.Formatter('[%(asctime)s] [%(levelname)s] %(message)s', datefmt='%Y-%m-%d %H:%M:%S')
        file_handler.setFormatter(file_format)
        self.logger.addHandler(file_handler)

        self.logger.info(f"Logging to file: {self.log_file}")
        
    def log(self, message: str, level: str = "INFO"):
        """Log message to both console and file handlers"""
        if level == "ERROR":
            self.logger.error(message)
        elif level == "WARNING":
            self.logger.warning(message)
        else:
            self.logger.info(message)
            
    def file_log(self, message: str, level: str = "INFO"):
        if level == "ERROR":
            self.logger.error(message, extra={"to_console": False})
        elif level == "WARNING":
            self.logger.warning(message, extra={"to_console": False})
        else:
            self.logger.info(message, extra={"to_console": False})

    def log_step(self, step_number: int, title: str):
        """Log a structured step banner to file and a concise one to console."""
        self.logger.info(f"=== STEP {step_number}: {title} ===", extra={"is_step": True})

    def _build_ssh_cmd(self, remote_command: str) -> str:
        """Construct an SSH command with standard options and PATH export."""
        return f"{self.ssh_prefix} '{self.remote_path_export} {remote_command}'"
        
    def run_command(self, command: str, check: bool = True, capture_output: bool = True) -> subprocess.CompletedProcess:
        """Run a shell command and return the result"""
        self.file_log(f"Running: {command}")
        try:
            result = subprocess.run(
                command,
                shell=True,
                check=check,
                capture_output=True,
                text=True
            )
            if result.stdout:
                self.logger.debug(f"STDOUT: {result.stdout}")
            if result.stderr:
                self.logger.debug(f"STDERR: {result.stderr}")
            return result
        except subprocess.CalledProcessError as e:
            self.log(f"Command failed: {e}", "ERROR")
            if e.stdout:
                self.file_log(f"STDOUT: {e.stdout}", "ERROR")
            if e.stderr:
                self.file_log(f"STDERR: {e.stderr}", "ERROR")
            raise InstallerError(f"Command execution failed: {command}")
            
    def check_ssh_key(self) -> bool:
        try:
            # Test SSH connection without password
            result = self.run_command(
                f"ssh -o ConnectTimeout=10 -o BatchMode=yes -o StrictHostKeyChecking=no {self.ssh_host} 'echo test'",
                check=False,
                capture_output=True
            )
            return result.returncode == 0
        except Exception as e:
            self.log(f"SSH key check failed: {e}", "ERROR")
            raise InstallerError(f"SSH key validation failed: {e}")
            
    def remove_ssh_key(self):
        """Remove SSH key for the printer IP"""
        try:
            self.file_log("Removing old SSH key...")
            self.run_command(f"ssh-keygen -R {self.printer_ip}", check=False)
            self.file_log("SSH key removed successfully")
        except Exception as e:
            self.file_log(f"Failed to remove SSH key: {e}", "WARNING")
            
    def setup_ssh_key(self):
        """Setup SSH key for passwordless access"""
        try:
            self.file_log("Setting up SSH key...")
            
            self.run_command(
                f"sshpass -p {shlex.quote(self.password)} ssh-copy-id -o StrictHostKeyChecking=no {self.ssh_host}"
            )
            self.file_log("SSH key setup completed")
            
        except Exception as e:
            self.log(f"SSH key setup failed: {e}", "ERROR")
            raise InstallerError(f"SSH key setup failed: {e}")
            
    def ensure_ssh_access(self):
        """Ensure SSH access is working, setup key if needed"""
        if not self.check_ssh_key():
            self.log("SSH key not found or invalid, setting up...")
            # Console should be concise; detailed command output goes to file
            self.file_log("SSH key not found or invalid, setting up...")
            self.remove_ssh_key()
            self.setup_ssh_key()
            if not self.check_ssh_key():
                raise InstallerError("SSH key setup failed - cannot connect without password")
        else:
            self.file_log("SSH key is valid")

    def reset_device(self):
        """Factory reset the device via wipe.sock and wait until it comes back online"""
        self.log("Reset requested before installation. Initiating device wipe...")
        self.ensure_ssh_access()

        self.file_log("Sending reset command via wipe.sock")
        self._run_reset_with_disconnect_handling('echo "all" | /usr/bin/nc -U /var/run/wipe.sock', timeout=60)

        self.log("Reset acknowledged; waiting 30 seconds for device to reboot")
        time.sleep(30)

        start = time.time()
        self.remove_ssh_key()
        while True:
            time.sleep(5)
            port_open = False
            try:
                with socket.create_connection((self.printer_ip, 22), timeout=2):
                    port_open = True
            except Exception:
                port_open = False

            if not port_open:
                continue

            self.log("SSH port is open; verifying login...")
            result = subprocess.run(
                f"sshpass -p {shlex.quote(self.password)} ssh -o ConnectTimeout=5 -o StrictHostKeyChecking=no {self.ssh_host} 'echo online'",
                shell=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True
            )
            if result.returncode == 0 and "online" in (result.stdout or ""):
                break

        elapsed = int(time.time() - start)
        self.log(f"Device back online after {elapsed}s; re-establishing keys")

    def _run_reset_with_disconnect_handling(self, command: str, timeout: int = 60):
        """Run reset command expecting 'ok' then proactively end SSH to avoid hangs."""
        self._run_ssh_streaming(
            remote_command=command,
            timeout=timeout,
            expect_disconnect=True,
            success_tokens=["ok"],
        )

    def _run_ssh_streaming(
        self,
        remote_command: str,
        timeout: Optional[int] = None,
        expect_disconnect: bool = False,
        success_tokens: Optional[list] = None,
        on_line: Optional[callable] = None,
    ) -> str:
        """Run an SSH command with streaming output, timeouts, and optional disconnect expectation."""
        ssh_cmd = self._build_ssh_cmd(remote_command)
        try:
            process = subprocess.Popen(
                ssh_cmd,
                shell=True,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                universal_newlines=True
            )

            output_lines = []
            start_time = time.time()
            token_seen = False

            while True:
                if timeout is not None and (time.time() - start_time) > timeout:
                    try:
                        process.terminate()
                        process.wait(timeout=5)
                    except Exception:
                        process.kill()
                    raise InstallerError(f"Remote command timed out after {timeout} seconds: {remote_command}")

                ready, _, _ = select.select([process.stdout], [], [], 1)
                if ready:
                    line = process.stdout.readline()
                    if line:
                        self.logger.debug(f"REMOTE: {line.strip()}")
                        output_lines.append(line.strip())
                        if on_line is not None:
                            try:
                                on_line(line)
                            except Exception:
                                pass
                        if success_tokens and any(tok in line.lower() for tok in success_tokens):
                            token_seen = True
                            if expect_disconnect:
                                try:
                                    process.terminate()
                                    process.wait(timeout=2)
                                except Exception:
                                    process.kill()
                                break
                        continue

                if process.poll() is not None:
                    remaining = process.stdout.read()
                    if remaining:
                        for rem_line in remaining.splitlines():
                            self.logger.debug(f"REMOTE: {rem_line.strip()}")
                            output_lines.append(rem_line.strip())
                            if on_line is not None:
                                try:
                                    on_line(rem_line)
                                except Exception:
                                    pass
                        if success_tokens and any(tok in remaining.lower() for tok in success_tokens):
                            token_seen = True
                    break

            try:
                return_code = process.wait(timeout=2)
            except Exception:
                process.kill()
                return_code = 255

            # Bootstrap-like flows: treat expected disconnect or known messages as success
            output_text = "\n".join(output_lines)
            lower_output = output_text.lower()
            if expect_disconnect:
                if (
                    token_seen
                    or "logging you out now" in lower_output
                    or "please reconnect to continue" in lower_output
                    or return_code == 255
                ):
                    return output_text
                raise InstallerError(f"Remote command failed (expected disconnect): rc={return_code}; output: {lower_output}")

            if return_code != 0:
                raise InstallerError(f"Remote command failed with return code {return_code}: {remote_command}")

            return output_text
        except Exception as e:
            # Connection closure during expected-disconnect flows is okay
            if expect_disconnect and ("connection closed" in str(e).lower() or "connection reset" in str(e).lower()):
                self.file_log("SSH connection closed (expected)")
                return ""
            raise
            
    def upload_bootstrap(self):
        """Upload bootstrap folder to the printer using SSH+tar (since SCP is not available yet)"""
        if not self.bootstrap_tar.exists():
            raise InstallerError(f"Bootstrap tar file not found at {self.bootstrap_tar}")
            
        self.file_log(f"Uploading bootstrap files to {self.remote_bootstrap_path} using SSH+tar")
        
        # Create remote directory
        self.run_command(self._build_ssh_cmd(f"mkdir -p {self.remote_bootstrap_path}"))
        
        # Upload bootstrap tar file using SSH+tar (since SCP is not available yet)
        ssh_cmd = self._build_ssh_cmd(f"cd {self.remote_bootstrap_path} && tar -xzf -")
        try:
            with open(self.bootstrap_tar, 'rb') as f:
                process = subprocess.Popen(
                    ssh_cmd,
                    shell=True,
                    stdin=f,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE
                )
                stdout, stderr = process.communicate()
                if process.returncode != 0:
                    raise InstallerError(f"Failed to upload bootstrap files: {stderr.decode()}")
        except Exception as e:
            raise InstallerError(f"Failed to upload bootstrap files: {e}")
        
        self.file_log("Bootstrap files uploaded successfully")

    def _rsync_download(self, remote_path: str, local_path: Path):
        """Download a file using rsync over SSH."""
        self.file_log(f"Rsync download: {remote_path} -> {local_path}")
        local_path.parent.mkdir(parents=True, exist_ok=True)
        cmd = (
            f"rsync -e 'ssh -o BatchMode=yes -o StrictHostKeyChecking=no -o ConnectTimeout=10' "
            f"--times --compress --progress {self.ssh_host}:{shlex.quote(remote_path)} {shlex.quote(str(local_path))}"
        )
        self.run_command(cmd)

    def _rsync_upload(self, local_path: Path, remote_path: str):
        """Upload a file using rsync over SSH."""
        self.file_log(f"Rsync upload: {local_path} -> {remote_path}")
        remote_dir = os.path.dirname(remote_path)
        # Ensure remote directory exists before rsync
        self.run_remote_command(f"mkdir -p {remote_dir}")
        cmd = (
            f"rsync -e 'ssh -o BatchMode=yes -o StrictHostKeyChecking=no -o ConnectTimeout=10' "
            f"--times --compress --progress {shlex.quote(str(local_path))} {self.ssh_host}:{shlex.quote(remote_path)}"
        )
        self.run_command(cmd)

    def backup_moonraker_stats(self):
        """Backup Moonraker statistics databases from the device before reset."""
        if not self.preserve_stats:
            return
        self.log("Backing up Moonraker stats before reset...")
        # Ensure SSH connectivity first
        self.ensure_ssh_access()
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        self.moonraker_backup_dir = Path.cwd() / f"moonraker_backup_{self.printer_ip}_{timestamp}"
        self.moonraker_backup_dir.mkdir(parents=True, exist_ok=True)

        remote_db_dir = "/mnt/UDISK/root/printer_data/database"
        targets = [
            f"{remote_db_dir}/data.mdb",
            f"{remote_db_dir}/moonraker-sql.db",
        ]

        succeeded = 0
        failed_files = []
        for remote_file in targets:
            local_path = self.moonraker_backup_dir / Path(remote_file).name
            try:
                # Use rsync for reliability
                self._rsync_download(remote_file, local_path)
                self.moonraker_backup_files[Path(remote_file).name] = local_path
                succeeded += 1
            except Exception as e:
                failed_files.append(remote_file)
                self.file_log(f"Failed to backup {remote_file}: {e}", "ERROR")

        if failed_files:
            self.log(f"Backup failed for {len(failed_files)} file(s): {', '.join(failed_files)}", "ERROR")
            self.log("Aborting installation to prevent data loss.", "ERROR")
            raise InstallerError(f"Moonraker stats backup failed for: {', '.join(failed_files)}")
        
        self.file_log(f"Successfully backed up {succeeded} Moonraker file(s) to {self.moonraker_backup_dir}")
        self.log(f"Backed up {succeeded} Moonraker stats file(s)")

    def restore_moonraker_stats(self):
        """Restore previously backed up Moonraker statistics after installation completes."""
        if not self.preserve_stats:
            return
        if not self.moonraker_backup_files:
            self.file_log("No Moonraker backup files found; skipping restore.")
            return
        self.file_log("Restoring Moonraker stats to device...")
        # Ensure SSH connectivity
        self.ensure_ssh_access()
        
        # Stop Moonraker service before restoring databases
        self.file_log("Stopping Moonraker service...")
        self.run_remote_command("service moonraker stop", wait_for_completion=True)
        
        remote_db_dir = "/mnt/UDISK/root/printer_data/database"
        # Ensure directory exists
        self.run_remote_command(f"mkdir -p {remote_db_dir}")
        restored = 0
        for name, local_path in self.moonraker_backup_files.items():
            if not local_path.exists():
                self.file_log(f"Missing local backup file; skipping: {local_path}", "WARNING")
                continue
            remote_path = f"{remote_db_dir}/{name}"
            try:
                # Use rsync for reliability
                self._rsync_upload(local_path, remote_path)
                restored += 1
            except Exception as e:
                self.file_log(f"Failed to restore {name}: {e}", "ERROR")
        
        # Start Moonraker service after restoring databases
        self.file_log("Starting Moonraker service...")
        self.run_remote_command("service moonraker start", wait_for_completion=True)
        
        if restored == 0:
            self.log("No Moonraker stats files were restored.", "WARNING")
        else:
            self.log(f"Restored {restored} Moonraker stats file(s)")
        
    def run_remote_command(self, command: str, wait_for_completion: bool = True, timeout: int = None) -> str:
        """Run a command on the remote printer with real-time output logging"""
        self.file_log(f"Running remote command: {command}")  # Only log to file
        
        if wait_for_completion:
            return self._run_ssh_streaming(command, timeout=timeout)
        else:
            # Run in background; prepend PATH for Entware binaries
            self.run_command(self._build_ssh_cmd(f"nohup {command} > /dev/null 2>&1 &"))
            return ""
            
    def run_bootstrap_script(self):
        """Run the bootstrap script on the printer"""
        self.file_log("Running bootstrap script...")
        bootstrap_script = f"sh {self.remote_bootstrap_path}/bootstrap.sh"
        
        # This script intentionally disconnects SSH when done
        try:
            self._run_bootstrap_with_disconnect_handling(bootstrap_script)
            self.file_log("Bootstrap script completed successfully")
        except Exception as e:
            raise InstallerError(f"Bootstrap script failed: {e}")
            
    def _run_bootstrap_with_disconnect_handling(self, command: str):
        """Run bootstrap script expecting SSH disconnection"""
        self.file_log(f"Running bootstrap command: {command}")
        self._run_ssh_streaming(command, expect_disconnect=True)
                
    def run_k2_improvements(self):
        """Run the k2-improvements script"""
        self.file_log("Running k2-improvements script (this may take 10-20 minutes)...")
        
        k2_script = "sh /mnt/UDISK/root/k2-improvements/gimme-the-jamin.sh"
        
        # This script takes a long time and doesn't disconnect SSH
        def _feature_console_echo(line: str):
            try:
                match = re.search(r"install_feature\s+([\w\-]+)", line)
                if match:
                    feature = match.group(1)
                    # Console-only message, keep file log unchanged
                    self.logger.info(f"Installing feature {feature}.", extra={"to_file": False})
            except Exception:
                pass

        # Use streaming with a line hook to surface feature installations to console only
        self._run_ssh_streaming(k2_script, timeout=1800, on_line=_feature_console_echo)
        
        self.file_log("k2-improvements script completed")
        
    def clone_and_install_repo(self):
        """Clone the printer repository and run install.sh"""
        # File-only detail; console already has the step header
        self.file_log(f"Cloning repository and switching to branch '{self.branch}'...")
        
        # Use home directory for the clone
        clone_dir = "~/Printer"
        
        # Remove existing clone if it exists
        self.run_remote_command(f"rm -rf {clone_dir}", wait_for_completion=True)
        
        # Clone the repository to home directory
        self.run_remote_command(
            f"cd ~ && git clone https://github.com/Jacob10383/Printer.git",
            wait_for_completion=True
        )
        
        # Switch to the specified branch
        self.run_remote_command(
            f"cd {clone_dir} && git checkout {self.branch} || git checkout main",
            wait_for_completion=True
        )
        
        # Run the install script
        self.file_log("Running install.sh...")
        summary_status: dict = {}

        def _installer_console_echo(line: str):
            try:
                match = re.search(r"\bRunning\s+([\w\-]+)\s+installer\b", line, flags=re.IGNORECASE)
                if match:
                    name = match.group(1)
                    self.logger.info(f"Running {name} installer.", extra={"to_file": False})
                # Detect installation summary result lines
                m2 = re.search(r"\b([a-zA-Z0-9_\-]+)\s*:\s*(?:[\u2713\u2714\u2717\u274C\u00D7\s]*)?(SUCCESS|FAILED)\b", line, flags=re.IGNORECASE)
                if m2:
                    comp = m2.group(1)
                    status = m2.group(2).upper()
                    summary_status[comp] = status
            except Exception:
                pass

        self._run_ssh_streaming(
            f"cd {clone_dir} && chmod +x install.sh && ./install.sh",
            timeout=None,
            on_line=_installer_console_echo,
        )
        # After script completes, if we captured any summary statuses, report concise result to console only
        if summary_status:
            if all(s == 'SUCCESS' for s in summary_status.values()):
                self.logger.info("All installations succeeded.", extra={"to_file": False})
            else:
                self.logger.info("One or more installations failed.", extra={"to_file": False})
        
        self.file_log("Repository installation completed")
        
    def install(self):
        """Run the complete installation process"""
        try:
            self.log(f"Starting printer installation for {self.printer_ip}")
            self.log(f"Branch: {self.branch}")
            self.log(f"Detailed log: {self.log_file}")
            self.log("")
            
            self.file_log("Starting full printer installation...")
            self.file_log(f"Target: {self.printer_ip}")
            self.file_log(f"Branch: {self.branch}")
            self.file_log(f"Log file: {self.log_file}")

            self.log_step(1, "Setting up SSH access")
            self.ensure_ssh_access()
            self.log("SSH access configured")
            

            self.log_step(2, "Uploading bootstrap files")
            self.upload_bootstrap()
            self.log("Bootstrap files uploaded")
            
            self.log_step(3, "Running bootstrap script")
            self.run_bootstrap_script()
            self.log("Bootstrap script completed")
            
            self.log_step(4, "Running k2-improvements script (10-20 min)")
            self.run_k2_improvements()
            self.log("K2 improvements completed")
            
            self.log_step(5, "Cloning and installing repository")
            self.clone_and_install_repo()
            self.log("Repository installation completed")

            # Optional Step 6: Restore Moonraker stats
            if self.preserve_stats:
                self.log_step(6, "Restoring Moonraker stats")
                try:
                    self.restore_moonraker_stats()
                    self.log("Moonraker stats restore completed")
                except Exception as e:
                    self.log(f"Moonraker stats restore failed: {e}", "ERROR")
            
            # Calculate total time
            total_time = time.time() - self.start_time
            minutes = int(total_time // 60)
            seconds = int(total_time % 60)
            
            self.log("")
            self.log(f"Successfully installed in {minutes}m {seconds}s")
            self.log(f"Complete log saved to: {self.log_file}")
            
            self.file_log("Full installation completed successfully!")
            self.file_log(f"Total installation time: {minutes}m {seconds}s")
            self.file_log(f"Complete log saved to: {self.log_file}")
            
        except Exception as e:
            total_time = time.time() - self.start_time
            minutes = int(total_time // 60)
            seconds = int(total_time % 60)
            
            self.log("")
            self.log(f"Installation failed after {minutes}m {seconds}s")
            self.log(f"Check log file for details: {self.log_file}")
            
            self.log(f"Installation failed: {e}", "ERROR")
            self.log(f"Installation time before failure: {minutes}m {seconds}s", "ERROR")
            self.log(f"Check log file for details: {self.log_file}", "ERROR")
            sys.exit(1)


def main():
    parser = argparse.ArgumentParser(
        description="Full 3D Printer Installer",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python3 fullinstaller.py 192.168.1.100
  python3 fullinstaller.py 192.168.1.100 jac
  python3 fullinstaller.py 192.168.1.100 main
        """
    )
    
    parser.add_argument(
        "printer_ip",
        help="IP address of the 3D printer"
    )
    
    parser.add_argument(
        "branch",
        nargs="?",
        default="main",
        help="Git branch to use (default: main)"
    )
    
    parser.add_argument(
        "--password",
        default="creality_2024",
        help="SSH password for the printer (default: creality_2024)"
    )
    parser.add_argument(
        "--reset",
        action="store_true",
        help="Factory reset the device before installation"
    )
    parser.add_argument(
        "--preserve-stats", "--backup",
        dest="preserve_stats",
        action="store_true",
        help="Backup and restore Moonraker stats across factory reset (requires --reset)"
    )
    
    args = parser.parse_args()
    
    # Check if sshpass is available
    try:
        subprocess.run(["which", "sshpass"], check=True, capture_output=True)
    except subprocess.CalledProcessError:
        print("ERROR: sshpass is required but not installed.")
        print("Install it with: brew install sshpass (macOS) or apt-get install sshpass (Ubuntu)")
        sys.exit(1)
    
    # Enforce flag rules
    if args.preserve_stats and not args.reset:
        print("ERROR: --preserve-stats/--backup can only be used together with --reset.")
        sys.exit(2)

    # Create and run installer
    installer = PrinterInstaller(
        printer_ip=args.printer_ip,
        branch=args.branch,
        password=args.password,
        reset=args.reset,
        preserve_stats=args.preserve_stats,
    )
    # Backup before reset if requested
    if args.reset and args.preserve_stats:
        installer.backup_moonraker_stats()
    if args.reset:
        installer.reset_device()
        # After reset, keys will change; re-ensure access
        installer.ensure_ssh_access()
    installer.install()


if __name__ == "__main__":
    main()