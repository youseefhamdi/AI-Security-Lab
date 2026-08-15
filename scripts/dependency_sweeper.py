#!/usr/bin/env python3
"""Bounded, persistent Dependency Sweeper workflow for the local lab.

The workflow consumes normalized dependency findings and creates review
proposals. It never installs packages, edits manifests, opens pull requests,
or merges changes. State is persisted in SQLite so retries and interrupted
runs remain bounded and auditable.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

WORKFLOW = "dependency-sweeper"
SCHEMA_VERSION = 1
DEFAULT_INPUT = Path("loop-config/dependency-findings.json")
DEFAULT_STATE = Path("logs/dependency-sweeper.sqlite3")
DEFAULT_OUTPUT = Path("logs/dependency-sweeper-summary.json")
ALLOWED_SEVERITIES = {"critical", "high", "medium", "low", "info"}
REQUIRED_FIELDS = ("finding_id", "package", "ecosystem", "manifest", "current_version", "target_version", "severity")
MAX_INPUT_BYTES = 1_000_000
MAX_FINDINGS = 100
MAX_FIELD_LENGTH = 512
MAX_ADVISORY_LENGTH = 2_000
MAX_OUTPUT_CHARS = 20_000


class RetryableFindingError(Exception):
    """A worker failure that may be retried within the configured budget."""


class PermanentFindingError(Exception):
    """A finding that cannot produce a safe proposal."""


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def log(message: str) -> None:
    print(f"[dependency-sweeper] {message}", flush=True)


def fail(message: str) -> None:
    print(f"[dependency-sweeper] ERROR: {message}", file=sys.stderr, flush=True)
    raise SystemExit(1)


def json_text(value: Any, limit: int | None = None) -> str:
    text = json.dumps(value, sort_keys=True, separators=(",", ":"))
    return text[:limit] if limit is not None else text


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT, help="JSON dependency findings file")
    parser.add_argument("--state", type=Path, default=DEFAULT_STATE, help="SQLite state database")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT, help="JSON summary output")
    parser.add_argument("--run-id", default="", help="Optional stable run identifier")
    parser.add_argument("--max-retries", type=int, default=int(os.environ.get("LOOP_MAX_RETRIES", "3")))
    parser.add_argument("--max-items", type=int, default=int(os.environ.get("LOOP_MAX_ITEMS", str(MAX_FINDINGS))))
    parser.add_argument("--lease-seconds", type=int, default=int(os.environ.get("LOOP_LEASE_SECONDS", "300")))
    parser.add_argument("--retry-delay", type=float, default=float(os.environ.get("LOOP_RETRY_DELAY", "0")))
    parser.add_argument("--max-input-bytes", type=int, default=MAX_INPUT_BYTES)
    return parser.parse_args()


def validate_limits(args: argparse.Namespace) -> None:
    if args.max_retries < 0 or args.max_retries > 10:
        fail("--max-retries must be between 0 and 10")
    if args.max_items <= 0 or args.max_items > MAX_FINDINGS:
        fail(f"--max-items must be between 1 and {MAX_FINDINGS}")
    if args.lease_seconds <= 0 or args.lease_seconds > 86_400:
        fail("--lease-seconds must be between 1 and 86400")
    if args.retry_delay < 0 or args.retry_delay > 300:
        fail("--retry-delay must be between 0 and 300 seconds")
    if args.max_input_bytes <= 0 or args.max_input_bytes > MAX_INPUT_BYTES:
        fail(f"--max-input-bytes must be between 1 and {MAX_INPUT_BYTES}")


def bounded_string(value: Any, field: str, limit: int = MAX_FIELD_LENGTH) -> str:
    if not isinstance(value, str) or not value.strip():
        raise PermanentFindingError(f"{field} must be a non-empty string")
    value = value.strip()
    if len(value) > limit:
        raise PermanentFindingError(f"{field} exceeds the {limit}-character limit")
    return value


def normalize_finding(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise PermanentFindingError("each finding must be an object")
    missing = [field for field in REQUIRED_FIELDS if field not in raw]
    if missing:
        raise PermanentFindingError(f"missing required fields: {', '.join(missing)}")

    severity = bounded_string(raw["severity"], "severity").lower()
    if severity not in ALLOWED_SEVERITIES:
        raise PermanentFindingError(f"severity must be one of: {', '.join(sorted(ALLOWED_SEVERITIES))}")

    advisory = raw.get("advisory", "No advisory text supplied.")
    if not isinstance(advisory, str):
        raise PermanentFindingError("advisory must be a string")
    advisory = advisory.strip()
    if len(advisory) > MAX_ADVISORY_LENGTH:
        raise PermanentFindingError(f"advisory exceeds the {MAX_ADVISORY_LENGTH}-character limit")

    simulated_failures = raw.get("simulated_transient_failures", 0)
    if not isinstance(simulated_failures, int) or simulated_failures < 0 or simulated_failures > 10:
        raise PermanentFindingError("simulated_transient_failures must be an integer between 0 and 10")

    return {
        "finding_id": bounded_string(raw["finding_id"], "finding_id"),
        "package": bounded_string(raw["package"], "package"),
        "ecosystem": bounded_string(raw["ecosystem"], "ecosystem").lower(),
        "manifest": bounded_string(raw["manifest"], "manifest"),
        "current_version": bounded_string(raw["current_version"], "current_version"),
        "target_version": bounded_string(raw["target_version"], "target_version"),
        "severity": severity,
        "advisory": advisory,
        "source": bounded_string(raw.get("source", "local-finding"), "source"),
        "simulated_transient_failures": simulated_failures,
    }


def load_findings(path: Path, max_bytes: int, max_items: int) -> list[dict[str, Any]]:
    try:
        if path.stat().st_size > max_bytes:
            raise PermanentFindingError(f"input exceeds the {max_bytes}-byte limit")
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PermanentFindingError(f"could not read JSON input: {exc}") from exc

    if not isinstance(document, dict) or document.get("schema_version") != SCHEMA_VERSION:
        raise PermanentFindingError(f"input schema_version must be {SCHEMA_VERSION}")
    raw_findings = document.get("findings")
    if not isinstance(raw_findings, list):
        raise PermanentFindingError("input findings must be an array")
    if len(raw_findings) > max_items:
        raise PermanentFindingError(f"input contains {len(raw_findings)} findings; max is {max_items}")

    findings: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for raw in raw_findings:
        finding = normalize_finding(raw)
        if finding["finding_id"] in seen_ids:
            raise PermanentFindingError(f"duplicate finding_id: {finding['finding_id']}")
        seen_ids.add(finding["finding_id"])
        findings.append(finding)
    return findings


def fingerprint(finding: dict[str, Any]) -> str:
    payload = json.dumps(finding, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA busy_timeout = 5000")
    connection.execute("PRAGMA foreign_keys = ON")
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS runs (
            run_id TEXT PRIMARY KEY,
            workflow TEXT NOT NULL,
            status TEXT NOT NULL,
            started_at TEXT NOT NULL,
            finished_at TEXT,
            config_json TEXT NOT NULL,
            summary_json TEXT
        );
        CREATE TABLE IF NOT EXISTS items (
            item_key TEXT PRIMARY KEY,
            finding_id TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            status TEXT NOT NULL,
            attempts INTEGER NOT NULL DEFAULT 0,
            next_attempt_at TEXT NOT NULL,
            lease_until TEXT,
            last_error TEXT,
            proposal_json TEXT,
            last_run_id TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS items_ready_idx ON items(status, next_attempt_at);
        CREATE TABLE IF NOT EXISTS events (
            event_id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id TEXT NOT NULL,
            item_key TEXT,
            event TEXT NOT NULL,
            details_json TEXT NOT NULL,
            occurred_at TEXT NOT NULL,
            FOREIGN KEY(run_id) REFERENCES runs(run_id)
        );
        """
    )
    connection.commit()
    return connection


def event(connection: sqlite3.Connection, run_id: str, name: str, item_key: str | None = None, **details: Any) -> None:
    connection.execute(
        "INSERT INTO events(run_id, item_key, event, details_json, occurred_at) VALUES (?, ?, ?, ?, ?)",
        (run_id, item_key, name, json_text(details), now()),
    )
    connection.commit()


def upsert_findings(connection: sqlite3.Connection, run_id: str, findings: list[dict[str, Any]]) -> None:
    timestamp = now()
    for finding in findings:
        item_key = fingerprint(finding)
        connection.execute(
            """
            INSERT INTO items(item_key, finding_id, payload_json, status, attempts, next_attempt_at, created_at, updated_at)
            VALUES (?, ?, ?, 'pending', 0, ?, ?, ?)
            ON CONFLICT(item_key) DO UPDATE SET payload_json=excluded.payload_json, updated_at=excluded.updated_at
            """,
            (item_key, finding["finding_id"], json_text(finding), timestamp, timestamp, timestamp),
        )
        event(connection, run_id, "finding_loaded", item_key, finding_id=finding["finding_id"])


def recover_expired_items(connection: sqlite3.Connection, run_id: str, max_attempts: int) -> None:
    timestamp = now()
    rows = connection.execute(
        "SELECT item_key, attempts FROM items WHERE status = 'processing' AND lease_until <= ?",
        (timestamp,),
    ).fetchall()
    for row in rows:
        if row["attempts"] < max_attempts:
            connection.execute(
                "UPDATE items SET status='retry_wait', next_attempt_at=?, lease_until=NULL, last_error=?, updated_at=? WHERE item_key=?",
                (timestamp, "worker lease expired; recovered for retry", timestamp, row["item_key"]),
            )
            event(connection, run_id, "item_recovered", row["item_key"], attempts=row["attempts"])
        else:
            connection.execute(
                "UPDATE items SET status='failed', lease_until=NULL, last_error=?, updated_at=? WHERE item_key=?",
                ("worker lease expired after retry budget was exhausted", timestamp, row["item_key"]),
            )
            event(connection, run_id, "item_failed", row["item_key"], reason="lease_expired")
    connection.commit()


def claim_item(connection: sqlite3.Connection, run_id: str, lease_seconds: int, max_attempts: int) -> sqlite3.Row | None:
    timestamp = now()
    lease_until = datetime.fromtimestamp(time.time() + lease_seconds, timezone.utc).isoformat(timespec="seconds")
    connection.execute("BEGIN IMMEDIATE")
    row = connection.execute(
        """
        SELECT item_key, finding_id, payload_json, attempts, attempts + 1 AS attempt
        FROM items
        WHERE status IN ('pending', 'retry_wait')
          AND next_attempt_at <= ?
          AND attempts < ?
        ORDER BY created_at, finding_id
        LIMIT 1
        """,
        (timestamp, max_attempts),
    ).fetchone()
    if row is None:
        connection.commit()
        return None

    next_attempt = row["attempts"] + 1
    connection.execute(
        """
        UPDATE items
        SET status='processing', attempts=?, lease_until=?, last_run_id=?, updated_at=?
        WHERE item_key=?
        """,
        (next_attempt, lease_until, run_id, timestamp, row["item_key"]),
    )
    connection.commit()
    event(connection, run_id, "item_claimed", row["item_key"], attempt=next_attempt, lease_until=lease_until)
    return row


def evaluate_finding(finding: dict[str, Any], attempt: int) -> dict[str, Any]:
    """Produce a proposal without changing files or invoking package tooling."""
    if attempt <= finding["simulated_transient_failures"]:
        raise RetryableFindingError("synthetic transient worker failure")
    if finding["current_version"] == finding["target_version"]:
        raise PermanentFindingError("current_version and target_version are identical")

    return {
        "finding_id": finding["finding_id"],
        "package": finding["package"],
        "ecosystem": finding["ecosystem"],
        "manifest": finding["manifest"],
        "from_version": finding["current_version"],
        "to_version": finding["target_version"],
        "severity": finding["severity"],
        "advisory": finding["advisory"],
        "recommended_action": "review_and_update",
        "side_effects": [],
        "requires_human_approval": True,
        "auto_merge": False,
    }


def process_item(
    connection: sqlite3.Connection,
    run_id: str,
    row: sqlite3.Row,
    max_attempts: int,
    retry_delay: float,
) -> None:
    item_key = row["item_key"]
    attempt = int(row["attempt"])
    finding = json.loads(row["payload_json"])
    timestamp = now()
    try:
        proposal = evaluate_finding(finding, attempt)
    except RetryableFindingError as exc:
        if attempt < max_attempts:
            delay = min(retry_delay * (2 ** max(attempt - 1, 0)), 300.0)
            next_attempt_at = datetime.fromtimestamp(time.time() + delay, timezone.utc).isoformat(timespec="seconds")
            connection.execute(
                "UPDATE items SET status='retry_wait', next_attempt_at=?, lease_until=NULL, last_error=?, updated_at=? WHERE item_key=?",
                (next_attempt_at, str(exc)[:MAX_FIELD_LENGTH], timestamp, item_key),
            )
            connection.commit()
            event(connection, run_id, "item_retry_scheduled", item_key, attempt=attempt, delay_seconds=delay, error=str(exc))
            return
        connection.execute(
            "UPDATE items SET status='failed', lease_until=NULL, last_error=?, updated_at=? WHERE item_key=?",
            (f"retry budget exhausted: {exc}"[:MAX_FIELD_LENGTH], timestamp, item_key),
        )
        connection.commit()
        event(connection, run_id, "item_failed", item_key, attempt=attempt, retryable=True, error=str(exc))
        return
    except PermanentFindingError as exc:
        connection.execute(
            "UPDATE items SET status='failed', lease_until=NULL, last_error=?, updated_at=? WHERE item_key=?",
            (str(exc)[:MAX_FIELD_LENGTH], timestamp, item_key),
        )
        connection.commit()
        event(connection, run_id, "item_failed", item_key, attempt=attempt, retryable=False, error=str(exc))
        return

    connection.execute(
        "UPDATE items SET status='proposed', lease_until=NULL, last_error=NULL, proposal_json=?, updated_at=? WHERE item_key=?",
        (json_text(proposal), timestamp, item_key),
    )
    connection.commit()
    event(connection, run_id, "proposal_created", item_key, attempt=attempt, finding_id=finding["finding_id"])


def summarize(connection: sqlite3.Connection, item_keys: list[str]) -> dict[str, Any]:
    if not item_keys:
        return {"items": 0, "counts": {}, "proposals": [], "failures": [], "waiting": []}
    placeholders = ",".join("?" for _ in item_keys)
    rows = connection.execute(
        f"SELECT item_key, finding_id, status, attempts, last_error, proposal_json, next_attempt_at FROM items WHERE item_key IN ({placeholders})",
        item_keys,
    ).fetchall()
    counts: dict[str, int] = {}
    proposals: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    waiting: list[dict[str, Any]] = []
    for row in rows:
        counts[row["status"]] = counts.get(row["status"], 0) + 1
        if row["status"] == "proposed" and row["proposal_json"]:
            proposals.append(json.loads(row["proposal_json"]))
        elif row["status"] == "failed":
            failures.append({"finding_id": row["finding_id"], "attempts": row["attempts"], "error": row["last_error"]})
        elif row["status"] == "retry_wait":
            waiting.append({"finding_id": row["finding_id"], "attempts": row["attempts"], "next_attempt_at": row["next_attempt_at"]})
    return {"items": len(rows), "counts": counts, "proposals": proposals, "failures": failures, "waiting": waiting}


def write_summary(path: Path, summary: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    output = dict(summary)
    output["proposals"] = [
        {**proposal, "advisory": str(proposal.get("advisory", ""))[:500]}
        for proposal in summary.get("proposals", [])
    ]
    output["failures"] = summary.get("failures", [])[:50]
    output["waiting"] = summary.get("waiting", [])[:50]
    output["output_truncated"] = False
    rendered = json.dumps(output, indent=2, sort_keys=True)
    if len(rendered) > MAX_OUTPUT_CHARS:
        output["proposals"] = []
        output["failures"] = []
        output["waiting"] = []
        output["output_truncated"] = True
        output["proposals_total"] = len(summary.get("proposals", []))
        output["failures_total"] = len(summary.get("failures", []))
        output["waiting_total"] = len(summary.get("waiting", []))
        rendered = json.dumps(output, indent=2, sort_keys=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    temporary.write_text(rendered + "\n", encoding="utf-8")
    temporary.replace(path)


def run(args: argparse.Namespace) -> int:
    validate_limits(args)
    findings = load_findings(args.input, args.max_input_bytes, args.max_items)
    run_id = args.run_id or f"dep-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}-{uuid.uuid4().hex[:8]}"
    max_attempts = args.max_retries + 1
    connection = connect(args.state)
    item_keys: list[str] = [fingerprint(finding) for finding in findings]
    config = {
        "max_retries": args.max_retries,
        "max_attempts": max_attempts,
        "max_items": args.max_items,
        "lease_seconds": args.lease_seconds,
        "retry_delay": args.retry_delay,
    }
    try:
        connection.execute(
            "INSERT INTO runs(run_id, workflow, status, started_at, config_json) VALUES (?, ?, 'running', ?, ?)",
            (run_id, WORKFLOW, now(), json_text(config)),
        )
        connection.commit()
        upsert_findings(connection, run_id, findings)
        recover_expired_items(connection, run_id, max_attempts)

        while True:
            row = claim_item(connection, run_id, args.lease_seconds, max_attempts)
            if row is None:
                break
            process_item(connection, run_id, row, max_attempts, args.retry_delay)

        summary = summarize(connection, item_keys)
        summary.update({"run_id": run_id, "workflow": WORKFLOW, "config": config, "generated_at": now()})
        if summary["failures"]:
            run_status = "completed_with_failures"
        elif summary["waiting"]:
            run_status = "waiting_for_retry"
        else:
            run_status = "completed"
        connection.execute(
            "UPDATE runs SET status=?, finished_at=?, summary_json=? WHERE run_id=?",
            (run_status, now(), json_text(summary), run_id),
        )
        connection.commit()
        write_summary(args.output, summary)
        log(f"run={run_id} status={run_status} counts={summary['counts']}")
        log(f"persistent_state={args.state} summary={args.output}")
        return 0 if not summary["failures"] else 2
    finally:
        connection.close()


def main() -> int:
    if os.environ.get("RUNTIME", "0") != "1":
        log("Static/VPS mode: no dependency scan, state write, or workflow execution will run")
        log("Local execution: RUNTIME=1 python3 scripts/dependency_sweeper.py")
        return 0
    args = parse_args()
    try:
        return run(args)
    except (OSError, PermanentFindingError, sqlite3.Error) as exc:
        fail(str(exc))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
