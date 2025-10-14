from __future__ import annotations

import shlex
import subprocess
from dataclasses import dataclass
from typing import Iterable, Mapping, Optional

from .logging_utils import get_logger


@dataclass
class CommandResult:
    command: str
    returncode: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        return self.returncode == 0


def _format_command(command: str | Iterable[str]) -> str:
    if isinstance(command, str):
        return command
    return " ".join(shlex.quote(str(part)) for part in command)


def run(
    command: str | Iterable[str],
    *,
    check: bool = False,
    capture_output: bool = True,
    text: bool = True,
    env: Optional[Mapping[str, str]] = None,
    cwd: Optional[str] = None,
) -> CommandResult:
    """Run a command and return a normalized `CommandResult`."""
    formatted = _format_command(command)
    completed = subprocess.run(
        formatted,
        shell=True,
        capture_output=capture_output,
        text=text,
        env=env,
        cwd=cwd,
    )
    result = CommandResult(formatted, completed.returncode, completed.stdout or "", completed.stderr or "")
    if check and not result.ok:
        raise subprocess.CalledProcessError(result.returncode, formatted, output=result.stdout, stderr=result.stderr)
    return result


def run_logged(
    command: str | Iterable[str],
    *,
    logger_name: str = "shell",
    check: bool = False,
    capture_output: bool = True,
    env: Optional[Mapping[str, str]] = None,
    cwd: Optional[str] = None,
) -> CommandResult:
    """Run a command and log stdout/stderr if it fails or when verbose."""
    logger = get_logger(logger_name)
    result = run(command, check=False, capture_output=capture_output, env=env, cwd=cwd)
    if result.ok:
        logger.info("CMD OK: %s", result.command)
        if capture_output and result.stdout.strip():
            logger.debug(result.stdout.strip())
    else:
        logger.error("CMD FAIL (%s): rc=%s", result.command, result.returncode)
        if result.stdout.strip():
            logger.error("STDOUT: %s", result.stdout.strip())
        if result.stderr.strip():
            logger.error("STDERR: %s", result.stderr.strip())
        if check:
            raise subprocess.CalledProcessError(result.returncode, result.command, output=result.stdout, stderr=result.stderr)
    return result


def stream_command(command: str | Iterable[str], prefix: Optional[str] = None) -> int:
    """Stream a command's output live (used for nested installers)."""
    formatted = _format_command(command)
    process = subprocess.Popen(
        formatted,
        shell=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    assert process.stdout
    prefix_text = f"{prefix} " if prefix else ""
    for line in iter(process.stdout.readline, ""):
        line = line.rstrip("\n")
        if prefix:
            print(f"{prefix_text}{line}", flush=True)
        else:
            print(line, flush=True)
    process.stdout.close()
    return process.wait()
