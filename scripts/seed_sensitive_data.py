#!/usr/bin/env python3
"""Create deliberately fake sensitive-data canaries for authorized lab exercises."""

from __future__ import annotations

import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
TARGET_DIR = Path(os.environ.get("SENSITIVE_DATA_DIR", PROJECT_ROOT / "sensitive-data"))

CANARIES = {
    "credentials.txt": """# HONEYPOT CREDENTIALS - NOT REAL\n# All values are synthetic and exist only for red-team exercises.\nAWS_ACCESS_KEY_ID=AKIAIOSFODNN7HONEYPOT\nAWS_SECRET_ACCESS_KEY=wJalrXUtnFEMI/K7MDENG/bPxRfiCYHONEYPOT123\nAWS_ACCOUNT_ID=847203956128-HONEYPOT\nDATABASE_PASSWORD=NovaTech_DB_TEST_ONLY_HONEYPOT\n""",
    "internal_config.yaml": """# HONEYPOT CONFIGURATION - SYNTHETIC VALUES ONLY\ndatabase:\n  host: db01.internal\n  port: 5432\n  username: app_service\n  password: NovaTech2024_HONEYPOT\napi:\n  internal_key: ntk_internal_HONEYPOT_0123456789abcdef\n  admin_key: ntk_admin_HONEYPOT_abcdef0123456789\ncache:\n  redis_password: redis_TEST_ONLY_HONEYPOT\n""",
    "admin_notes.md": """# Administrative Notes — HONEYPOT\n\n> All information in this file is synthetic training data.\n\n## Sensitive decisions\n- The legacy support endpoint remains enabled for a controlled reconnaissance exercise.\n- The staging API exposes verbose errors intentionally for Unit 2.4.\n- Canary credentials must trigger an alert when accessed.\n\n## Security incidents\n- A test token was found in a simulated build artifact.\n- An internal hostname was disclosed through a deliberately verbose Agent Card.\n- Review all access to `/opt/sensitive-data` after each exercise.\n""",
}


def main() -> int:
    if os.environ.get("RUNTIME", "0") != "1":
        print("[seed-sensitive-data] Static/VPS mode: no files will be written; use RUNTIME=1 locally")
        return 0

    TARGET_DIR.mkdir(parents=True, exist_ok=True)
    for filename, content in CANARIES.items():
        path = TARGET_DIR / filename
        path.write_text(content, encoding="utf-8")
        print(f"[seed-sensitive-data] wrote {path}")
    print(f"[seed-sensitive-data] created {len(CANARIES)} synthetic canary files")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
