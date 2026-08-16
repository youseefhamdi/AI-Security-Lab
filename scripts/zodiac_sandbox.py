"""Safe local tool execution primitives for the synthetic Zodiac Bank lab.

The sandbox intentionally executes registered Python handlers only. It never
interprets shell strings, opens sockets, reads the host filesystem, or invokes
an unregistered command. This gives learners realistic capability-boundary and
argument-injection exercises without turning the lab into a command runner.
"""

from __future__ import annotations

import json
import posixpath
import threading
import time
from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Callable


class SandboxViolation(PermissionError):
    """Raised when a synthetic tool violates the sandbox policy."""


@dataclass(frozen=True)
class SandboxPolicy:
    allowed_tools: frozenset[str] = frozenset({"memory"})
    max_calls: int = 16
    max_argument_bytes: int = 4096
    max_output_bytes: int = 8192
    timeout_seconds: float = 1.0
    network_allowed: bool = False
    filesystem_mode: str = "fixture-only"


@dataclass(frozen=True)
class SandboxResult:
    tool: str
    status: str
    output: Any
    elapsed_ms: int
    side_effects: tuple[str, ...] = ()
    network_allowed: bool = False
    filesystem_mode: str = "fixture-only"


class FixtureFilesystem:
    """In-memory normalized paths; no host filesystem access is possible."""

    def __init__(self, files: dict[str, str] | None = None) -> None:
        self._files = {self._normalize(path): str(value) for path, value in (files or {}).items()}

    @staticmethod
    def _normalize(path: str) -> str:
        if not isinstance(path, str) or len(path) > 512 or "\x00" in path:
            raise SandboxViolation("invalid fixture path")
        normalized = posixpath.normpath("/" + path.lstrip("/"))
        if normalized == "/" or normalized.startswith("/../") or normalized == "/..":
            raise SandboxViolation("fixture path escapes the sandbox")
        return normalized

    def read(self, path: str) -> dict[str, Any]:
        normalized = self._normalize(path)
        if normalized not in self._files:
            raise SandboxViolation("fixture file is not present")
        return {"path": normalized, "content": self._files[normalized], "synthetic": True, "raw_host_file": False}


class LocalToolSandbox:
    """Run only explicitly registered pure handlers under a bounded policy."""

    def __init__(self, policy: SandboxPolicy | None = None) -> None:
        self.policy = policy or SandboxPolicy()
        self._handlers: dict[str, Callable[[dict[str, Any]], Any]] = {}
        self._calls = 0
        self._lock = threading.RLock()

    def register(self, name: str, handler: Callable[[dict[str, Any]], Any]) -> None:
        if name not in self.policy.allowed_tools:
            raise SandboxViolation("cannot register a tool outside the allowlist")
        if not callable(handler):
            raise SandboxViolation("sandbox handler must be callable")
        self._handlers[name] = handler

    def execute(self, name: str, arguments: dict[str, Any]) -> SandboxResult:
        started = time.monotonic()
        with self._lock:
            if name not in self.policy.allowed_tools:
                raise SandboxViolation("tool is not allowlisted")
            if name not in self._handlers:
                raise SandboxViolation("tool has no registered safe handler")
            if self._calls >= self.policy.max_calls:
                raise SandboxViolation("sandbox call budget exhausted")
            if not isinstance(arguments, dict):
                raise SandboxViolation("sandbox arguments must be an object")
            encoded = json.dumps(arguments, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
            if len(encoded.encode("utf-8")) > self.policy.max_argument_bytes:
                raise SandboxViolation("sandbox argument budget exceeded")
            self._calls += 1
            handler = self._handlers[name]
        try:
            output = handler(deepcopy(arguments))
            encoded_output = json.dumps(output, sort_keys=True, ensure_ascii=False)
            if len(encoded_output.encode("utf-8")) > self.policy.max_output_bytes:
                raise SandboxViolation("sandbox output budget exceeded")
            elapsed_ms = int((time.monotonic() - started) * 1000)
            if elapsed_ms > int(self.policy.timeout_seconds * 1000):
                raise SandboxViolation("sandbox handler exceeded its time budget")
            return SandboxResult(name, "completed", deepcopy(output), elapsed_ms, (), self.policy.network_allowed, self.policy.filesystem_mode)
        except SandboxViolation:
            raise
        except Exception as exc:
            raise SandboxViolation(f"sandbox handler rejected the call: {type(exc).__name__}") from exc

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {"calls": self._calls, "max_calls": self.policy.max_calls, "allowed_tools": sorted(self.policy.allowed_tools), "network_allowed": self.policy.network_allowed, "filesystem_mode": self.policy.filesystem_mode, "registered_tools": sorted(self._handlers), "side_effects": []}


def memory_handler(arguments: dict[str, Any]) -> dict[str, Any]:
    """A tiny synthetic memory fixture used by secure MCP tests."""
    operation = arguments.get("operation")
    if operation not in {"read", "write"}:
        raise SandboxViolation("memory operation must be read or write")
    key = arguments.get("key", "")
    if not isinstance(key, str) or not key or len(key) > 128 or key.startswith("/"):
        raise SandboxViolation("memory key is invalid")
    return {"operation": operation, "key": key, "value_present": operation == "write" and "value" in arguments, "synthetic": True, "side_effects": []}
