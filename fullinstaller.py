#!/usr/bin/env python3
"""

Usage:
    python3 fullinstaller.py <printer_ip> [branch_name] [options]

Basic Installation:
    python3 fullinstaller.py 192.168.1.100
    python3 fullinstaller.py 192.168.1.100 jac
    python3 fullinstaller.py 192.168.1.100 main

Advanced Options:
    --password PASSWORD      SSH password (default: creality_2024)
    --reset                  Factory reset device before installation
    --preserve-stats         Backup/restore Moonraker stats (requires --reset)
    --key-only               Only ensure SSH access and install public key

Backup & Restore Options:
    --backup-only            Only perform backup of Moonraker stats
    --restore-only DIR       Restore Moonraker stats from backup directory
    --restore-backup DIR     Use specific backup directory during installation

Examples:
    # Full installation with reset and stats preservation
    python3 fullinstaller.py 192.168.1.100 --reset --preserve-stats
    
    # Only backup Moonraker stats
    python3 fullinstaller.py 192.168.1.100 --backup-only
    
    # Only restore from specific backup
    python3 fullinstaller.py 192.168.1.100 --restore-only /path/to/backup
    
    # Install using existing backup for restore
    python3 fullinstaller.py 192.168.1.100 --reset --preserve-stats --restore-backup /path/to/backup
"""

from __future__ import annotations

import argparse
import contextlib
import logging
import os
import shlex
import socket
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, Iterable, Optional, Sequence


def _stream_reader(file_obj, chunk_size: int = 1024 * 512) -> Iterable[bytes]:
    while True:
        chunk = file_obj.read(chunk_size)
        if not chunk:
            break
        if isinstance(chunk, str):
            chunk = chunk.encode()
        yield chunk


import paramiko


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class InstallerError(Exception):
    """Base installer error that should stop the flow."""


class SSHConnectionError(InstallerError):
    """Raised when the SSH connection cannot be established or maintained."""


class CommandExecutionError(InstallerError):
    """Raised when a remote command fails to execute successfully."""


class FileTransferError(InstallerError):
    """Raised when a file transfer operation fails."""


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class InstallerConfig:
    username: str = "root"
    ssh_port: int = 22
    keepalive_interval: int = 10
    connect_timeout: int = 15
    command_check_interval: float = 0.2
    remote_path_export: str = (
        "export PATH=/opt/bin:/opt/sbin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin;"
    )
    remote_bootstrap_path: str = "/mnt/UDISK/printer_data/config/bootstrap"
    remote_bootstrap_archive_name: str = "bootstrap.tar.gz"
    remote_clone_dir: str = "~/Printer"
    remote_repo_url: str = "https://github.com/Jacob10383/Printer.git"
    k2_script_path: str = "/mnt/UDISK/root/k2-improvements/gimme-the-jamin.sh"
    moonraker_database_dir: str = "/mnt/UDISK/root/printer_data/database"
    moonraker_service: str = "moonraker"


@dataclass
class CommandResult:
    command: str
    stdout: str
    stderr: str
    exit_status: Optional[int]
    success_tokens_seen: bool
    elapsed: float

    @property
    def ok(self) -> bool:
        return self.exit_status == 0 or self.exit_status is None


# ---------------------------------------------------------------------------
# Remote execution helpers
# ---------------------------------------------------------------------------


class RemoteExecutor:
    def __init__(
        self,
        host: str,
        password: str,
        logger: logging.Logger,
        config: InstallerConfig,
    ) -> None:
        self._host = host
        self._password = password
        self._logger = logger
        self._config = config
        self._client: Optional[paramiko.SSHClient] = None
        self._logger_fields = {
            "hostname": self._host,
        }

    # -- Client lifecycle -------------------------------------------------

    def connect(self, *, force: bool = False) -> None:
        if not force and self._transport_is_active():
            return

        self.close()

        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

        try:
            client.connect(
                hostname=self._host,
                port=self._config.ssh_port,
                username=self._config.username,
                password=self._password,
                look_for_keys=False,
                allow_agent=False,
                timeout=self._config.connect_timeout,
            )
        except paramiko.AuthenticationException as exc:
            self.close()
            raise SSHConnectionError("Authentication to printer failed") from exc
        except (paramiko.SSHException, socket.error) as exc:
            self.close()
            raise SSHConnectionError("Unable to establish SSH connection") from exc

        transport = client.get_transport()
        if transport is None:
            client.close()
            raise SSHConnectionError("SSH transport unavailable after connection")

        transport.set_keepalive(self._config.keepalive_interval)
        self._client = client

    def close(self) -> None:
        if self._client is not None:
            try:
                self._client.close()
            finally:
                self._client = None

    def _transport_is_active(self) -> bool:
        if self._client is None:
            return False
        transport = self._client.get_transport()
        return transport.is_active() if transport else False

    @contextlib.contextmanager
    def sftp(self):
        self.connect()
        assert self._client is not None
        try:
            sftp_client = self._client.open_sftp()
        except (paramiko.SSHException, OSError) as exc:
            raise FileTransferError("Unable to open SFTP session") from exc

        try:
            yield sftp_client
        finally:
            with contextlib.suppress(Exception):
                sftp_client.close()

    # -- Command execution -----------------------------------------------

    def run(
        self,
        command: str,
        *,
        timeout: Optional[int] = None,
        expect_disconnect: bool = False,
        success_tokens: Optional[Iterable[str]] = None,
        on_line: Optional[Callable[[str], None]] = None,
        input_data: Optional[Iterable[bytes]] = None,
        request_pty: bool = False,
    ) -> CommandResult:
        self.connect()
        assert self._client is not None

        full_command = f"{self._config.remote_path_export} {command}".strip()
        self._logger.debug("[REMOTE] Executing: %s", full_command)

        try:
            transport = self._client.get_transport()
            if transport is None or not transport.is_active():
                raise SSHConnectionError("SSH transport became unavailable")
            channel = transport.open_session()
            if request_pty:
                channel.get_pty()
            channel.exec_command(full_command)
        except (paramiko.SSHException, OSError) as exc:
            self.close()
            raise SSHConnectionError("Failed to open SSH channel") from exc

        if input_data is not None:
            try:
                for chunk in input_data:
                    if not chunk:
                        continue
                    if isinstance(chunk, str):
                        chunk = chunk.encode()
                    channel.sendall(chunk)
            except Exception as exc:
                channel.close()
                self.close()
                raise CommandExecutionError(
                    "Failed while streaming input to remote command"
                ) from exc
            finally:
                with contextlib.suppress(Exception):
                    channel.shutdown_write()

        buffers = {"stdout": "", "stderr": ""}
        collected = {"stdout": [], "stderr": []}
        success_seen = False
        start_time = time.time()

        def _process_stream(kind: str, chunk: str) -> None:
            nonlocal success_seen
            buffers[kind] += chunk
            while "\n" in buffers[kind]:
                line, rest = buffers[kind].split("\n", 1)
                buffers[kind] = rest
                clean_line = line.rstrip("\r")
                collected[kind].append(clean_line)
                self._logger.debug("REMOTE %s: %s", kind.upper(), clean_line)
                if success_tokens and any(token in clean_line.lower() for token in success_tokens):
                    success_seen = True
                if on_line:
                    with contextlib.suppress(Exception):
                        on_line(clean_line)

        try:
            while True:
                if timeout is not None and (time.time() - start_time) > timeout:
                    channel.close()
                    raise CommandExecutionError(
                        f"Remote command timed out after {timeout} seconds: {command}"
                    )

                if channel.recv_ready():
                    data = channel.recv(4096).decode("utf-8", errors="replace")
                    _process_stream("stdout", data)

                if channel.recv_stderr_ready():
                    data = channel.recv_stderr(4096).decode("utf-8", errors="replace")
                    _process_stream("stderr", data)

                if channel.exit_status_ready() and not channel.recv_ready() and not channel.recv_stderr_ready():
                    break

                time.sleep(self._config.command_check_interval)

            if buffers["stdout"]:
                _process_stream("stdout", "\n")
            if buffers["stderr"]:
                _process_stream("stderr", "\n")

            try:
                exit_status = channel.recv_exit_status()
            except paramiko.SSHException:
                exit_status = None

        except (paramiko.SSHException, OSError) as exc:
            channel.close()
            self.close()
            if expect_disconnect:
                return CommandResult(
                    command=command,
                    stdout="\n".join(collected["stdout"]),
                    stderr="\n".join(collected["stderr"]),
                    exit_status=None,
                    success_tokens_seen=success_seen,
                    elapsed=time.time() - start_time,
                )
            raise CommandExecutionError("Remote command failed during execution") from exc

        elapsed = time.time() - start_time

        stdout_text = "\n".join(collected["stdout"])
        stderr_text = "\n".join(collected["stderr"])

        if expect_disconnect:
            if exit_status in (0, None) or success_seen:
                return CommandResult(
                    command=command,
                    stdout=stdout_text,
                    stderr=stderr_text,
                    exit_status=None,
                    success_tokens_seen=success_seen,
                    elapsed=elapsed,
                )
            message = self._format_failure_message(
                command=command,
                exit_status=exit_status,
                stdout=stdout_text,
                stderr=stderr_text,
            )
            self._logger.error(message)
            raise CommandExecutionError(message)

        if exit_status != 0:
            message = self._format_failure_message(
                command=command,
                exit_status=exit_status,
                stdout=stdout_text,
                stderr=stderr_text,
            )
            self._logger.error(message)
            raise CommandExecutionError(message)

        return CommandResult(
            command=command,
            stdout=stdout_text,
            stderr=stderr_text,
            exit_status=exit_status,
            success_tokens_seen=success_seen,
            elapsed=elapsed,
        )

    def _format_failure_message(
        self,
        *,
        command: str,
        exit_status: Optional[int],
        stdout: str,
        stderr: str,
    ) -> str:
        status_text = "unknown" if exit_status is None else str(exit_status)

        def _trim(text: str) -> str:
            text = text.strip()
            if len(text) > 400:
                return text[:400] + "... [truncated]"
            return text

        stdout_trimmed = _trim(stdout)
        stderr_trimmed = _trim(stderr)

        parts = [
            f"Remote command failed with exit status {status_text}: {command}",
        ]
        if stdout_trimmed:
            parts.append(f"STDOUT: {stdout_trimmed}")
        if stderr_trimmed:
            parts.append(f"STDERR: {stderr_trimmed}")
        return " | ".join(parts)


# ---------------------------------------------------------------------------
# Printer installer
# ---------------------------------------------------------------------------


class PrinterInstaller:
    def __init__(
        self,
        printer_ip: str,
        branch: str,
        password: Optional[str] = None,
        reset: bool = False,
        preserve_stats: bool = False,
    ) -> None:
        if password is None:
            raise ValueError("Password is required")

        self.printer_ip = printer_ip
        self.branch = branch
        self.password = password
        self.reset = reset
        self.preserve_stats = preserve_stats
        self.config = InstallerConfig()
        self.log_file = f"printer_install_{printer_ip}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
        self.start_time = time.time()
        self.bootstrap_path = Path(__file__).parent / "bootstrap"
        self.bootstrap_tar = Path(__file__).parent / "bootstrap.tar.gz"
        self.moonraker_backup_dir: Optional[Path] = None
        self.moonraker_backup_files: dict[str, Path] = {}

        self.logger = logging.getLogger("printer_installer")
        self.setup_logging()
        self.executor = RemoteExecutor(
            host=self.printer_ip,
            password=self.password,
            logger=self.logger,
            config=self.config,
        )

    # -- Logging ---------------------------------------------------------

    def setup_logging(self) -> None:
        self.logger.setLevel(logging.DEBUG)
        self.logger.handlers.clear()

        class _ConsoleFilter(logging.Filter):
            def filter(self, record: logging.LogRecord) -> bool:
                return getattr(record, "to_console", True)

        class _FileFilter(logging.Filter):
            def filter(self, record: logging.LogRecord) -> bool:
                return getattr(record, "to_file", True)

        class _ColorFormatter(logging.Formatter):
            def format(self, record: logging.LogRecord) -> str:
                msg = super().format(record)
                if getattr(record, "is_step", False):
                    return f"\033[96m{msg}\033[0m"
                return msg

        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setLevel(logging.INFO)
        console_handler.addFilter(_ConsoleFilter())
        console_handler.setFormatter(_ColorFormatter("%(message)s"))

        file_handler = logging.FileHandler(self.log_file)
        file_handler.setLevel(logging.DEBUG)
        file_handler.addFilter(_FileFilter())
        file_handler.setFormatter(
            logging.Formatter(
                "[%(asctime)s] [%(levelname)s] %(message)s",
                datefmt="%Y-%m-%d %H:%M:%S",
            )
        )

        self.logger.addHandler(console_handler)
        self.logger.addHandler(file_handler)
        self.logger.info(f"Logging to file: {self.log_file}")
        
    def log(self, message: str, level: str = "INFO") -> None:
        if level == "ERROR":
            self.logger.error(message)
        elif level == "WARNING":
            self.logger.warning(message)
        else:
            self.logger.info(message)
            
    def file_log(self, message: str, level: str = "INFO") -> None:
        extra = {"to_console": False}
        if level == "ERROR":
            self.logger.error(message, extra=extra)
        elif level == "WARNING":
            self.logger.warning(message, extra=extra)
        else:
            self.logger.info(message, extra=extra)

    def log_step(self, step_number: int, title: str) -> None:
        self.logger.info(
            f"=== STEP {step_number}: {title} ===",
            extra={"is_step": True},
        )

    # -- SSH helpers -----------------------------------------------------

    def ensure_ssh_access(self) -> None:
        self.file_log("Ensuring SSH access...")
        try:
            self.executor.connect()
            result = self.executor.run("echo test")
            if "test" not in result.stdout:
                raise SSHConnectionError("Printer did not respond with expected output")
            self.file_log("SSH access verified")
        except InstallerError:
            raise
        except Exception as exc:
            raise SSHConnectionError(f"Failed to verify SSH access: {exc}") from exc

    def install_public_key(self) -> bool:
        self.log("Installing local SSH public key on printer...")
        commands = [
            ["ssh-keygen", "-R", self.printer_ip],
            [
                "sshpass",
                "-p",
                self.password,
                "ssh-copy-id",
                "-o",
                "StrictHostKeyChecking=no",
                f"{self.config.username}@{self.printer_ip}",
            ],
        ]

        for cmd in commands:
            pretty_cmd = " ".join(shlex.quote(c) for c in cmd)
            self.file_log(f"Running local command: {pretty_cmd}")
            try:
                result = subprocess.run(
                    cmd,
                    check=True,
                    capture_output=True,
                    text=True,
                )
            except subprocess.CalledProcessError as exc:
                if exc.stdout:
                    self.file_log(f"stdout: {exc.stdout.strip()}", "ERROR")
                if exc.stderr:
                    self.file_log(f"stderr: {exc.stderr.strip()}", "ERROR")
                self.log(f"Command failed: {pretty_cmd}", "ERROR")
                return False

            if result.stdout:
                self.file_log(f"stdout: {result.stdout.strip()}")
            if result.stderr:
                self.file_log(f"stderr: {result.stderr.strip()}")

        self.file_log("Installed local public key on printer for passwordless SSH.")
        self.log("Public SSH key configured successfully.")
        return True

    # -- File transfer helpers ------------------------------------------
            
    def upload_bootstrap(self) -> None:
        if not self.bootstrap_tar.exists():
            raise InstallerError(f"Bootstrap tar file not found at {self.bootstrap_tar}")
            
        self.file_log(
            f"Uploading bootstrap archive to {self.config.remote_bootstrap_path}"
        )

        self.executor.run(f"mkdir -p {self.config.remote_bootstrap_path}")

        remote_archive = os.path.join(
            self.config.remote_bootstrap_path,
            self.config.remote_bootstrap_archive_name,
        )

        with self.bootstrap_tar.open("rb") as local_file:
            self.executor.run(
                f"cat > {remote_archive}",
                input_data=_stream_reader(local_file),
                request_pty=False,
            )

        extract_cmd = (
            f"cd {self.config.remote_bootstrap_path} && "
            f"tar -xzf {self.config.remote_bootstrap_archive_name}"
        )
        self.executor.run(extract_cmd, request_pty=False)
        self.executor.run(
            f"rm -f {remote_archive}",
            request_pty=False,
        )
        
        self.file_log("Bootstrap files uploaded successfully")

    # -- Moonraker data --------------------------------------------------

    def backup_moonraker_stats(self, *, force: bool = False) -> None:
        if not self.preserve_stats and not force:
            return

        msg = (
            "Backing up Moonraker stats before reset..."
            if self.preserve_stats and not force
            else "Backing up Moonraker stats..."
        )
        self.log(msg)
        self.ensure_ssh_access()
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.moonraker_backup_dir = Path.cwd() / f"moonraker_backup_{self.printer_ip}_{timestamp}"
        self.moonraker_backup_dir.mkdir(parents=True, exist_ok=True)

        targets = [
            f"{self.config.moonraker_database_dir}/data.mdb",
            f"{self.config.moonraker_database_dir}/moonraker-sql.db",
        ]

        succeeded = 0
        failed: list[str] = []

        with self.executor.sftp() as sftp:
            for remote_file in targets:
                local_path = self.moonraker_backup_dir / Path(remote_file).name
                try:
                    sftp.get(remote_file, str(local_path))
                    self.moonraker_backup_files[Path(remote_file).name] = local_path
                    succeeded += 1
                except IOError as exc:
                    failed.append(remote_file)
                    self.file_log(f"Failed to backup {remote_file}: {exc}", "ERROR")

        if failed:
            raise FileTransferError(
                f"Moonraker stats backup failed for: {', '.join(failed)}"
            )

        self.file_log(
            f"Successfully backed up {succeeded} Moonraker file(s) to {self.moonraker_backup_dir}"
        )
        self.log(f"Backed up {succeeded} Moonraker stats file(s)")

    def restore_moonraker_stats(self, *, force: bool = False) -> None:
        if not self.preserve_stats and not force:
            return
        if not self.moonraker_backup_files:
            self.file_log("No Moonraker backup files found; skipping restore.")
            return

        self.file_log("Restoring Moonraker stats to device...")
        self.ensure_ssh_access()
        
        self.executor.run(
            f"/etc/init.d/{self.config.moonraker_service} stop",
            timeout=30,
        )

        self.executor.run(f"mkdir -p {self.config.moonraker_database_dir}")

        restored = 0
        with self.executor.sftp() as sftp:
            for name, local_path in self.moonraker_backup_files.items():
                if not local_path.exists():
                    self.file_log(
                        f"Missing local backup file; skipping: {local_path}",
                        "WARNING",
                    )
                    continue
                remote_path = f"{self.config.moonraker_database_dir}/{name}"
                try:
                    sftp.put(str(local_path), remote_path)
                    restored += 1
                except IOError as exc:
                    self.file_log(f"Failed to restore {name}: {exc}", "ERROR")

        self.executor.run(
            f"/etc/init.d/{self.config.moonraker_service} start",
            timeout=30,
        )
        
        if restored == 0:
            self.log("No Moonraker stats files were restored.", "WARNING")
        else:
            self.log(f"Restored {restored} Moonraker stats file(s)")
        
    # -- Installation steps ---------------------------------------------

    def run_bootstrap_script(self) -> None:
        self.file_log("Running bootstrap script...")
        command = f"sh {self.config.remote_bootstrap_path}/bootstrap.sh"
        self.executor.run(
            command,
            expect_disconnect=True,
            success_tokens=(
                "ok",
                "logging you out now",
                "please reconnect",
                "you need to log back in",
            ),
        )
        self.file_log("Bootstrap script completed successfully")

    def run_k2_improvements(self) -> None:
        self.file_log(
            "Running k2-improvements script (this may take 10-20 minutes)..."
        )

        def feature_echo(line: str) -> None:
            match = line.lower().strip()
            if "install_feature" in match:
                self.logger.info(line, extra={"to_file": False})

        self.executor.run(
            f"sh {self.config.k2_script_path}",
            timeout=1800,
            on_line=feature_echo,
        )
        self.file_log("k2-improvements script completed")
        
    def clone_and_install_repo(self) -> None:
        self.file_log(
            f"Cloning repository and switching to branch '{self.branch}'..."
        )

        self.executor.run(f"rm -rf {self.config.remote_clone_dir}")

        self.executor.run(
            f"cd ~ && git clone {self.config.remote_repo_url}",
            timeout=300,
        )

        checkout_cmd = (
            f"cd {self.config.remote_clone_dir} && "
            f"git checkout {self.branch} || git checkout main"
        )
        self.executor.run(checkout_cmd, timeout=120)

        summary_status: dict[str, str] = {}

        def installer_echo(line: str) -> None:
            lower = line.lower()
            if "running" in lower and "installer" in lower:
                self.logger.info(line, extra={"to_file": False})
            if ":" in line:
                parts = line.split(":", 1)
                name, status = parts[0].strip(), parts[1].strip().upper()
                if status in {"SUCCESS", "FAILED"}:
                    summary_status[name] = status

        self.executor.run(
            f"cd {self.config.remote_clone_dir} && chmod +x install.sh && ./install.sh",
            on_line=installer_echo,
        )

        if summary_status:
            if all(status == "SUCCESS" for status in summary_status.values()):
                self.logger.info(
                    "All installations succeeded.",
                    extra={"to_file": False},
                )
            else:
                self.logger.info(
                    "One or more installations failed.",
                    extra={"to_file": False},
                )
        
        self.file_log("Repository installation completed")
        
    def run_remote_command(
        self,
        command: str,
        *,
        wait_for_completion: bool = True,
        timeout: Optional[int] = None,
    ) -> str:
        if wait_for_completion:
            result = self.executor.run(command, timeout=timeout)
            return result.stdout
        self.executor.run(
            f"nohup {command} > /dev/null 2>&1 &",
            timeout=timeout,
        )
        return ""

    def reset_device(self) -> None:
        self.log("Reset requested before installation. Initiating device wipe...")
        self.ensure_ssh_access()

        self.executor.run(
            'echo "all" | /usr/bin/nc -U /var/run/wipe.sock',
            timeout=120,
            expect_disconnect=True,
            success_tokens=("ok",),
        )

        self.log("Reset acknowledged; waiting 30 seconds for device to reboot")
        time.sleep(30)

        start = time.time()
        self.executor.close()

        while True:
            time.sleep(5)
            if not self._is_port_open(self.printer_ip, self.config.ssh_port):
                continue
            try:
                self.executor.connect(force=True)
                result = self.executor.run("echo online", timeout=5)
            except InstallerError:
                self.executor.close()
                continue
            if "online" in result.stdout:
                break

        elapsed = int(time.time() - start)
        self.log(f"Device back online after {elapsed}s")

    @staticmethod
    def _is_port_open(host: str, port: int) -> bool:
        try:
            with socket.create_connection((host, port), timeout=2):
                return True
        except OSError:
            return False

    # -- Orchestration ---------------------------------------------------

    def install(self) -> None:
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

            self.install_public_key()

            if self.preserve_stats:
                self.log_step(6, "Restoring Moonraker stats")
                try:
                    self.restore_moonraker_stats()
                    self.log("Moonraker stats restore completed")
                except InstallerError as exc:
                    self.log(f"Moonraker stats restore failed: {exc}", "ERROR")
            
            total_time = time.time() - self.start_time
            minutes = int(total_time // 60)
            seconds = int(total_time % 60)
            
            self.log("")
            self.log(f"Successfully installed in {minutes}m {seconds}s")
            self.log(f"Complete log saved to: {self.log_file}")
            
            self.file_log("Full installation completed successfully!")
            self.file_log(f"Total installation time: {minutes}m {seconds}s")
            self.file_log(f"Complete log saved to: {self.log_file}")
            
        except InstallerError as exc:
            self._handle_failure(exc)
        finally:
            self.executor.close()

    def _handle_failure(self, error: InstallerError) -> None:
        total_time = time.time() - self.start_time
        minutes = int(total_time // 60)
        seconds = int(total_time % 60)

        self.log("")
        self.log(f"Installation failed after {minutes}m {seconds}s")
        self.log(f"Check log file for details: {self.log_file}")

        self.log(f"Installation failed: {error}", "ERROR")
        self.log(
            f"Installation time before failure: {minutes}m {seconds}s",
            "ERROR",
        )
        self.log(f"Check log file for details: {self.log_file}", "ERROR")
        sys.exit(1)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Full 3D Printer Installer",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python3 fullinstaller.py 192.168.1.100
  python3 fullinstaller.py 192.168.1.100 jac
  python3 fullinstaller.py 192.168.1.100 main
        """,
    )

    parser.add_argument("printer_ip", help="IP address of the 3D printer")
    parser.add_argument(
        "branch",
        nargs="?",
        default="main",
        help="Git branch to use (default: main)",
    )
    parser.add_argument(
        "--password",
        default="creality_2024",
        help="SSH password for the printer (default: creality_2024)",
    )
    parser.add_argument(
        "--reset",
        action="store_true",
        help="Factory reset the device before installation",
    )
    parser.add_argument(
        "--preserve-stats",
        "--backup",
        dest="preserve_stats",
        action="store_true",
        help="Backup and restore Moonraker stats across factory reset (requires --reset)",
    )
    parser.add_argument(
        "--key-only",
        dest="key_only",
        action="store_true",
        help="Only ensure SSH access and install local public key on the printer",
    )
    parser.add_argument(
        "--backup-only",
        action="store_true",
        help="Only perform backup of Moonraker stats without installation"
    )
    parser.add_argument(
        "--restore-only",
        metavar="BACKUP_DIR",
        help="Restore Moonraker stats from specified backup directory without installation"
    )
    parser.add_argument(
        "--restore-backup",
        metavar="BACKUP_DIR",
        help="Specify backup directory to use for restore during installation"
    )
    
    args = parser.parse_args()
    
    # Validate modes: --key-only, --backup-only, --restore-only are mutually exclusive
    modes_selected = sum(1 for x in (args.key_only, args.backup_only, bool(args.restore_only)) if x)
    if modes_selected > 1:
        print("ERROR: Choose only one mode: --key-only, --backup-only, or --restore-only.")
        sys.exit(2)

    # Forbid mixing modes with install modifiers
    if args.key_only or args.backup_only or args.restore_only:
        if args.reset or args.preserve_stats or args.restore_backup:
            print("ERROR: --key-only, --backup-only, and --restore-only cannot be combined with --reset, --preserve-stats, or --restore-backup.")
            sys.exit(2)

    # --preserve-stats requires --reset
    if args.preserve_stats and not args.reset:
        print("ERROR: --preserve-stats/--backup can only be used together with --reset.")
        sys.exit(2)

    # --preserve-stats conflicts with --restore-backup
    if args.preserve_stats and args.restore_backup:
        print("ERROR: --preserve-stats cannot be used together with --restore-backup.")
        sys.exit(2)

    # Validate provided backup directories exist and are complete
    def _validate_backup_dir(path_str: str) -> None:
        backup_dir = Path(path_str)
        if not backup_dir.exists() or not backup_dir.is_dir():
            print(f"ERROR: Backup directory does not exist: {backup_dir}")
            sys.exit(2)
        required_files = ["data.mdb", "moonraker-sql.db"]
        missing = [name for name in required_files if not (backup_dir / name).exists()]
        if missing:
            print(f"ERROR: Backup directory is missing required files: {', '.join(missing)} in {backup_dir}")
            sys.exit(2)

    if args.restore_only:
        _validate_backup_dir(args.restore_only)

    if args.restore_backup:
        _validate_backup_dir(args.restore_backup)

    installer = PrinterInstaller(
        printer_ip=args.printer_ip,
        branch=args.branch,
        password=args.password,
        reset=args.reset,
        preserve_stats=args.preserve_stats,
    )

    if args.key_only:
        installer.ensure_ssh_access()
        if installer.install_public_key():
            sys.exit(0)
        else:
            installer.log("Failed to configure public SSH key.", "ERROR")
            sys.exit(1)

    if args.backup_only:
        installer.ensure_ssh_access()
        installer.backup_moonraker_stats(force=True)
        print(f"Backup completed successfully. Saved to: {installer.moonraker_backup_dir}")
        sys.exit(0)

    if args.restore_only:
        installer.ensure_ssh_access()
        installer.moonraker_backup_dir = Path(args.restore_only)
        installer.moonraker_backup_files = {
            "data.mdb": installer.moonraker_backup_dir / "data.mdb",
            "moonraker-sql.db": installer.moonraker_backup_dir / "moonraker-sql.db"
        }
        installer.restore_moonraker_stats(force=True)
        print("Restore completed successfully.")
        sys.exit(0)

    if args.reset and args.preserve_stats:
        if args.restore_backup:
            # Use manually specified backup
            installer.moonraker_backup_dir = Path(args.restore_backup)
            installer.moonraker_backup_files = {
                "data.mdb": installer.moonraker_backup_dir / "data.mdb",
                "moonraker-sql.db": installer.moonraker_backup_dir / "moonraker-sql.db"
            }
        else:
            # Create new backup as usual
            installer.backup_moonraker_stats()
    if args.reset:
        installer.reset_device()
        installer.ensure_ssh_access()

    installer.install()

    # If a specific backup was requested for restore during install, perform restore now
    if args.restore_backup:
        installer.moonraker_backup_dir = Path(args.restore_backup)
        installer.moonraker_backup_files = {
            "data.mdb": installer.moonraker_backup_dir / "data.mdb",
            "moonraker-sql.db": installer.moonraker_backup_dir / "moonraker-sql.db"
        }
        installer.restore_moonraker_stats(force=True)


if __name__ == "__main__":
    main()

