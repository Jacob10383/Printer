#!/usr/bin/env python3

from __future__ import annotations

import contextlib
import importlib
import io
import sys
from typing import Iterable, Mapping


class _QueueStream(io.TextIOBase):
    """Redirect text output into a multiprocessing queue."""

    def __init__(self, queue):
        super().__init__()
        self._queue = queue
        self._buffer = ""

    def write(self, data: str) -> int:
        if not data:
            return 0
        text = str(data)
        self._buffer += text
        while "\n" in self._buffer:
            line, self._buffer = self._buffer.split("\n", 1)
            self._queue.put({"type": "log", "data": line + "\n"})
        return len(text)

    def flush(self) -> None:
        if self._buffer:
            self._queue.put({"type": "log", "data": self._buffer})
            self._buffer = ""
    
    def put(self, message: Mapping) -> None:
        """Allow direct queue access for non-log messages."""
        self._queue.put(message)


def _resolve_exit_code(exc: SystemExit) -> tuple[int, str | None]:
    code_obj = exc.code
    if code_obj is None:
        return 0, None
    if isinstance(code_obj, int):
        return code_obj, None
    return 1, str(code_obj)


def run_installation(cli_args: Iterable[str], message_queue) -> None:
    """Run fullinstaller.main() and forward stdout/stderr to parent process."""
    try:
        module = importlib.import_module("fullinstaller")
        module = importlib.reload(module)
    except Exception as exc:
        message_queue.put(
            {
                "type": "exit",
                "code": 1,
                "error": f"Failed to load fullinstaller: {exc}",
            }
        )
        return

    stream = _QueueStream(message_queue)

    original_stdout = sys.stdout
    original_stderr = sys.stderr
    original_argv = sys.argv.copy()

    sys.stdout = stream
    sys.stderr = stream
    sys.argv = ["fullinstaller.py", *cli_args]

    exit_code = 0
    rendered_error: str | None = None

    try:
        module.main()
    except SystemExit as exc:
        exit_code, rendered_error = _resolve_exit_code(exc)
    except Exception as exc:  # pragma: no cover - defensive
        exit_code = 1
        rendered_error = f"Unhandled error: {exc}"
    finally:
        stream.flush()
        sys.stdout = original_stdout
        sys.stderr = original_stderr
        sys.argv = original_argv

        logger = getattr(module, "logging", None)
        if logger:
            printer_logger = logger.getLogger("printer_installer")
            for handler in list(printer_logger.handlers):
                printer_logger.removeHandler(handler)
                with contextlib.suppress(Exception):
                    handler.close()

    if rendered_error:
        message_queue.put({"type": "log", "data": rendered_error + "\n"})

    message_queue.put({"type": "exit", "code": int(exit_code)})
