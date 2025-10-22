#!/usr/bin/env python3
"""
Reference summary for the GTK/Flet GUI and supporting automation.

Positional arguments:
  printer_ip             Target printer IPv4 address (required)
  branch                 Git branch to deploy (defaults to 'main' when omitted)

Core options:
  --password PASSWORD    SSH password (default: creality_2024)
  --reset                Factory reset device before installation
  --reset-only           Factory reset device without running installers
  --key-only             Install the local public SSH key and exit

Preservation flags (only valid with --reset or --reset-only):
  --preserve-stats | --backup   Moonraker database
  --preserve-timelapses         Timelapse media
  --preserve-gcodes             Uploaded gcode files

Backup mode (mutually exclusive with install/reset flows):
  --backup-only [DIR]           Write selected components to DIR (or CWD)
  --backup-moonraker            Include Moonraker stats
  --backup-timelapses           Include timelapse files
  --backup-gcodes               Include gcode files

Restore mode (mutually exclusive with install/reset flows):
  --restore-only DIR            Restore selected components from DIR
  --restore-moonraker           Include Moonraker stats
  --restore-timelapses          Include timelapse files
  --restore-gcodes              Include gcode files

Targeted installer steps (defaults to all when omitted):
  --run-bootstrap               Upload and run bootstrap scripts
  --run-k2                      Execute k2-improvements script
  --run-repo                    Clone and run install.sh from the repo

Notes:
  * --preserve-* flags require a reset-driven workflow and manage capture/restore automatically.
  * Standalone backup/restore modes cannot be combined with reset or preserve options.
  * The GUI orchestrates these combinations; this reference exists so the CLI entry point stays aligned.
"""

from __future__ import annotations

import argparse
import contextlib
import logging
import os
import shlex
import shutil
import socket
import stat
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, Iterable, Optional, Sequence
from urllib import error, request


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

BOOTSTRAP_DOWNLOAD_URLS: tuple[str, ...] = (
    "https://github.com/Jacob10383/k2-improvements/releases/latest/download/bootstrap.tar.gz",
    "https://github.com/Jacob10383/k2-improvements/releases/download/strap/bootstrap.tar.gz",
)


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
    timelapse_directory: str = "/mnt/UDISK/root/timelapse"
    gcodes_directory: str = "/mnt/UDISK/root/printer_data/gcodes"


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
                if success_tokens and any(
                    token in clean_line.lower() for token in success_tokens
                ):
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

                if (
                    channel.exit_status_ready()
                    and not channel.recv_ready()
                    and not channel.recv_stderr_ready()
                ):
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
            raise CommandExecutionError(
                "Remote command failed during execution"
            ) from exc

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
        preserve_timelapses: bool = False,
        preserve_gcodes: bool = False,
    ) -> None:
        if password is None:
            raise ValueError("Password is required")

        self.printer_ip = printer_ip
        self.branch = branch
        self.password = password
        self.reset = reset
        self.preserve_stats = preserve_stats
        self.preserve_timelapses = preserve_timelapses
        self.preserve_gcodes = preserve_gcodes
        self.config = InstallerConfig()

        self.start_time = time.time()

        # Use __file__.parent for bootstrap path (works in both compiled and direct Python)
        base_path = Path(__file__).parent
        self.bootstrap_path = base_path / "bootstrap"
        self.bootstrap_tar = base_path / "bootstrap.tar.gz"
        self.moonraker_backup_dir: Optional[Path] = None
        self.moonraker_backup_files: dict[str, Path] = {}
        self.timelapse_backup_dir: Optional[Path] = None
        self.timelapse_backup_files: list[Path] = []
        self.gcodes_backup_dir: Optional[Path] = None
        self.gcodes_backup_files: list[Path] = []

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

        class _ColorFormatter(logging.Formatter):
            def __init__(self) -> None:
                super().__init__("%(asctime)s %(message)s", "%H:%M:%S")

            def format(self, record: logging.LogRecord) -> str:
                msg = super().format(record)
                # Prefix DEBUG messages with [VERBOSE] so GUI can filter them
                if record.levelno == logging.DEBUG:
                    msg = f"[VERBOSE] {msg}"
                # Only apply color to console messages (to_console=True)
                elif getattr(record, "to_console", True) and getattr(
                    record, "is_step", False
                ):
                    return f"\033[96m{msg}\033[0m"
                return msg

        # Use UTF-8 encoding with error handling to prevent Windows cp1252 crashes
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setLevel(
            logging.DEBUG
        )  # Send all messages to GUI for verbose buffer
        console_handler.setFormatter(_ColorFormatter())
        # Force UTF-8 encoding on the stream with error handling
        if hasattr(console_handler.stream, "reconfigure"):
            console_handler.stream.reconfigure(encoding="utf-8", errors="ignore")

        self.logger.addHandler(console_handler)

    def log(self, message: str, level: str = "INFO") -> None:
        if level == "ERROR":
            self.logger.error(message)
        elif level == "WARNING":
            self.logger.warning(message)
        else:
            self.logger.info(message)

    def file_log(self, message: str, level: str = "INFO") -> None:
        # Prefix with marker so GUI can identify verbose-only messages
        prefixed_message = f"[VERBOSE] {message}"
        if level == "ERROR":
            self.logger.error(prefixed_message)
        elif level == "WARNING":
            self.logger.warning(prefixed_message)
        else:
            self.logger.info(prefixed_message)

    def log_step(self, step_number: int, title: str) -> None:
        self.logger.info(
            f"=== STEP {step_number}: {title} ===",
            extra={"is_step": True},
        )

    def _format_size(self, num_bytes: int) -> str:
        if num_bytes <= 0:
            return "0 B"
        units = ("B", "KB", "MB", "GB", "TB")
        value = float(num_bytes)
        idx = 0
        while value >= 1024 and idx < len(units) - 1:
            value /= 1024
            idx += 1
        if idx == 0:
            return f"{int(value)} {units[idx]}"
        if value >= 100:
            return f"{value:,.0f} {units[idx]}"
        return f"{value:.1f} {units[idx]}"

    @staticmethod
    def _format_file_count(count: int) -> str:
        return f"{count} file{'s' if count != 1 else ''}"

    class _TransferMonitor:
        UPDATE_INTERVAL = 10.0

        def __init__(
            self,
            owner: "PrinterInstaller",
            *,
            verb: str,
            component: str,
            total_files: int,
            total_bytes: int,
        ) -> None:
            self._owner = owner
            self._verb = verb
            self._component = component
            self._total_files = total_files
            self._total_bytes = max(total_bytes, 0)
            self._completed_files = 0
            self._completed_bytes = 0
            self._current_file_size = 0
            self._last_emit = time.monotonic()
            self._last_percent = -1
            self._last_overall = 0
            self._active = self._total_bytes > 0
            self._emitted_complete = False

        def start_file(self, filename: str, file_size: int) -> None:
            if not self._active:
                return
            self._current_file_size = max(file_size, 0)
            self._current_transferred = 0

        def callback(self, transferred: int, total: int) -> None:
            if not self._active:
                return
            self._current_transferred = max(transferred, 0)
            overall = self._completed_bytes + self._current_transferred
            if self._total_bytes <= 0:
                return
            percent = int((min(overall, self._total_bytes) / self._total_bytes) * 100)
            now = time.monotonic()
            should_emit = False
            if percent >= 100:
                self._emitted_complete = True
                self._last_percent = percent
                self._last_overall = overall
                self._last_emit = now
                return
            if (
                (now - self._last_emit) >= self.UPDATE_INTERVAL
                and percent > self._last_percent
                and overall > self._last_overall
            ):
                should_emit = True
            if should_emit:
                transferred_str = self._owner._format_size(overall)
                total_str = self._owner._format_size(self._total_bytes)
                self._owner.log(
                    f"{self._verb} {self._component} – {percent}% ({transferred_str} / {total_str})"
                )
                self._last_emit = now
                self._last_percent = percent
                self._last_overall = overall

        def finish_file(self) -> None:
            if not self._active:
                return
            self._completed_files += 1
            self._completed_bytes += self._current_file_size
            self._current_file_size = 0
            self._current_transferred = 0
            if self._completed_bytes >= self._total_bytes:
                self.complete()

        def abort_current_file(self) -> None:
            if not self._active:
                return
            self._total_bytes = max(self._total_bytes - self._current_file_size, 0)
            self._total_files = max(self._total_files - 1, 0)
            self._current_file_size = 0
            self._current_transferred = 0
            self._last_overall = min(self._last_overall, self._completed_bytes)
            if self._total_bytes == 0:
                self._active = False

        def complete(self) -> None:
            if not self._active or self._emitted_complete:
                return
            self._last_overall = self._total_bytes
            self._last_percent = 100
            self._emitted_complete = True

    def _create_transfer_monitor(
        self,
        *,
        verb: str,
        component: str,
        total_files: int,
        total_bytes: int,
    ) -> Optional["_TransferMonitor"]:
        if total_files <= 0 or total_bytes <= 0:
            return None
        return self._TransferMonitor(
            self,
            verb=verb,
            component=component,
            total_files=total_files,
            total_bytes=total_bytes,
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
        ssh_dir = Path.home() / ".ssh"
        # Prefer modern key types first, fall back to older algorithms
        possible_keys = (
            "id_ed25519.pub",
            "id_ecdsa.pub",
            "id_rsa.pub",
            "id_dsa.pub",
        )
        pubkey_path = next(
            (
                ssh_dir / key_name
                for key_name in possible_keys
                if (ssh_dir / key_name).exists()
            ),
            None,
        )
        if pubkey_path is None:
            raise InstallerError(
                "No supported public key found in ~/.ssh. Run ssh-keygen first."
            )

        pubkey = pubkey_path.read_text().strip()
        self.log(f"Using public key {pubkey_path}")

        self.log(f"Configuring passwordless SSH on {self.printer_ip}...")
        self.ensure_ssh_access()

        # Remove old host key from known_hosts (host key changes after resets)
        try:
            subprocess.run(
                ["ssh-keygen", "-R", self.printer_ip],
                check=False,
                capture_output=True,
                text=True,
            )
        except FileNotFoundError:
            pass  # ssh-keygen isn't guaranteed on Windows; ignore if missing

        # Install key into Dropbear authorized_keys
        self.executor.run("mkdir -p /etc/dropbear && chmod 755 /etc/dropbear")
        escaped_key = pubkey.replace('"', r"\"")
        self.executor.run(
            f'grep -qxF "{escaped_key}" /etc/dropbear/authorized_keys '
            f'|| echo "{escaped_key}" >> /etc/dropbear/authorized_keys'
        )
        self.executor.run("chmod 600 /etc/dropbear/authorized_keys")

        self.log("SSH key installed. Future connections will not require a password.")
        return True

    # -- File transfer helpers ------------------------------------------

    def ensure_bootstrap_archive(self) -> None:
        self.file_log(f"Checking for bootstrap archive at: {self.bootstrap_tar}")
        if self.bootstrap_tar.exists():
            self.file_log(f"Bootstrap archive found at: {self.bootstrap_tar}")
            return

        self.log(
            "Local bootstrap archive missing; attempting to download latest release..."
        )
        self.file_log(f"Bootstrap archive not found at: {self.bootstrap_tar}")
        self._download_bootstrap_archive()

    def _download_bootstrap_archive(self) -> None:
        last_error: Optional[Exception] = None
        self.bootstrap_tar.parent.mkdir(parents=True, exist_ok=True)

        for url in BOOTSTRAP_DOWNLOAD_URLS:
            tmp_path: Optional[Path] = None
            try:
                self.file_log(f"Downloading bootstrap archive from {url}")
                with request.urlopen(url, timeout=60) as response:
                    status = getattr(response, "status", None)
                    if status is None:
                        status = response.getcode()
                    if status != 200:
                        raise InstallerError(
                            f"Unexpected HTTP status {status} while downloading from {url}"
                        )
                    with tempfile.NamedTemporaryFile("wb", delete=False) as tmp_file:
                        shutil.copyfileobj(response, tmp_file)
                        tmp_path = Path(tmp_file.name)
                if tmp_path is None:
                    raise InstallerError(
                        "Failed to write bootstrap archive to temporary file"
                    )
                tmp_path.replace(self.bootstrap_tar)
                self.log("Bootstrap archive downloaded successfully.")
                return
            except (InstallerError, error.URLError, error.HTTPError, OSError) as exc:
                last_error = exc
                self.file_log(
                    f"Failed to download bootstrap archive from {url}: {exc}", "WARNING"
                )
                if tmp_path and tmp_path.exists():
                    tmp_path.unlink(missing_ok=True)

        raise InstallerError(
            "Bootstrap archive not found locally and download failed from all sources"
        ) from last_error

    def upload_bootstrap(self) -> None:
        self.ensure_bootstrap_archive()

        # Double-check the file exists before attempting to open it
        if not self.bootstrap_tar.exists():
            raise FileTransferError(
                f"Bootstrap archive not found at {self.bootstrap_tar} after ensure_bootstrap_archive()"
            )

        self.file_log(
            f"Uploading bootstrap archive to {self.config.remote_bootstrap_path}"
        )

        self.executor.run(f"mkdir -p {self.config.remote_bootstrap_path}")

        # Use forward slash for remote Linux path (not os.path.join which uses local OS separator)
        remote_archive = f"{self.config.remote_bootstrap_path}/{self.config.remote_bootstrap_archive_name}"

        try:
            with self.bootstrap_tar.open("rb") as local_file:
                self.executor.run(
                    f"cat > {remote_archive}",
                    input_data=_stream_reader(local_file),
                    request_pty=False,
                )
        except (OSError, IOError) as exc:
            raise FileTransferError(
                f"Failed to read bootstrap archive from {self.bootstrap_tar}: {exc}"
            ) from exc

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

    def query_backup_sizes(self) -> dict[str, dict[str, int | bool]]:
        """Query backup sizes from printer without logging. Returns dict with moonraker, timelapses, and gcodes info.
        Raises SSHConnectionError if connection fails."""
        result = {
            "moonraker": {"count": 0, "size_kb": 0, "exists": False},
            "timelapses": {"count": 0, "size_kb": 0, "exists": False},
            "gcodes": {"count": 0, "size_kb": 0, "exists": False},
        }

        try:
            self.executor.connect()
        except SSHConnectionError:
            # Re-raise connection errors so caller knows connection failed
            raise
        except Exception:
            # Other connection errors should also be raised
            raise SSHConnectionError("Failed to establish SSH connection")

        # Query Moonraker database files
        try:
            # Only count the files that are actually backed up (exclude lock.mdb)
            count_result = self.executor.run(
                f"ls -1 {self.config.moonraker_database_dir}/data.mdb "
                f"{self.config.moonraker_database_dir}/moonraker-sql.db 2>/dev/null | wc -l",
                timeout=5,
            )
            if count_result.ok and count_result.stdout.strip().isdigit():
                result["moonraker"]["count"] = int(count_result.stdout.strip())
                result["moonraker"]["exists"] = result["moonraker"]["count"] > 0

            if result["moonraker"]["exists"]:
                # Only calculate size for files that are actually backed up (exclude lock.mdb)
                size_result = self.executor.run(
                    f"du -sk {self.config.moonraker_database_dir}/data.mdb "
                    f"{self.config.moonraker_database_dir}/moonraker-sql.db 2>/dev/null | "
                    "awk '{sum+=$1} END {print sum}'",
                    timeout=5,
                )
                if size_result.ok and size_result.stdout.strip().isdigit():
                    result["moonraker"]["size_kb"] = int(size_result.stdout.strip())
        except Exception:
            pass  # Silent failure for individual queries

        # Query timelapse files
        try:
            # Check if directory exists and get count
            dir_check = self.executor.run(
                f"test -d {self.config.timelapse_directory} && "
                f"ls -1 {self.config.timelapse_directory} 2>/dev/null | wc -l || echo 0",
                timeout=5,
            )
            if dir_check.ok and dir_check.stdout.strip().isdigit():
                count = int(dir_check.stdout.strip())
                result["timelapses"]["count"] = count
                result["timelapses"]["exists"] = count > 0

            if result["timelapses"]["exists"]:
                size_result = self.executor.run(
                    f"du -sk {self.config.timelapse_directory} 2>/dev/null | awk '{{print $1}}'",
                    timeout=5,
                )
                if size_result.ok and size_result.stdout.strip().isdigit():
                    result["timelapses"]["size_kb"] = int(size_result.stdout.strip())
        except Exception:
            pass  # Silent failure for individual queries

        # Query gcode files
        try:
            # Check if directory exists and get count
            dir_check = self.executor.run(
                f"test -d {self.config.gcodes_directory} && "
                f"ls -1 {self.config.gcodes_directory} 2>/dev/null | wc -l || echo 0",
                timeout=5,
            )
            if dir_check.ok and dir_check.stdout.strip().isdigit():
                count = int(dir_check.stdout.strip())
                result["gcodes"]["count"] = count
                result["gcodes"]["exists"] = count > 0

            if result["gcodes"]["exists"]:
                size_result = self.executor.run(
                    f"du -sk {self.config.gcodes_directory} 2>/dev/null | awk '{{print $1}}'",
                    timeout=5,
                )
                if size_result.ok and size_result.stdout.strip().isdigit():
                    result["gcodes"]["size_kb"] = int(size_result.stdout.strip())
        except Exception:
            pass  # Silent failure for individual queries

        return result

    def backup_moonraker_stats(
        self,
        *,
        force: bool = False,
        backup_dir: Optional[Path] = None,
        use_temp: bool = False,
    ) -> None:
        if not self.preserve_stats and not force:
            return

        start_message = (
            "Backing up Moonraker stats before reset"
            if self.preserve_stats and not force
            else "Backing up Moonraker stats"
        )
        self.ensure_ssh_access()
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

        # Determine backup directory: explicit path > temp > current working directory
        if backup_dir:
            base_dir = backup_dir
        elif use_temp:
            base_dir = Path(tempfile.gettempdir())
        else:
            base_dir = Path.cwd()

        self.moonraker_backup_dir = (
            base_dir / f"printer_backup_{self.printer_ip}_{timestamp}"
        )
        self.moonraker_backup_dir.mkdir(parents=True, exist_ok=True)

        targets = [
            f"{self.config.moonraker_database_dir}/data.mdb",
            f"{self.config.moonraker_database_dir}/moonraker-sql.db",
        ]

        succeeded = 0
        failed: list[str] = []

        with self.executor.sftp() as sftp:
            file_infos: list[tuple[str, Path, int]] = []
            total_bytes = 0
            for remote_file in targets:
                local_name = Path(remote_file).name
                size = 0
                try:
                    attrs = sftp.stat(remote_file)
                    if not stat.S_ISDIR(getattr(attrs, "st_mode", 0)):
                        size = getattr(attrs, "st_size", 0) or 0
                except IOError:
                    size = 0
                file_infos.append((remote_file, local_name, size))
                total_bytes += size

            self.log(
                f"{start_message} ({self._format_file_count(len(file_infos))}, "
                f"{self._format_size(total_bytes)})..."
            )

            for remote_file, local_name, _ in file_infos:
                local_path = self.moonraker_backup_dir / local_name
                try:
                    sftp.get(remote_file, str(local_path))
                    self.moonraker_backup_files[local_name] = local_path
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

        file_entries: list[tuple[str, Path]] = []
        total_bytes = 0
        for name, local_path in self.moonraker_backup_files.items():
            if not local_path.exists():
                self.file_log(
                    f"Missing local backup file; skipping: {local_path}",
                    "WARNING",
                )
                continue
            total_bytes += local_path.stat().st_size
            file_entries.append((name, local_path))

        if not file_entries:
            self.log("No Moonraker stats files were restored.", "WARNING")
            return

        self.log(
            f"Restoring Moonraker stats ({self._format_file_count(len(file_entries))}, "
            f"{self._format_size(total_bytes)})..."
        )

        restored = 0
        with self.executor.sftp() as sftp:
            for name, local_path in file_entries:
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

    # -- Timelapse files -------------------------------------------------

    def backup_timelapse_files(
        self,
        *,
        force: bool = False,
        backup_dir: Optional[Path] = None,
        use_temp: bool = False,
    ) -> None:
        """Backup timelapse files from the printer."""
        if not self.preserve_timelapses and not force:
            return

        self.ensure_ssh_access()

        # Check if timelapse directory exists
        try:
            result = self.executor.run(
                f"test -d {self.config.timelapse_directory} && echo exists",
                timeout=10,
            )
            if "exists" not in result.stdout:
                self.log("No timelapse directory found; skipping timelapse backup")
                return
        except CommandExecutionError:
            self.log("No timelapse directory found; skipping timelapse backup")
            return

        # Determine backup directory (same logic as moonraker stats)
        if backup_dir:
            base_dir = backup_dir
        elif use_temp:
            base_dir = Path(tempfile.gettempdir())
        else:
            base_dir = Path.cwd()

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

        # Use existing moonraker backup dir if available, otherwise create new
        if self.moonraker_backup_dir:
            self.timelapse_backup_dir = self.moonraker_backup_dir / "timelapse"
        else:
            backup_root = base_dir / f"printer_backup_{self.printer_ip}_{timestamp}"
            backup_root.mkdir(parents=True, exist_ok=True)
            self.timelapse_backup_dir = backup_root / "timelapse"

        self.timelapse_backup_dir.mkdir(parents=True, exist_ok=True)

        # List files in timelapse directory
        try:
            result = self.executor.run(
                f"ls -1 {self.config.timelapse_directory}",
                timeout=30,
            )
            files = [f.strip() for f in result.stdout.split("\n") if f.strip()]

            if not files:
                self.log("No timelapse files found to backup")
                return

        except CommandExecutionError:
            self.log("No timelapse files found to backup")
            return

        succeeded = 0
        failed: list[str] = []
        monitor: Optional[PrinterInstaller._TransferMonitor] = None

        with self.executor.sftp() as sftp:
            file_infos: list[tuple[str, str, int]] = []
            total_bytes = 0
            for filename in files:
                remote_path = f"{self.config.timelapse_directory}/{filename}"
                try:
                    attrs = sftp.stat(remote_path)
                except IOError as exc:
                    self.file_log(
                        f"Failed to inspect timelapse file {filename}: {exc}",
                        "WARNING",
                    )
                    continue
                if stat.S_ISDIR(getattr(attrs, "st_mode", 0)):
                    continue
                size = getattr(attrs, "st_size", 0) or 0
                file_infos.append((filename, remote_path, size))
                total_bytes += size

            if not file_infos:
                self.log("No timelapse files found to backup")
                return

            self.log(
                f"Backing up timelapse files ({self._format_file_count(len(file_infos))}, "
                f"{self._format_size(total_bytes)})..."
            )
            monitor = self._create_transfer_monitor(
                verb="Backing up",
                component="timelapse files",
                total_files=len(file_infos),
                total_bytes=total_bytes,
            )

            for filename, remote_path, file_size in file_infos:
                local_path = self.timelapse_backup_dir / filename
                if monitor:
                    monitor.start_file(filename, file_size)
                try:
                    if monitor and file_size:
                        sftp.get(
                            remote_path,
                            str(local_path),
                            callback=monitor.callback,
                        )
                    else:
                        sftp.get(remote_path, str(local_path))
                    if monitor:
                        monitor.finish_file()
                    self.timelapse_backup_files.append(local_path)
                    succeeded += 1
                except IOError as exc:
                    if monitor:
                        monitor.abort_current_file()
                    failed.append(filename)
                    self.file_log(f"Failed to backup {filename}: {exc}", "WARNING")

        if monitor:
            monitor.complete()

        if failed:
            self.log(
                f"Warning: Failed to backup {len(failed)} timelapse file(s)", "WARNING"
            )

        self.file_log(
            f"Successfully backed up {succeeded} timelapse file(s) to {self.timelapse_backup_dir}"
        )
        self.log(f"Backed up {succeeded} timelapse file(s)")

    def restore_timelapse_files(self, *, force: bool = False) -> None:
        """Restore timelapse files to the printer."""
        if not self.preserve_timelapses and not force:
            return
        if not self.timelapse_backup_files:
            self.file_log("No timelapse backup files found; skipping restore.")
            return

        self.file_log("Restoring timelapse files to device...")
        self.ensure_ssh_access()

        # Create timelapse directory if it doesn't exist
        self.executor.run(
            f"mkdir -p {self.config.timelapse_directory}",
            timeout=10,
        )

        file_entries: list[tuple[Path, int]] = []
        total_bytes = 0
        for local_path in self.timelapse_backup_files:
            if not local_path.exists():
                self.file_log(
                    f"Missing local backup file; skipping: {local_path}",
                    "WARNING",
                )
                continue
            size = local_path.stat().st_size
            file_entries.append((local_path, size))
            total_bytes += size

        if not file_entries:
            self.log("No timelapse files were restored.", "WARNING")
            return

        self.log(
            f"Restoring timelapse files ({self._format_file_count(len(file_entries))}, "
            f"{self._format_size(total_bytes)})..."
        )

        monitor = self._create_transfer_monitor(
            verb="Restoring",
            component="timelapse files",
            total_files=len(file_entries),
            total_bytes=total_bytes,
        )

        restored = 0
        failed: list[str] = []

        with self.executor.sftp() as sftp:
            for local_path, size in file_entries:
                remote_path = f"{self.config.timelapse_directory}/{local_path.name}"
                if monitor:
                    monitor.start_file(local_path.name, size)
                try:
                    if monitor and size:
                        sftp.put(
                            str(local_path),
                            remote_path,
                            callback=monitor.callback,
                        )
                    else:
                        sftp.put(str(local_path), remote_path)
                    if monitor:
                        monitor.finish_file()
                    restored += 1
                except IOError as exc:
                    if monitor:
                        monitor.abort_current_file()
                    failed.append(local_path.name)
                    self.file_log(
                        f"Failed to restore {local_path.name}: {exc}", "ERROR"
                    )

        if monitor:
            monitor.complete()

        if failed:
            self.log(
                f"Warning: Failed to restore {len(failed)} timelapse file(s)", "WARNING"
            )

        if restored == 0:
            self.log("No timelapse files were restored.", "WARNING")
        else:
            self.log(f"Restored {restored} timelapse file(s)")

    # -- GCode files backup/restore -------------------------------------

    def backup_gcode_files(
        self,
        *,
        force: bool = False,
        backup_dir: Optional[Path] = None,
        use_temp: bool = False,
    ) -> None:
        """Backup gcode files from the printer."""
        if not self.preserve_gcodes and not force:
            return

        self.file_log("Backing up gcode files from device...")
        self.ensure_ssh_access()

        # Check if gcodes directory exists
        try:
            result = self.executor.run(
                f"test -d {self.config.gcodes_directory} && echo exists",
                timeout=10,
            )
            if "exists" not in result.stdout:
                self.log("No gcodes directory found on device")
                return
        except CommandExecutionError:
            self.log("No gcodes directory found on device")
            return

        # Determine backup directory: explicit path > temp > current working directory
        if backup_dir:
            base_dir = backup_dir
        elif use_temp:
            base_dir = Path(tempfile.gettempdir())
        else:
            base_dir = Path.cwd()

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

        # Use existing backup dir if available, otherwise create new
        if self.moonraker_backup_dir:
            self.gcodes_backup_dir = self.moonraker_backup_dir / "gcodes"
        else:
            backup_root = base_dir / f"printer_backup_{self.printer_ip}_{timestamp}"
            backup_root.mkdir(parents=True, exist_ok=True)
            self.gcodes_backup_dir = backup_root / "gcodes"

        self.gcodes_backup_dir.mkdir(parents=True, exist_ok=True)

        # List files in gcodes directory
        try:
            result = self.executor.run(
                f"ls -1 {self.config.gcodes_directory}",
                timeout=30,
            )
            files = [f.strip() for f in result.stdout.split("\n") if f.strip()]

            if not files:
                self.log("No gcode files found to backup")
                return

        except CommandExecutionError:
            self.log("No gcode files found to backup")
            return

        succeeded = 0
        failed: list[str] = []
        monitor: Optional[PrinterInstaller._TransferMonitor] = None

        with self.executor.sftp() as sftp:
            file_infos: list[tuple[str, str, int]] = []
            total_bytes = 0
            for filename in files:
                remote_path = f"{self.config.gcodes_directory}/{filename}"
                try:
                    attrs = sftp.stat(remote_path)
                except IOError as exc:
                    self.file_log(
                        f"Failed to inspect gcode file {filename}: {exc}",
                        "WARNING",
                    )
                    continue
                if stat.S_ISDIR(getattr(attrs, "st_mode", 0)):
                    continue
                size = getattr(attrs, "st_size", 0) or 0
                file_infos.append((filename, remote_path, size))
                total_bytes += size

            if not file_infos:
                self.log("No gcode files found to backup")
                return

            self.log(
                f"Backing up gcode files ({self._format_file_count(len(file_infos))}, "
                f"{self._format_size(total_bytes)})..."
            )

            monitor = self._create_transfer_monitor(
                verb="Backing up",
                component="gcode files",
                total_files=len(file_infos),
                total_bytes=total_bytes,
            )

            for filename, remote_path, size in file_infos:
                local_path = self.gcodes_backup_dir / filename
                if monitor:
                    monitor.start_file(filename, size)
                try:
                    if monitor and size:
                        sftp.get(
                            remote_path,
                            str(local_path),
                            callback=monitor.callback,
                        )
                    else:
                        sftp.get(remote_path, str(local_path))
                    if monitor:
                        monitor.finish_file()
                    self.gcodes_backup_files.append(local_path)
                    succeeded += 1
                except IOError as exc:
                    if monitor:
                        monitor.abort_current_file()
                    failed.append(filename)
                    self.file_log(f"Failed to backup {filename}: {exc}", "WARNING")

        if monitor:
            monitor.complete()

        if failed:
            self.log(
                f"Warning: Failed to backup {len(failed)} gcode file(s)", "WARNING"
            )

        self.file_log(
            f"Successfully backed up {succeeded} gcode file(s) to {self.gcodes_backup_dir}"
        )
        self.log(f"Backed up {succeeded} gcode file(s)")

    def restore_gcode_files(self, *, force: bool = False) -> None:
        """Restore gcode files to the printer."""
        if not self.preserve_gcodes and not force:
            return
        if not self.gcodes_backup_files:
            self.file_log("No gcode backup files found; skipping restore.")
            return

        self.file_log("Restoring gcode files to device...")
        self.ensure_ssh_access()

        # Create gcodes directory if it doesn't exist
        self.executor.run(
            f"mkdir -p {self.config.gcodes_directory}",
            timeout=10,
        )

        file_entries: list[tuple[Path, int]] = []
        total_bytes = 0
        for local_path in self.gcodes_backup_files:
            if not local_path.exists():
                self.file_log(
                    f"Missing local backup file; skipping: {local_path}",
                    "WARNING",
                )
                continue
            size = local_path.stat().st_size
            file_entries.append((local_path, size))
            total_bytes += size

        if not file_entries:
            self.log("No gcode files were restored.", "WARNING")
            return

        self.log(
            f"Restoring gcode files ({self._format_file_count(len(file_entries))}, "
            f"{self._format_size(total_bytes)})..."
        )

        monitor = self._create_transfer_monitor(
            verb="Restoring",
            component="gcode files",
            total_files=len(file_entries),
            total_bytes=total_bytes,
        )

        restored = 0
        failed: list[str] = []

        with self.executor.sftp() as sftp:
            for local_path, size in file_entries:
                remote_path = f"{self.config.gcodes_directory}/{local_path.name}"
                if monitor:
                    monitor.start_file(local_path.name, size)
                try:
                    if monitor and size:
                        sftp.put(
                            str(local_path),
                            remote_path,
                            callback=monitor.callback,
                        )
                    else:
                        sftp.put(str(local_path), remote_path)
                    if monitor:
                        monitor.finish_file()
                    restored += 1
                except IOError as exc:
                    if monitor:
                        monitor.abort_current_file()
                    failed.append(local_path.name)
                    self.file_log(
                        f"Failed to restore {local_path.name}: {exc}", "ERROR"
                    )

        if monitor:
            monitor.complete()

        if failed:
            self.log(
                f"Warning: Failed to restore {len(failed)} gcode file(s)", "WARNING"
            )

        if restored == 0:
            self.log("No gcode files were restored.", "WARNING")
        else:
            self.log(f"Restored {restored} gcode file(s)")

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
        self.file_log("Installing nano via opkg...")
        self.executor.run("/opt/bin/opkg install nano", timeout=120)
        self.file_log("Nano package installed via opkg")

    def run_k2_improvements(self) -> None:
        self.file_log("Running k2-improvements script (this may take 10-20 minutes)...")

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
        self.file_log(f"Cloning repository and switching to branch '{self.branch}'...")

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

    def install(
        self,
        *,
        run_bootstrap: bool = True,
        run_k2: bool = True,
        run_repo: bool = True,
    ) -> None:
        try:
            self.log(f"Starting printer installation for {self.printer_ip}")
            self.log(f"Branch: {self.branch}")
            self.log("")

            self.file_log("Starting full printer installation...")
            self.file_log(f"Target: {self.printer_ip}")
            self.file_log(f"Branch: {self.branch}")
            selection = [
                name
                for enabled, name in (
                    (run_bootstrap, "bootstrap"),
                    (run_k2, "k2-improvements"),
                    (run_repo, "repo-install"),
                )
                if enabled
            ]
            if len(selection) != 3:
                selected_text = ", ".join(selection) if selection else "none"
                self.file_log(f"Selected operations: {selected_text}")

            self.log_step(1, "Setting up SSH access")
            self.ensure_ssh_access()
            self.log("SSH access configured")

            step_number = 2

            if run_bootstrap:
                self.log_step(step_number, "Deploying bootstrap (upload + run)")
                self.upload_bootstrap()
                self.log("Bootstrap files uploaded")
                self.run_bootstrap_script()
                self.log("Bootstrap script completed")
                step_number += 1

            if run_k2:
                self.log_step(step_number, "Running k2-improvements script (10-20 min)")
                self.run_k2_improvements()
                self.log("K2 improvements completed")
                step_number += 1

            if run_repo:
                self.log_step(step_number, "Cloning and installing repository")
                self.clone_and_install_repo()
                self.log("Repository installation completed")
                step_number += 1
                self.install_public_key()

            if self.preserve_stats:
                self.log_step(step_number, "Restoring Moonraker stats")
                try:
                    self.restore_moonraker_stats()
                    self.log("Moonraker stats restore completed")
                except InstallerError as exc:
                    self.log(f"Moonraker stats restore failed: {exc}", "ERROR")
                step_number += 1

            if self.preserve_timelapses:
                self.log_step(step_number, "Restoring timelapse files")
                try:
                    self.restore_timelapse_files()
                    self.log("Timelapse files restore completed")
                except InstallerError as exc:
                    self.log(f"Timelapse files restore failed: {exc}", "ERROR")
                step_number += 1

            if self.preserve_gcodes:
                self.log_step(step_number, "Restoring gcode files")
                try:
                    self.restore_gcode_files()
                    self.log("GCode files restore completed")
                except InstallerError as exc:
                    self.log(f"GCode files restore failed: {exc}", "ERROR")
                step_number += 1

            total_time = time.time() - self.start_time
            minutes = int(total_time // 60)
            seconds = int(total_time % 60)

            self.log("")
            self.log(f"Successfully installed in {minutes}m {seconds}s")

            self.file_log("Full installation completed successfully!")
            self.file_log(f"Total installation time: {minutes}m {seconds}s")

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

        self.log(f"Installation failed: {error}", "ERROR")
        self.log(
            f"Installation time before failure: {minutes}m {seconds}s",
            "ERROR",
        )
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
        "--reset-only",
        action="store_true",
        help="Factory reset the device without running installation",
    )
    parser.add_argument(
        "--preserve-stats",
        "--backup",
        dest="preserve_stats",
        action="store_true",
        help="Backup and restore Moonraker stats across factory reset (requires --reset or --reset-only)",
    )
    parser.add_argument(
        "--preserve-timelapses",
        action="store_true",
        help="Backup and restore timelapse files across factory reset (requires --reset or --reset-only)",
    )
    parser.add_argument(
        "--preserve-gcodes",
        action="store_true",
        help="Backup and restore gcode files across factory reset (requires --reset or --reset-only)",
    )
    parser.add_argument(
        "--key-only",
        dest="key_only",
        action="store_true",
        help="Only ensure SSH access and install local public key on the printer",
    )
    parser.add_argument(
        "--backup-only",
        nargs="?",
        const=None,
        default=False,
        metavar="BACKUP_DIR",
        help="Perform backup to specified directory (or current directory if not specified). Use with --backup-moonraker and/or --backup-timelapses to select components (defaults to both if neither specified).",
    )
    parser.add_argument(
        "--restore-only",
        metavar="BACKUP_DIR",
        help="Restore from specified backup directory without installation. Use with --restore-moonraker and/or --restore-timelapses to select components (defaults to both if neither specified).",
    )
    parser.add_argument(
        "--restore-backup",
        metavar="BACKUP_DIR",
        help="Specify backup directory to use for restore during installation",
    )
    parser.add_argument(
        "--backup-moonraker",
        action="store_true",
        help="Include Moonraker stats in backup (use with --backup-only)",
    )
    parser.add_argument(
        "--backup-timelapses",
        action="store_true",
        help="Include timelapse files in backup (use with --backup-only)",
    )
    parser.add_argument(
        "--backup-gcodes",
        action="store_true",
        help="Include gcode files in backup (use with --backup-only)",
    )
    parser.add_argument(
        "--restore-moonraker",
        action="store_true",
        help="Include Moonraker stats in restore (use with --restore-only)",
    )
    parser.add_argument(
        "--restore-timelapses",
        action="store_true",
        help="Include timelapse files in restore (use with --restore-only)",
    )
    parser.add_argument(
        "--restore-gcodes",
        action="store_true",
        help="Include gcode files in restore (use with --restore-only)",
    )
    parser.add_argument(
        "--run-bootstrap",
        action="store_true",
        help="Upload bootstrap archive and run bootstrap script only",
    )
    parser.add_argument(
        "--run-k2",
        action="store_true",
        help="Run the k2-improvements script",
    )
    parser.add_argument(
        "--run-repo",
        action="store_true",
        help="Clone the repository and execute install.sh",
    )

    args = parser.parse_args()

    # Validate modes: --key-only, --backup-only, --restore-only are mutually exclusive
    modes_selected = sum(
        1
        for x in (
            args.key_only,
            args.backup_only is not False,
            bool(args.restore_only),
            args.reset_only,
        )
        if x
    )
    if modes_selected > 1:
        print(
            "ERROR: Choose only one mode: --key-only, --backup-only, --restore-only, or --reset-only."
        )
        sys.exit(2)

    # Forbid mixing modes with install modifiers
    if args.key_only or args.backup_only is not False or args.restore_only:
        if (
            args.reset
            or args.reset_only
            or args.preserve_stats
            or args.preserve_timelapses
            or args.preserve_gcodes
            or args.restore_backup
        ):
            print(
                "ERROR: Standalone backup/restore modes cannot be combined with --reset, --reset-only, --preserve-stats, --preserve-timelapses, --preserve-gcodes, or --restore-backup."
            )
            sys.exit(2)

    # Validate component flags are only used with their respective operations
    if args.backup_moonraker and args.backup_only is False:
        print("ERROR: --backup-moonraker can only be used with --backup-only.")
        sys.exit(2)
    if args.backup_timelapses and args.backup_only is False:
        print("ERROR: --backup-timelapses can only be used with --backup-only.")
        sys.exit(2)
    if args.backup_gcodes and args.backup_only is False:
        print("ERROR: --backup-gcodes can only be used with --backup-only.")
        sys.exit(2)
    if args.restore_moonraker and not args.restore_only:
        print("ERROR: --restore-moonraker can only be used with --restore-only.")
        sys.exit(2)
    if args.restore_timelapses and not args.restore_only:
        print("ERROR: --restore-timelapses can only be used with --restore-only.")
        sys.exit(2)
    if args.restore_gcodes and not args.restore_only:
        print("ERROR: --restore-gcodes can only be used with --restore-only.")
        sys.exit(2)

    if args.reset_only:
        if args.reset:
            print("ERROR: --reset-only cannot be combined with --reset.")
            sys.exit(2)
        if args.restore_backup:
            print("ERROR: --reset-only cannot be combined with --restore-backup.")
            sys.exit(2)

    # --preserve-stats requires --reset or --reset-only
    if args.preserve_stats and not (args.reset or args.reset_only):
        print(
            "ERROR: --preserve-stats/--backup can only be used together with --reset or --reset-only."
        )
        sys.exit(2)

    # --preserve-timelapses requires --reset or --reset-only
    if args.preserve_timelapses and not (args.reset or args.reset_only):
        print(
            "ERROR: --preserve-timelapses can only be used together with --reset or --reset-only."
        )
        sys.exit(2)

    # --preserve-gcodes requires --reset or --reset-only
    if args.preserve_gcodes and not (args.reset or args.reset_only):
        print(
            "ERROR: --preserve-gcodes can only be used together with --reset or --reset-only."
        )
        sys.exit(2)

    # --preserve-stats conflicts with --restore-backup
    if args.preserve_stats and args.restore_backup:
        print("ERROR: --preserve-stats cannot be used together with --restore-backup.")
        sys.exit(2)

    # Validate provided backup directories exist and are complete
    def _validate_backup_dir(
        path_str: str,
        *,
        requires_moonraker: bool = False,
        requires_timelapses: bool = False,
        requires_gcodes: bool = False,
    ) -> None:
        backup_dir = Path(path_str)
        if not backup_dir.exists() or not backup_dir.is_dir():
            print(f"ERROR: Backup directory does not exist: {backup_dir}")
            sys.exit(2)

        # Only validate if requirements are specified
        if requires_moonraker:
            required_files = ["data.mdb", "moonraker-sql.db"]
            missing = [
                name for name in required_files if not (backup_dir / name).exists()
            ]
            if missing:
                print(
                    f"ERROR: Backup directory is missing required Moonraker files: {', '.join(missing)} in {backup_dir}"
                )
                sys.exit(2)

        if requires_timelapses:
            timelapse_dir = backup_dir / "timelapse"
            if not timelapse_dir.exists() or not timelapse_dir.is_dir():
                print(
                    f"ERROR: Backup directory is missing timelapse subdirectory: {timelapse_dir}"
                )
                sys.exit(2)

        if requires_gcodes:
            gcodes_dir = backup_dir / "gcodes"
            if not gcodes_dir.exists() or not gcodes_dir.is_dir():
                print(
                    f"ERROR: Backup directory is missing gcodes subdirectory: {gcodes_dir}"
                )
                sys.exit(2)

    if args.restore_only:
        # Validate based on component flags (default to all if none specified)
        restore_moonraker = args.restore_moonraker or (
            not args.restore_moonraker
            and not args.restore_timelapses
            and not args.restore_gcodes
        )
        restore_timelapses = args.restore_timelapses or (
            not args.restore_moonraker
            and not args.restore_timelapses
            and not args.restore_gcodes
        )
        restore_gcodes = args.restore_gcodes or (
            not args.restore_moonraker
            and not args.restore_timelapses
            and not args.restore_gcodes
        )
        _validate_backup_dir(
            args.restore_only,
            requires_moonraker=restore_moonraker,
            requires_timelapses=restore_timelapses,
            requires_gcodes=restore_gcodes,
        )

    if args.restore_backup:
        # For restore-backup during installation, validate all components exist
        _validate_backup_dir(
            args.restore_backup,
            requires_moonraker=True,
            requires_timelapses=True,
            requires_gcodes=True,
        )

    installer = PrinterInstaller(
        printer_ip=args.printer_ip,
        branch=args.branch,
        password=args.password,
        reset=args.reset or args.reset_only,
        preserve_stats=args.preserve_stats,
        preserve_timelapses=args.preserve_timelapses,
        preserve_gcodes=args.preserve_gcodes,
    )

    if args.key_only:
        installer.ensure_ssh_access()
        if installer.install_public_key():
            sys.exit(0)
        else:
            installer.log("Failed to configure public SSH key.", "ERROR")
            sys.exit(1)

    if args.backup_only is not False:
        installer.ensure_ssh_access()
        backup_dir = Path(args.backup_only) if args.backup_only else None

        # Determine which components to backup (default to all if none specified)
        backup_moonraker = args.backup_moonraker or (
            not args.backup_moonraker
            and not args.backup_timelapses
            and not args.backup_gcodes
        )
        backup_timelapses = args.backup_timelapses or (
            not args.backup_moonraker
            and not args.backup_timelapses
            and not args.backup_gcodes
        )
        backup_gcodes = args.backup_gcodes or (
            not args.backup_moonraker
            and not args.backup_timelapses
            and not args.backup_gcodes
        )

        components_backed_up = []

        if backup_moonraker:
            installer.backup_moonraker_stats(force=True, backup_dir=backup_dir)
            components_backed_up.append("Moonraker stats")

        if backup_timelapses:
            installer.backup_timelapse_files(force=True, backup_dir=backup_dir)
            components_backed_up.append("timelapses")

        if backup_gcodes:
            installer.backup_gcode_files(force=True, backup_dir=backup_dir)
            components_backed_up.append("gcodes")

        if components_backed_up:
            print(
                f"Backup completed successfully. Backed up: {', '.join(components_backed_up)}"
            )
            if installer.moonraker_backup_dir:
                print(f"Saved to: {installer.moonraker_backup_dir}")
            elif installer.timelapse_backup_dir:
                print(f"Saved to: {installer.timelapse_backup_dir}")
            elif installer.gcodes_backup_dir:
                print(f"Saved to: {installer.gcodes_backup_dir}")
        else:
            print("No components selected for backup.")
            sys.exit(1)
        sys.exit(0)

    if args.restore_only:
        installer.ensure_ssh_access()
        backup_dir = Path(args.restore_only)

        # Determine which components to restore (default to all if none specified)
        restore_moonraker = args.restore_moonraker or (
            not args.restore_moonraker
            and not args.restore_timelapses
            and not args.restore_gcodes
        )
        restore_timelapses = args.restore_timelapses or (
            not args.restore_moonraker
            and not args.restore_timelapses
            and not args.restore_gcodes
        )
        restore_gcodes = args.restore_gcodes or (
            not args.restore_moonraker
            and not args.restore_timelapses
            and not args.restore_gcodes
        )

        components_restored = []

        if restore_moonraker:
            installer.moonraker_backup_dir = backup_dir
            installer.moonraker_backup_files = {
                "data.mdb": backup_dir / "data.mdb",
                "moonraker-sql.db": backup_dir / "moonraker-sql.db",
            }
            installer.restore_moonraker_stats(force=True)
            components_restored.append("Moonraker stats")

        if restore_timelapses:
            timelapse_dir = backup_dir / "timelapse"
            if timelapse_dir.exists():
                installer.timelapse_backup_dir = timelapse_dir
                installer.timelapse_backup_files = list(timelapse_dir.glob("*"))
                installer.restore_timelapse_files(force=True)
                components_restored.append("timelapses")
            else:
                installer.log(
                    "No timelapse directory found in backup; skipping timelapse restore.",
                    "WARNING",
                )

        if restore_gcodes:
            gcodes_dir = backup_dir / "gcodes"
            if gcodes_dir.exists():
                installer.gcodes_backup_dir = gcodes_dir
                installer.gcodes_backup_files = list(gcodes_dir.glob("*"))
                installer.restore_gcode_files(force=True)
                components_restored.append("gcodes")
            else:
                installer.log(
                    "No gcodes directory found in backup; skipping gcodes restore.",
                    "WARNING",
                )

        if components_restored:
            print(
                f"Restore completed successfully. Restored: {', '.join(components_restored)}"
            )
        else:
            print("No components were restored.")
            sys.exit(1)
        sys.exit(0)

    if args.reset_only:
        if args.preserve_stats:
            installer.backup_moonraker_stats(use_temp=True)
        if args.preserve_timelapses:
            installer.backup_timelapse_files(use_temp=True)
        if args.preserve_gcodes:
            installer.backup_gcode_files(use_temp=True)
        installer.reset_device()
        installer.log("Factory reset completed successfully.")
        installer.executor.close()
        sys.exit(0)

    if args.reset and args.preserve_stats:
        if args.restore_backup:
            # Use manually specified backup
            installer.moonraker_backup_dir = Path(args.restore_backup)
            installer.moonraker_backup_files = {
                "data.mdb": installer.moonraker_backup_dir / "data.mdb",
                "moonraker-sql.db": installer.moonraker_backup_dir / "moonraker-sql.db",
            }
        else:
            # Create new backup in temp (automated, single-session)
            installer.backup_moonraker_stats(use_temp=True)

    if args.reset and args.preserve_timelapses:
        if args.restore_backup:
            # Load from existing backup
            timelapse_dir = Path(args.restore_backup) / "timelapse"
            if timelapse_dir.exists():
                installer.timelapse_backup_dir = timelapse_dir
                installer.timelapse_backup_files = list(timelapse_dir.glob("*"))
        else:
            # Create new backup in temp (automated, single-session)
            installer.backup_timelapse_files(use_temp=True)

    if args.reset and args.preserve_gcodes:
        if args.restore_backup:
            # Load from existing backup
            gcodes_dir = Path(args.restore_backup) / "gcodes"
            if gcodes_dir.exists():
                installer.gcodes_backup_dir = gcodes_dir
                installer.gcodes_backup_files = list(gcodes_dir.glob("*"))
        else:
            # Create new backup in temp (automated, single-session)
            installer.backup_gcode_files(use_temp=True)

    if args.reset:
        installer.reset_device()
        installer.ensure_ssh_access()

    selected_steps_provided = any((args.run_bootstrap, args.run_k2, args.run_repo))
    installer.install(
        run_bootstrap=args.run_bootstrap or not selected_steps_provided,
        run_k2=args.run_k2 or not selected_steps_provided,
        run_repo=args.run_repo or not selected_steps_provided,
    )

    # If a specific backup was requested for restore during install, perform restore now
    if args.restore_backup:
        installer.moonraker_backup_dir = Path(args.restore_backup)
        installer.moonraker_backup_files = {
            "data.mdb": installer.moonraker_backup_dir / "data.mdb",
            "moonraker-sql.db": installer.moonraker_backup_dir / "moonraker-sql.db",
        }
        installer.restore_moonraker_stats(force=True)


if __name__ == "__main__":
    main()
