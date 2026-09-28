"""Versioned JSON-lines client for a separately installed Fast OFM Core.

The GPL server deliberately does not import the noncommercial implementation.
The configured executable is an independently replaceable program and receives
only JSON values and ordinary file-artifact references.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import queue
import subprocess
import threading
import uuid
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

LOGGER = logging.getLogger(__name__)
PROTOCOL_VERSION = "1.0"


class FastOFMCoreError(RuntimeError):
    """The external core was unavailable or violated its protocol."""

    def __init__(self, code: str, message: str, *, retryable: bool = False) -> None:
        """Retain the bounded machine code and retry policy from the response."""
        super().__init__(message)
        self.code = code
        self.retryable = retryable


class FastOFMCoreProcess:
    """Serialize requests over one persistent, replaceable subprocess."""

    def __init__(
        self,
        command: Sequence[str] = ("fast-ofm-core", "serve-jsonl"),
        *,
        environment: Mapping[str, str] | None = None,
        artifact_roots: Sequence[Path] = (),
        maximum_response_bytes: int = 16 * 1024 * 1024,
    ) -> None:
        """Configure an argv-only child process without invoking a shell."""
        if not command or any(
            not isinstance(value, str) or not value for value in command
        ):
            raise ValueError("Core command must contain non-empty argument strings")
        if maximum_response_bytes < 1024:
            raise ValueError("maximum_response_bytes is too small")
        root_arguments = tuple(
            argument
            for root in artifact_roots
            for argument in ("--artifact-root", str(Path(root).resolve()))
        )
        self.command = (*command, *root_arguments)
        self.environment = dict(environment) if environment is not None else None
        self.maximum_response_bytes = maximum_response_bytes
        self._process: asyncio.subprocess.Process | None = None
        self._lock = asyncio.Lock()
        self._stderr_task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        """Start once and keep the child warm across coarse operations."""
        if self._process is not None and self._process.returncode is None:
            return
        try:
            self._process = await asyncio.create_subprocess_exec(
                *self.command,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=self.environment,
                limit=self.maximum_response_bytes,
            )
        except OSError as error:
            raise FastOFMCoreError(
                "CORE_UNAVAILABLE", str(error), retryable=True
            ) from error
        self._stderr_task = asyncio.create_task(self._drain_stderr())

    async def _drain_stderr(self) -> None:
        process = self._process
        if process is None or process.stderr is None:
            return
        while line := await process.stderr.readline():
            LOGGER.info("Fast OFM Core: %s", line.decode(errors="replace").rstrip())

    async def request(  # noqa: C901 - protocol validation is intentionally linear
        self,
        operation: str,
        payload: Mapping[str, object],
        *,
        timeout_s: float = 30.0,
    ) -> dict[str, Any]:
        """Submit one request and require a matching finite response envelope."""
        if not operation or timeout_s <= 0:
            raise ValueError("Operation and a positive timeout are required")
        async with self._lock:
            await self.start()
            process = self._process
            if process is None or process.stdin is None or process.stdout is None:
                raise FastOFMCoreError(
                    "CORE_UNAVAILABLE", "Core process has no protocol streams"
                )
            request_id = str(uuid.uuid4())
            envelope = {
                "protocol_version": PROTOCOL_VERSION,
                "request_id": request_id,
                "operation": operation,
                "timeout_ms": max(1, min(3_600_000, int(timeout_s * 1000))),
                "payload": dict(payload),
            }
            encoded = (
                json.dumps(envelope, separators=(",", ":"), allow_nan=False).encode()
                + b"\n"
            )
            try:
                process.stdin.write(encoded)
                await process.stdin.drain()
                line = await asyncio.wait_for(
                    process.stdout.readline(), timeout=timeout_s
                )
            except (BrokenPipeError, ConnectionError) as error:
                raise FastOFMCoreError(
                    "CORE_DISCONNECTED", str(error), retryable=True
                ) from error
            except TimeoutError as error:
                raise FastOFMCoreError(
                    "CORE_TIMEOUT", "Core request timed out", retryable=True
                ) from error
            if not line:
                return_code = await process.wait()
                raise FastOFMCoreError(
                    "CORE_EXITED",
                    f"Core exited before replying (status {return_code})",
                    retryable=True,
                )
            if len(line) >= self.maximum_response_bytes:
                raise FastOFMCoreError(
                    "CORE_RESPONSE_TOO_LARGE", "Core response exceeds its bound"
                )
            try:
                response = json.loads(line)
            except (UnicodeDecodeError, json.JSONDecodeError) as error:
                raise FastOFMCoreError(
                    "CORE_PROTOCOL_ERROR", "Core returned invalid JSON"
                ) from error
            if not isinstance(response, dict):
                raise FastOFMCoreError(
                    "CORE_PROTOCOL_ERROR", "Core response is not an object"
                )
            if (
                response.get("protocol_version") != PROTOCOL_VERSION
                or response.get("request_id") != request_id
            ):
                raise FastOFMCoreError(
                    "CORE_PROTOCOL_ERROR",
                    "Core response identity does not match request",
                )
            if response.get("status") in {"failed", "refused", "cancelled"}:
                error_value = response.get("error")
                if not isinstance(error_value, dict):
                    raise FastOFMCoreError(
                        "CORE_PROTOCOL_ERROR", "Core failure has no error object"
                    )
                raise FastOFMCoreError(
                    str(error_value.get("code", "CORE_ERROR")),
                    str(error_value.get("message", "Core operation failed")),
                    retryable=error_value.get("retryable") is True,
                )
            if response.get("status") not in {"completed", "accepted"}:
                raise FastOFMCoreError(
                    "CORE_PROTOCOL_ERROR", "Core returned an unknown status"
                )
            return response

    async def capabilities(self, *, timeout_s: float = 5.0) -> dict[str, Any]:
        """Discover exact operations instead of assuming an installed version."""
        response = await self.request("core.capabilities", {}, timeout_s=timeout_s)
        result = response.get("result")
        if not isinstance(result, dict):
            raise FastOFMCoreError(
                "CORE_PROTOCOL_ERROR", "Capability result is not an object"
            )
        return result

    async def close(self) -> None:
        """Terminate only the owned child and release its stream tasks."""
        process, self._process = self._process, None
        if process is not None and process.returncode is None:
            process.terminate()
            try:
                await asyncio.wait_for(process.wait(), timeout=2.0)
            except TimeoutError:
                process.kill()
                await process.wait()
        if self._stderr_task is not None:
            self._stderr_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._stderr_task
            self._stderr_task = None

    async def __aenter__(self) -> FastOFMCoreProcess:
        """Start the owned child for an asynchronous context."""
        await self.start()
        return self

    async def __aexit__(self, *_exc: object) -> None:
        """Close the owned child on every context exit."""
        await self.close()


class FastOFMCoreBlockingProcess:
    """Synchronous persistent client for LabThings actions and worker threads."""

    def __init__(
        self,
        command: Sequence[str] = ("fast-ofm-core", "serve-jsonl"),
        *,
        environment: Mapping[str, str] | None = None,
        artifact_roots: Sequence[Path] = (),
        maximum_response_bytes: int = 16 * 1024 * 1024,
    ) -> None:
        """Configure an argv-only child and its allow-listed artifact roots."""
        if not command or any(
            not isinstance(value, str) or not value for value in command
        ):
            raise ValueError("Core command must contain non-empty argument strings")
        if maximum_response_bytes < 1024:
            raise ValueError("maximum_response_bytes is too small")
        root_arguments = tuple(
            argument
            for root in artifact_roots
            for argument in ("--artifact-root", str(Path(root).resolve()))
        )
        self.command = (*command, *root_arguments)
        self.environment = dict(environment) if environment is not None else None
        self.maximum_response_bytes = maximum_response_bytes
        self._process: subprocess.Popen[bytes] | None = None
        self._responses: queue.Queue[bytes] = queue.Queue(maxsize=1)
        self._lock = threading.Lock()
        self._stdout_thread: threading.Thread | None = None
        self._stderr_thread: threading.Thread | None = None

    def start(self) -> None:
        """Start the child and bounded pipe readers exactly once."""
        if self._process is not None and self._process.poll() is None:
            return
        try:
            self._process = subprocess.Popen(
                self.command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=self.environment,
            )
        except OSError as error:
            raise FastOFMCoreError(
                "CORE_UNAVAILABLE", str(error), retryable=True
            ) from error
        self._responses = queue.Queue(maxsize=1)
        self._stdout_thread = threading.Thread(target=self._read_stdout, daemon=True)
        self._stderr_thread = threading.Thread(target=self._read_stderr, daemon=True)
        self._stdout_thread.start()
        self._stderr_thread.start()

    def _read_stdout(self) -> None:
        process = self._process
        if process is None or process.stdout is None:
            return
        while line := process.stdout.readline(self.maximum_response_bytes + 1):
            self._responses.put(line)
        self._responses.put(b"")

    def _read_stderr(self) -> None:
        process = self._process
        if process is None or process.stderr is None:
            return
        while line := process.stderr.readline():
            LOGGER.info("Fast OFM Core: %s", line.decode(errors="replace").rstrip())

    def request(  # noqa: C901 - response validation is intentionally linear
        self,
        operation: str,
        payload: Mapping[str, object],
        *,
        timeout_s: float = 30.0,
    ) -> dict[str, Any]:
        """Submit one blocking request with timeout and strict response identity."""
        if not operation or timeout_s <= 0:
            raise ValueError("Operation and a positive timeout are required")
        with self._lock:
            self.start()
            process = self._process
            if process is None or process.stdin is None:
                raise FastOFMCoreError(
                    "CORE_UNAVAILABLE", "Core process has no input stream"
                )
            request_id = str(uuid.uuid4())
            envelope = {
                "protocol_version": PROTOCOL_VERSION,
                "request_id": request_id,
                "operation": operation,
                "timeout_ms": max(1, min(3_600_000, int(timeout_s * 1000))),
                "payload": dict(payload),
            }
            try:
                process.stdin.write(
                    json.dumps(
                        envelope, separators=(",", ":"), allow_nan=False
                    ).encode()
                    + b"\n"
                )
                process.stdin.flush()
                line = self._responses.get(timeout=timeout_s)
            except (BrokenPipeError, OSError) as error:
                raise FastOFMCoreError(
                    "CORE_DISCONNECTED", str(error), retryable=True
                ) from error
            except queue.Empty as error:
                self.close()
                raise FastOFMCoreError(
                    "CORE_TIMEOUT", "Core request timed out", retryable=True
                ) from error
            if not line:
                return_code = process.poll()
                raise FastOFMCoreError(
                    "CORE_EXITED",
                    f"Core exited before replying (status {return_code})",
                    retryable=True,
                )
            if len(line) > self.maximum_response_bytes:
                raise FastOFMCoreError(
                    "CORE_RESPONSE_TOO_LARGE", "Core response exceeds its bound"
                )
            try:
                response = json.loads(line)
            except (UnicodeDecodeError, json.JSONDecodeError) as error:
                raise FastOFMCoreError(
                    "CORE_PROTOCOL_ERROR", "Core returned invalid JSON"
                ) from error
            if not isinstance(response, dict) or (
                response.get("protocol_version") != PROTOCOL_VERSION
                or response.get("request_id") != request_id
            ):
                raise FastOFMCoreError(
                    "CORE_PROTOCOL_ERROR",
                    "Core response identity does not match request",
                )
            if response.get("status") in {"failed", "refused", "cancelled"}:
                error_value = response.get("error")
                if not isinstance(error_value, dict):
                    raise FastOFMCoreError(
                        "CORE_PROTOCOL_ERROR", "Core failure has no error object"
                    )
                raise FastOFMCoreError(
                    str(error_value.get("code", "CORE_ERROR")),
                    str(error_value.get("message", "Core operation failed")),
                    retryable=error_value.get("retryable") is True,
                )
            if response.get("status") not in {"completed", "accepted"}:
                raise FastOFMCoreError(
                    "CORE_PROTOCOL_ERROR", "Core returned an unknown status"
                )
            return response

    def capabilities(self, *, timeout_s: float = 5.0) -> dict[str, Any]:
        """Discover operations exposed by the installed standalone process."""
        result = self.request("core.capabilities", {}, timeout_s=timeout_s).get(
            "result"
        )
        if not isinstance(result, dict):
            raise FastOFMCoreError(
                "CORE_PROTOCOL_ERROR", "Capability result is not an object"
            )
        return result

    def close(self) -> None:
        """Terminate only the process owned by this client."""
        process, self._process = self._process, None
        if process is None or process.poll() is not None:
            return
        process.terminate()
        try:
            process.wait(timeout=2.0)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=2.0)

    def __enter__(self) -> FastOFMCoreBlockingProcess:
        """Start the process for a synchronous context."""
        self.start()
        return self

    def __exit__(self, *_exc: object) -> None:
        """Close the owned process on every context exit."""
        self.close()
