#!/usr/bin/env python3
"""Validate and render safe Zodiac Bank AI/APT campaign simulations.

The command is deliberately offline and side-effect-free. It turns research-backed
threat records into defender-facing phases, expected telemetry, and containment
checkpoints. It never scans, executes commands, contacts a model, or targets a
real system.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
MODEL_PATH = ROOT / "training-config" / "threat-model.json"
RULES_PATH = ROOT / "detection-config" / "zodiac-bank-rules.json"
CURRICULUM_PATH = ROOT / "training-config" / "curriculum.json"


def load(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot load {path}: {exc}") from exc


def validate(model: dict[str, Any], ruleset: dict[str, Any], curriculum: dict[str, Any]) -> dict[str, Any]:
    errors: list[str] = []
    if model.get("schema_version") != 1 or ruleset.get("schema_version") != 1:
        errors.append("unsupported threat-model or detection-rules schema")
    if model.get("scope", {}).get("classification") != "synthetic-training-only":
        errors.append("threat model is not marked synthetic-training-only")
    if model.get("scope", {}).get("side_effects") is not False:
        errors.append("threat model must forbid side effects")

    sources = {source.get("id") for source in model.get("research_basis", [])}
    source_urls = [source.get("url", "") for source in model.get("research_basis", [])]
    if len(sources) != len(model.get("research_basis", [])):
        errors.append("research source IDs are not unique")
    if any(not url.startswith("https://") for url in source_urls):
        errors.append("every research source must use HTTPS")

    rule_ids = {rule.get("id") for rule in ruleset.get("rules", [])}
    if len(rule_ids) != len(ruleset.get("rules", [])):
        errors.append("detection rule IDs are not unique")
    threat_ids: set[str] = set()
    curriculum_ids = {stage.get("id") for stage in curriculum.get("stages", [])}
    for threat in model.get("threats", []):
        threat_id = threat.get("id")
        if not threat_id or threat_id in threat_ids:
            errors.append(f"duplicate or missing threat ID: {threat_id}")
        threat_ids.add(str(threat_id))
        if threat.get("stage_id") not in curriculum_ids:
            errors.append(f"{threat_id} references an unknown curriculum stage")
        if not threat.get("research_refs") or not set(threat["research_refs"]).issubset(sources):
            errors.append(f"{threat_id} has an unknown or missing research reference")
        if not threat.get("safe_simulation") or not threat.get("controls"):
            errors.append(f"{threat_id} needs a safe simulation and controls")
        missing_rules = set(threat.get("detection_rule_ids", [])) - rule_ids
        if missing_rules:
            errors.append(f"{threat_id} references missing detection rules: {sorted(missing_rules)}")

    campaigns = model.get("campaigns", [])
    campaign_ids = {campaign.get("id") for campaign in campaigns}
    if len(campaign_ids) != len(campaigns):
        errors.append("campaign IDs are not unique")
    for campaign in campaigns:
        phases = campaign.get("phases", [])
        if not phases or len(phases) > int(campaign.get("max_phases", 0)):
            errors.append(f"campaign {campaign.get('id')} exceeds its phase budget")
        if campaign.get("side_effects") is not False:
            errors.append(f"campaign {campaign.get('id')} must forbid side effects")
        for checkpoint in ("scope", "identity", "collection", "containment"):
            if checkpoint not in campaign.get("human_approval_checkpoints", []):
                errors.append(f"campaign {campaign.get('id')} is missing {checkpoint} approval checkpoint")
        seen_phases: set[int] = set()
        for phase in phases:
            number = phase.get("phase")
            if not isinstance(number, int) or number in seen_phases:
                errors.append(f"campaign {campaign.get('id')} has invalid phase numbering")
            seen_phases.add(number)
            if phase.get("threat_id") not in threat_ids:
                errors.append(f"campaign {campaign.get('id')} references unknown threat {phase.get('threat_id')}")
            if phase.get("safe") is not True:
                errors.append(f"campaign {campaign.get('id')} contains a non-safe phase")
            if not set(phase.get("expected_rule_ids", [])).issubset(rule_ids):
                errors.append(f"campaign {campaign.get('id')} references unknown detection rule")
    if "ai-apt-campaign" not in campaign_ids:
        errors.append("the required ai-apt-campaign is missing")
    if errors:
        raise ValueError("; ".join(errors))
    return {
        "sources": len(sources),
        "threats": len(threat_ids),
        "detection_rules": len(rule_ids),
        "campaigns": len(campaigns),
        "curriculum_stages": len(curriculum.get("stages", [])),
    }


def report(model: dict[str, Any], ruleset: dict[str, Any], campaign_id: str) -> dict[str, Any]:
    campaign = next((item for item in model["campaigns"] if item["id"] == campaign_id), None)
    if campaign is None:
        raise ValueError(f"unknown campaign: {campaign_id}")
    threats = {item["id"]: item for item in model["threats"]}
    rules = {item["id"]: item for item in ruleset["rules"]}
    phases: list[dict[str, Any]] = []
    for phase in campaign["phases"]:
        threat = threats[phase["threat_id"]]
        phases.append(
            {
                "phase": phase["phase"],
                "id": phase["id"],
                "event": phase["event"],
                "training_stage": threat["stage_id"],
                "threat": {"id": threat["id"], "name": threat["name"]},
                "telemetry": [
                    {
                        "rule_id": rule_id,
                        "title": rules[rule_id]["title"],
                        "severity": rules[rule_id]["severity"],
                        "response": rules[rule_id]["response"],
                    }
                    for rule_id in phase["expected_rule_ids"]
                ],
                "human_approval_required": phase["id"] in {"scope-and-baseline", "identity-escalation", "collection-pressure", "containment-and-recovery"},
                "side_effects": "forbidden",
                "status": "simulated-event",
            }
        )
    return {
        "schema_version": 1,
        "report_id": f"{campaign_id}-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}",
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "offline_safe": True,
        "classification": "synthetic-training-only",
        "campaign_id": campaign_id,
        "campaign_name": campaign["name"],
        "human_approval_checkpoints": campaign["human_approval_checkpoints"],
        "phases": phases,
        "operator_instruction": "Review evidence and detections only. Do not execute commands, contact external systems, or use real identities.",
    }


def args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--campaign", default="ai-apt-campaign")
    parser.add_argument("--validate-only", action="store_true")
    parser.add_argument("--format", choices=("text", "json"), default="text")
    parser.add_argument("--output", type=Path)
    return parser.parse_args()


def main() -> int:
    options = args()
    try:
        model = load(MODEL_PATH)
        ruleset = load(RULES_PATH)
        curriculum = load(CURRICULUM_PATH)
        summary = validate(model, ruleset, curriculum)
        result: dict[str, Any] = {"status": "pass", "validation": summary}
        if not options.validate_only:
            result["campaign"] = report(model, ruleset, options.campaign)
        if options.output:
            options.output.parent.mkdir(parents=True, exist_ok=True)
            temporary = options.output.with_suffix(options.output.suffix + ".tmp")
            temporary.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            temporary.replace(options.output)
        if options.format == "json":
            print(json.dumps(result, indent=2, sort_keys=True))
        else:
            print("[zodiac-bank-threats] PASS: research-backed threat model and detection rules are valid")
            print("[zodiac-bank-threats] coverage: " + json.dumps(summary, sort_keys=True))
            if not options.validate_only:
                print(f"[zodiac-bank-threats] campaign: {options.campaign} ({len(result['campaign']['phases'])} simulated phases)")
        return 0
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(f"[zodiac-bank-threats] ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
