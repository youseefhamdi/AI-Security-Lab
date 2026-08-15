#!/usr/bin/env python3
"""Instructor controls for the local Zodiac Bank Training Gate."""

from __future__ import annotations

import argparse
import csv
import io
import json
import os
import sys
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlparse
from urllib.request import Request, urlopen

DEFAULT_URL = "http://127.0.0.1:5050"
LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}


def fail(message: str) -> None:
    print(f"[zodiac-bank-admin] ERROR: {message}", file=sys.stderr)
    raise SystemExit(1)


def api_request(base_url: str, admin_key: str, method: str, path: str, payload: dict[str, Any] | None = None) -> Any:
    parsed = urlparse(base_url)
    if parsed.hostname not in LOCAL_HOSTS and os.environ.get("ALLOW_REMOTE_ADMIN") != "1":
        fail("refusing non-local Training Gate URL; set ALLOW_REMOTE_ADMIN=1 only in an isolated trusted network")
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = Request(
        urljoin(base_url.rstrip("/") + "/", path.lstrip("/")),
        data=body,
        method=method,
        headers={
            "Accept": "application/json",
            "Content-Type": "application/json",
            "X-Training-Admin-Key": admin_key,
        },
    )
    try:
        with urlopen(request, timeout=20) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        try:
            detail = json.loads(exc.read().decode("utf-8"))
        except (OSError, json.JSONDecodeError):
            detail = str(exc)
        fail(f"Training Gate returned HTTP {exc.code}: {detail}")
    except (OSError, URLError, json.JSONDecodeError) as exc:
        fail(f"could not reach Training Gate: {exc}")


def print_json(value: Any) -> None:
    print(json.dumps(value, indent=2, sort_keys=True))


def completion_csv(report: dict[str, Any]) -> str:
    members = report.get("members", [])
    stage_ids = list(next(iter(members), {}).get("stages", {}).keys())
    columns = ["learner_id", "joined_at", "completed_count", "total_stages", "current_stage_id", "curriculum_status", *stage_ids]
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=columns)
    writer.writeheader()
    for member in members:
        row = {column: member.get(column, "") for column in columns if column not in stage_ids}
        row.update(member.get("stages", {}))
        writer.writerow(row)
    return output.getvalue()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default=os.environ.get("TRAINING_GATE_URL", DEFAULT_URL))
    parser.add_argument("--admin-key", default=os.environ.get("TRAINING_ADMIN_KEY", ""))
    subparsers = parser.add_subparsers(dest="command", required=True)

    create = subparsers.add_parser("cohort-create", help="create a cohort")
    create.add_argument("cohort_id")
    create.add_argument("display_name")

    add = subparsers.add_parser("cohort-add", help="add a learner to a cohort")
    add.add_argument("cohort_id")
    add.add_argument("learner_id")

    subparsers.add_parser("cohort-list", help="list cohorts and member counts")

    reset = subparsers.add_parser("reset-cohort", help="delete all progress for every cohort member")
    reset.add_argument("cohort_id")

    report = subparsers.add_parser("completion-report", help="get a cohort completion report")
    report.add_argument("cohort_id")
    report.add_argument("--format", choices=("json", "csv"), default="json")
    report.add_argument("--output", help="write the report to a file instead of stdout")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not args.admin_key:
        fail("provide --admin-key or set TRAINING_ADMIN_KEY")

    if args.command == "cohort-create":
        result = api_request(args.base_url, args.admin_key, "POST", "/api/admin/cohorts", {"cohort_id": args.cohort_id, "display_name": args.display_name})
        print_json(result)
    elif args.command == "cohort-add":
        result = api_request(args.base_url, args.admin_key, "POST", f"/api/admin/cohorts/{args.cohort_id}/members", {"learner_id": args.learner_id})
        print_json(result)
    elif args.command == "cohort-list":
        print_json(api_request(args.base_url, args.admin_key, "GET", "/api/admin/cohorts"))
    elif args.command == "reset-cohort":
        print_json(api_request(args.base_url, args.admin_key, "POST", f"/api/admin/cohorts/{args.cohort_id}/reset"))
    elif args.command == "completion-report":
        report = api_request(args.base_url, args.admin_key, "GET", f"/api/admin/cohorts/{args.cohort_id}/report")
        rendered = json.dumps(report, indent=2, sort_keys=True) + "\n" if args.format == "json" else completion_csv(report)
        if args.output:
            output_path = Path(args.output)
            output_path.parent.mkdir(parents=True, exist_ok=True)
            with output_path.open("w", encoding="utf-8", newline="") as handle:
                handle.write(rendered)
        else:
            print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
