"""Dependency-free scenario primitives shared by the challenge service and evaluator.

Schema v2: steps declare *evidence value types* (e.g. ``http-method``) instead of
literal expected values. The service derives the per-run expected values with an
HMAC bound to the flag secret, learner ID, scenario ID, step ID, and the run
nonce. The repository therefore never contains a literal answer, and every
learner/run sees a different value set. Steps after the first additionally
require a chained ``proof`` value produced by the previous accepted step.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 2
SAFE_EVENT_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{2,63}$")
MAX_ATTEMPTS_PER_STEP = 20
PROOF_KEY = "proof"

# Bounded value vocabularies. The engine selects a per-run value from one of
# these lists using the HMAC digest; the observation text guides the learner.
VOCABULARY: dict[str, list[str]] = {
    "http-method": ["GET", "HEAD", "POST", "PUT", "OPTIONS", "DELETE"],
    "scope-route": ["/public/scope", "/api/models", "/api/support/chat", "/api/rag/query", "/api/agent/dispatch", "/api/memory/search"],
    "admin-route": ["/api/admin/approval", "/api/admin/cohorts", "/api/admin/cohorts/report", "/api/admin/cohorts/reset"],
    "ci-route": ["/api/ci/artifacts", "/api/logs/search", "/api/campaign/execute", "/api/scenarios", "/api/stages"],
    "header-name": ["X-Zodiac-Bank-Training-Flag", "X-Zodiac-Bank-Scope", "X-Zodiac-Bank-Provider", "X-Graph-Context-Key", "X-Training-Learner-Token", "X-Forwarded-User"],
    "entity-id": ["ZB-CUS-001", "ZB-CUS-002", "ZB-CUS-004", "ZB-CASE-002", "ZB-CASE-006", "ZB-EMP-001"],
    "case-id": ["ZB-CASE-002", "ZB-CASE-003", "ZB-CASE-006", "ZB-CASE-009"],
    "worker-id": ["risk-engine", "support-router", "fraud-analyst", "approval-worker", "credit-officer"],
    "trust-class": ["synthetic-training-only", "retrieved-untrusted-data", "evidence-not-authority", "unverified", "quarantined", "denied"],
    "loopback-addr": ["127.0.0.1", "127.0.0.2", "127.0.0.3", "localhost"],
    "mode": ["normal", "stealth", "verbose", "bounded", "diagnostic"],
    "boolean": ["true", "false"],
    "result": ["permitted", "denied", "suspicious", "blocked", "deferred", "withheld"],
    "decision": ["deny-and-quarantine", "stop-and-reapprove", "deny-and-alert", "hold", "allow-data-only", "stop-and-review"],
    "control": ["approval-required", "output-validation", "tenant-filter", "provenance", "rollback", "manifest-pinning", "audience-binding", "least-privilege", "canonicalization", "circuit-breaker"],
    "detection-rule": ["ZB-AI-001", "ZB-AI-002", "ZB-AI-003", "ZB-AI-004", "ZB-AI-005", "ZB-AI-006", "ZB-AI-007", "ZB-AI-008", "ZB-AI-009", "ZB-AI-010"],
    "tool-name": ["add_numbers", "send_notification", "search_documents", "read_file", "execute_sql", "list_directory"],
    "package-name": ["zodiac-risk-parser", "fastparserx", "dataframe-utils", "auth-helper", "zodiac-notify"],
    "publisher": ["synthetic-registry", "synthetic-mirror", "untrusted-mirror", "approved-registry"],
    "digest-state": ["pinned", "changed", "mismatch", "approved", "unapproved"],
    "occlusion": ["visible", "hidden", "parsed-only", "split", "obfuscated"],
    "side-effect": ["none", "denied", "disabled", "blocked"],
    "marker": ["baseline", "anomaly", "canary", "benign", "declared", "over-limit"],
    "chained-proof": ["chained-proof"],
}



TECHNICAL_TRACKS: tuple[dict[str, Any], ...] = (
    {
        "id": "certificate", "keywords": ("certificate", "pfx", "credential-access"),
        "label": "certificate and identity telemetry",
        "artifact": "certificate discovery, copy, recovery, and authentication records",
        "baseline": "Establish which synthetic certificate artifacts and identity events exist before the controlled test.",
        "test": "Correlate certificate handling and authentication signals while keeping key material redacted.",
        "close": "Quarantine the certificate path, rotate the synthetic identity, and verify the authorization boundary.",
    },
    {
        "id": "mail", "keywords": ("email", "exchange", "mail", "journaling", "transport-rule"),
        "label": "mail-flow and assistant telemetry",
        "artifact": "message, assistant, PowerShell, and delivery-trace records",
        "baseline": "Establish the normal mailbox, assistant, and delivery path before introducing the message fixture.",
        "test": "Correlate untrusted message content with file access, reply, journaling, or transport-rule activity.",
        "close": "Disable the synthetic mail-flow change, preserve the message timeline, and require re-approval.",
    },
    {
        "id": "document", "keywords": ("resume", "document", "ocr", "multimodal", "image", "screening"),
        "label": "document and multimodal pipeline",
        "artifact": "source checksum, parser output, hidden-content marker, and reviewer decision",
        "baseline": "Record the visible document representation, parser provenance, and clean classifier decision.",
        "test": "Compare visible content with instruction-like OCR or parsed content and identify the trust transition.",
        "close": "Quarantine the fixture, discard parser instructions, and require an independent review record.",
    },
    {
        "id": "delegation", "keywords": ("rbcd", "delegation", "active-directory", "machine-account"),
        "label": "delegation and privileged-access telemetry",
        "artifact": "target discovery, account creation, delegation configuration, and share-access records",
        "baseline": "Record the synthetic target and existing delegation relationships before changing one authorization edge.",
        "test": "Correlate machine-account or delegated-agent creation with the resulting privileged access signal.",
        "close": "Remove the synthetic delegation edge, delete or rotate the identity, and verify the target allow-list.",
    },
    {
        "id": "identity", "keywords": ("oidc", "jwt", "token", "kerberos", "dcsync", "shadow"),
        "label": "identity and authentication telemetry",
        "artifact": "issuer, audience, identity, ticket, directory, and approval fields",
        "baseline": "Record the approved issuer, audience, role, and synthetic identity before testing the claim.",
        "test": "Correlate the identity claim with directory, ticket, or privileged-operation telemetry without exposing secrets.",
        "close": "Invalidate the synthetic identity, rotate tokens, and verify least privilege and audience binding.",
    },
    {
        "id": "prompt", "keywords": ("prompt", "injection", "shell", "unicode", "multilingual", "tool-call"),
        "label": "instruction-boundary and tool authorization",
        "artifact": "baseline response, untrusted content marker, typed output, and approval decision",
        "baseline": "Capture the clean assistant response and the tools available to the approved role.",
        "test": "Trace where untrusted text attempts to become an instruction, structured output, or tool invocation.",
        "close": "Keep content data-only, validate the output schema, and require explicit approval for consequential actions.",
    },
    {
        "id": "retrieval", "keywords": ("rag", "vector", "embedding", "retrieval", "cache", "citation", "policy"),
        "label": "retrieval provenance and tenant isolation",
        "artifact": "query, tenant, source, ranking, cache, citation, and answer lineage",
        "baseline": "Record the authoritative source, tenant scope, and citation chain for the clean query.",
        "test": "Compare the retrieved source and cache state after the poisoned, stale, or cross-tenant fixture is introduced.",
        "close": "Quarantine the source or cache entry, restore tenant filtering, and verify provenance end to end.",
    },
    {
        "id": "protocol", "keywords": ("mcp", "a2a", "agent-card", "webhook", "tool", "oauth", "capability"),
        "label": "agent protocol and tool integrity",
        "artifact": "agent card, manifest, audience, nonce, tool schema, and delegation receipt",
        "baseline": "Inventory the signed agent or tool manifest and the allow-listed capabilities for the caller.",
        "test": "Correlate the requested capability with identity, audience, nonce, manifest, and tool-result integrity.",
        "close": "Reject the drift or replay, pin the manifest, and require re-approval before the tool chain resumes.",
    },
    {
        "id": "memory", "keywords": ("memory", "retention", "privacy", "summary", "context", "tombstone", "feedback"),
        "label": "memory provenance and scope isolation",
        "artifact": "write authorization, source lineage, tenant scope, retention, and derived recall",
        "baseline": "Record the clean memory scope, source lineage, and retention state for the synthetic user or run.",
        "test": "Compare the requested scope with the persisted summary, cache, or derived memory after the fixture is introduced.",
        "close": "Rollback or tombstone the poisoned record, verify deletion, and preserve tenant isolation evidence.",
    },
    {
        "id": "supply-chain", "keywords": ("supply-chain", "dependency", "package", "model", "container", "artifact", "dataset", "skill", "publisher"),
        "label": "artifact provenance and promotion control",
        "artifact": "publisher, manifest, digest, workspace, review, and runtime-load records",
        "baseline": "Record the approved publisher, digest, manifest, workspace, and review state before promotion.",
        "test": "Correlate the changed package, model, dataset, or skill with manifest and runtime evidence.",
        "close": "Quarantine the mismatch, pin the digest, and require independent review before promotion.",
    },
    {
        "id": "detection", "keywords": ("detector", "alert", "evasion", "velocity", "threshold", "suppression", "baseline"),
        "label": "detection baseline and evasion telemetry",
        "artifact": "normalization, behavior baseline, alert state, risk threshold, and circuit-breaker records",
        "baseline": "Record the normal event shape, detector version, threshold, and alert path before the evasion fixture.",
        "test": "Compare normalized and distributed behavior against the baseline and identify the blind spot.",
        "close": "Restore the detector or circuit breaker, preserve the evasion delta, and verify the new control catches it.",
    },
    {
        "id": "incident-response", "keywords": ("apt", "campaign", "mule", "payment", "recovery", "rollback", "containment", "residual-risk"),
        "label": "campaign correlation and recovery verification",
        "artifact": "identity, timeline, tool, funds-flow, containment, and recovery records",
        "baseline": "Build the clean campaign timeline and identify the synthetic identities, tools, and approval checkpoints.",
        "test": "Correlate the multi-stage activity without treating agent consensus or a single receipt as proof.",
        "close": "Contain the campaign, rotate identities, verify rollback, and reconcile residual risk before closure.",
    },
)


def _technical_track_for_spec(spec: dict[str, Any]) -> dict[str, Any]:
    haystack = " ".join([
        str(spec.get("title", "")),
        str(spec.get("objective", "")),
        *[str(value) for value in spec.get("threat_tags", [])],
        *[str(value) for value in spec.get("concepts", [])],
    ]).lower()
    for track in TECHNICAL_TRACKS:
        if any(keyword in haystack for keyword in track["keywords"]):
            return track
    stage_defaults = {
        "L00-foundation": "detection",
        "L01-recon": "protocol",
        "L02-prompt-injection": "prompt",
        "L03-rag": "retrieval",
        "L04-agent-protocols": "protocol",
        "L05-memory": "memory",
        "L06-identity-control-plane": "identity",
        "L07-supply-chain": "supply-chain",
        "L08-detection-evasion": "detection",
        "L09-apt-capstone": "incident-response",
    }
    default_id = stage_defaults.get(str(spec.get("stage_id")), "incident-response")
    return next(track for track in TECHNICAL_TRACKS if track["id"] == default_id)


def _generated_scenario(spec: dict[str, Any]) -> dict[str, Any]:
    """Materialize a research scenario as a four-chapter technical case file.

    Expansion records are intentionally small manifests. This compiler turns
    each one into a distinct operator journey: baseline, boundary transition,
    telemetry correlation, and closure proof. It never writes literal answers;
    the per-run HMAC engine still derives those values at execution time.
    """
    scenario_id = str(spec["id"])
    prefix = re.sub(r"[^a-z0-9]+", "_", scenario_id.lower()).strip("_")[:48]
    title = str(spec["title"])
    objective = str(spec["objective"])
    tag_values = [str(value) for value in spec.get("threat_tags", []) or spec.get("concepts", [])]
    focus = ", ".join(tag_values[:3]) or "the declared control boundary"
    controls = ", ".join(str(value) for value in spec.get("required_controls", [])) or "the declared controls"
    detections = ", ".join(str(value) for value in spec.get("detection_rule_ids", [])) or "the declared detections"
    track = _technical_track_for_spec(spec)
    steps = [
        {
            "id": "s1",
            "event": f"{prefix}_baseline"[:64],
            "observation": f"Baseline for {title}: map {focus}, record the clean {track['label']} state, and preserve the approved scope before testing {objective.lower()}.",
            "evidence": {"surface": "marker", "trust": "trust-class", "decision": "decision"},
        },
        {
            "id": "s2",
            "event": f"{prefix}_boundary"[:64],
            "observation": f"Controlled boundary transition for {title}: the synthetic fixture tests {focus} while remaining loopback-only; identify where data could become authority.",
            "evidence": {"signal": "marker", "control": "control", "proof": "chained-proof"},
        },
        {
            "id": "s3",
            "event": f"{prefix}_correlation"[:64],
            "observation": f"Correlation for {title}: join the identity, request, telemetry, and {detections} records without treating one receipt or model response as proof.",
            "evidence": {"subject": "entity-id", "detection": "detection-rule", "proof": "chained-proof"},
        },
        {
            "id": "s4",
            "event": f"{prefix}_closure"[:64],
            "observation": f"Closure proof for {title}: apply {controls}, preserve the redacted {track['artifact']}, and verify the corrected decision after the fixture is removed.",
            "evidence": {"finding": "result", "response": "decision", "proof": "chained-proof"},
        },
    ]
    return {
        "id": scenario_id,
        "stage_id": spec["stage_id"],
        "difficulty": spec["difficulty"],
        "branch": spec["branch"],
        "title": title,
        "objective": objective,
        "technical_track": track["id"],
        "technical_artifact": track["artifact"],
        "clues": [
            f"Orient: treat this as a localhost-only synthetic exercise focused on {focus}.",
            f"Investigate: capture the {track['artifact']} before and after one controlled transition.",
            f"Correlate: reconcile {detections} with the identity, request, and decision timeline.",
            f"Verify: enforce {controls}; never create a real side effect.",
        ],
        "detection_rule_ids": spec["detection_rule_ids"],
        "required_controls": spec["required_controls"],
        "concepts": spec.get("concepts", []),
        "threat_tags": spec.get("threat_tags", []),
        "steps": steps,
    }


def load_scenario_pack(path: Path) -> dict[str, Any]:
    """Load canonical, research, CWF-style, and generated hard-gate manifests."""
    document = json.loads(path.read_text(encoding="utf-8"))
    expansion_paths = [
        path.with_name("scenario-expansion.json"),
        path.with_name("scenario-expansion-2026.json"),
    ]
    gates_path = path.with_name("hard-gates.json")
    existing_ids = {str(item["id"]) for item in document.get("scenarios", [])}
    # Canonical scenarios predate the research expansion and do not all carry
    # the richer track metadata. Enrich them at load time so every lab gets a
    # named technical lane and an answer-safe artifact contract without
    # duplicating 150 records in another manifest.
    for scenario in document.get("scenarios", []):
        stage_requirement = document.get("stage_requirements", {}).get(str(scenario.get("stage_id", "")), {})
        enriched_spec = {
            **scenario,
            "threat_tags": scenario.get("threat_tags") or stage_requirement.get("concepts", []),
            "concepts": scenario.get("concepts") or stage_requirement.get("concepts", []),
        }
        track = _technical_track_for_spec(enriched_spec)
        scenario.setdefault("technical_track", track["id"])
        scenario.setdefault("technical_artifact", track["artifact"])
        if not scenario.get("threat_tags"):
            scenario["threat_tags"] = [track["id"]]
        if not scenario.get("concepts"):
            scenario["concepts"] = stage_requirement.get("concepts", [track["id"]])
    expansion_pack_ids: list[str] = []
    research_basis: list[str] = []
    for expansion_path in expansion_paths:
        if not expansion_path.is_file():
            continue
        expansion = json.loads(expansion_path.read_text(encoding="utf-8"))
        for spec in expansion.get("scenarios", []):
            scenario_id = str(spec.get("id", ""))
            if scenario_id in existing_ids:
                raise ValueError(f"duplicate expanded scenario ID: {scenario_id}")
            existing_ids.add(scenario_id)
            materialized = _generated_scenario(spec)
            materialized["research_window"] = expansion.get("research_window", "2026-01-01/2026-08-17")
            materialized["research_basis"] = expansion.get("research_basis", [])
            document.setdefault("scenarios", []).append(materialized)
            requirement = document.setdefault("stage_requirements", {}).setdefault(spec["stage_id"], {})
            requirement.setdefault("scenario_ids", []).append(scenario_id)
        if expansion.get("pack_id"):
            expansion_pack_ids.append(str(expansion["pack_id"]))
        research_basis.extend(str(value) for value in expansion.get("research_basis", []))
    if expansion_pack_ids:
        document["expansion_pack_id"] = "+".join(expansion_pack_ids)
    if research_basis:
        document["research_basis"] = list(dict.fromkeys(research_basis))
    if gates_path.is_file():
        gates = json.loads(gates_path.read_text(encoding="utf-8")).get("gates", [])
        by_stage: dict[str, list[str]] = {}
        gate_by_scenario: dict[str, str] = {}
        for gate in gates:
            by_stage.setdefault(str(gate["stage_id"]), []).append(str(gate["gate_id"]))
            for scenario_id in gate.get("scenario_ids", []):
                if scenario_id in gate_by_scenario:
                    raise ValueError(f"scenario belongs to multiple hard gates: {scenario_id}")
                gate_by_scenario[str(scenario_id)] = str(gate["gate_id"])
        next_rank = max((int(gate.get("rank", 0)) for gate in gates), default=0) + 1
        scenario_lookup = {str(item["id"]): item for item in document.get("scenarios", [])}
        for stage_id, requirement in document.get("stage_requirements", {}).items():
            unassigned = [scenario_id for scenario_id in requirement.get("scenario_ids", []) if scenario_id not in gate_by_scenario]
            if len(unassigned) % 2:
                raise ValueError(f"new scenarios for {stage_id} must be added in pairs for hard gates")
            for index in range(0, len(unassigned), 2):
                pair = unassigned[index:index + 2]
                first = scenario_lookup[pair[0]]
                second = scenario_lookup[pair[1]]
                gate_slug = re.sub(r"[^a-z0-9]+", "-", pair[0].lower()).strip("-")[:48]
                gate_id = f"G{next_rank:02d}-research-{gate_slug}"
                gates.append(
                    {
                        "gate_id": gate_id,
                        "stage_id": stage_id,
                        "rank": next_rank,
                        "title": f"2026 Research Gate: {first['title']} + {second['title']}",
                        "scenario_ids": pair,
                        "detection_rule_ids": sorted(set(requirement.get("detection_rule_ids", []))),
                        "required_controls": list(requirement.get("required_controls", [])),
                        "concepts": list(dict.fromkeys((first.get("concepts", []) + second.get("concepts", []))))[:4],
                    }
                )
                by_stage.setdefault(stage_id, []).append(gate_id)
                for scenario_id in pair:
                    gate_by_scenario[scenario_id] = gate_id
                next_rank += 1
        for scenario in document.get("scenarios", []):
            if scenario["id"] in gate_by_scenario:
                scenario["gate_id"] = gate_by_scenario[scenario["id"]]
        for stage_id, requirement in document.get("stage_requirements", {}).items():
            requirement["hard_gate_ids"] = by_stage.get(stage_id, [])
        document["hard_gates"] = gates
    return document


def validate_scenarios(document: dict[str, Any], curriculum: dict[str, Any]) -> dict[str, Any]:
    errors: list[str] = []
    if document.get("schema_version") != SCHEMA_VERSION:
        errors.append(f"unsupported scenario schema: expected v{SCHEMA_VERSION}")
    if document.get("scope", {}).get("classification") != "synthetic-training-only":
        errors.append("scenario pack must be synthetic-training-only")
    if document.get("scope", {}).get("side_effects") is not False:
        errors.append("scenario pack must forbid side effects")
    curriculum_ids = {str(stage["id"]) for stage in curriculum.get("stages", [])}
    scenarios = document.get("scenarios", [])
    scenario_ids: set[str] = set()
    by_stage: dict[str, list[str]] = {}
    for scenario in scenarios:
        scenario_id = str(scenario.get("id", ""))
        if not scenario_id or scenario_id in scenario_ids:
            errors.append(f"duplicate or missing scenario ID: {scenario_id}")
        scenario_ids.add(scenario_id)
        stage_id = scenario.get("stage_id")
        if stage_id not in curriculum_ids:
            errors.append(f"scenario {scenario_id} references unknown stage")
        by_stage.setdefault(str(stage_id), []).append(scenario_id)
        if scenario.get("difficulty", 0) < 1 or not scenario.get("objective"):
            errors.append(f"scenario {scenario_id} lacks difficulty or objective")
        steps = scenario.get("steps", [])
        if len(steps) < 2 or len(steps) > 8:
            errors.append(f"scenario {scenario_id} must contain 2-8 bounded steps")
        step_ids: set[str] = set()
        for index, step in enumerate(steps):
            step_id = str(step.get("id", ""))
            event = str(step.get("event", ""))
            if not step_id or step_id in step_ids or not SAFE_EVENT_PATTERN.fullmatch(event):
                errors.append(f"scenario {scenario_id} has invalid step/event")
            step_ids.add(step_id)
            evidence = step.get("evidence")
            if not isinstance(evidence, dict) or not evidence:
                errors.append(f"scenario {scenario_id} step {step_id} lacks evidence type mapping")
            else:
                for key, value_type in evidence.items():
                    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", str(key)):
                        errors.append(f"scenario {scenario_id} step {step_id} has an invalid evidence key")
                    if value_type not in VOCABULARY:
                        errors.append(f"scenario {scenario_id} step {step_id} uses unknown evidence type {value_type!r}")
                if index > 0 and PROOF_KEY not in evidence:
                    errors.append(f"scenario {scenario_id} step {step_id} must chain the previous step via '{PROOF_KEY}'")
            if not step.get("observation"):
                errors.append(f"scenario {scenario_id} step {step_id} lacks an observation")
        if len(step_ids) != len(steps):
            errors.append(f"scenario {scenario_id} has duplicate step IDs")
        if not scenario.get("detection_rule_ids") or not scenario.get("required_controls"):
            errors.append(f"scenario {scenario_id} lacks detection rules or controls")
        if scenario.get("branch") not in {"attack", "defense", "forensics", "recovery"}:
            errors.append(f"scenario {scenario_id} has invalid branch")
    requirements = document.get("stage_requirements", {})
    if set(requirements) != curriculum_ids:
        errors.append("every curriculum stage must have an explicit scenario requirement")
    referenced_scenarios: set[str] = set()
    for stage_id, requirement in requirements.items():
        required = requirement.get("scenario_ids", [])
        if not required or len(required) != len(set(required)) or not set(required).issubset(scenario_ids):
            errors.append(f"stage {stage_id} has missing or duplicate required scenarios")
        referenced_scenarios.update(required)
        if stage_id not in curriculum_ids:
            errors.append(f"stage requirement references unknown stage {stage_id}")
        if not requirement.get("detection_rule_ids") or not requirement.get("required_controls") or not requirement.get("concepts"):
            errors.append(f"stage {stage_id} lacks synthesis requirements")
    if referenced_scenarios != scenario_ids:
        errors.append("every scenario must be required by exactly one stage")

    gates = document.get("hard_gates", [])
    gate_ids: set[str] = set()
    gated_scenarios: set[str] = set()
    gates_by_stage: dict[str, list[str]] = {}
    for gate in gates:
        gate_id = str(gate.get("gate_id", ""))
        gate_stage = str(gate.get("stage_id", ""))
        gate_scenario_ids = [str(value) for value in gate.get("scenario_ids", [])]
        if not gate_id or gate_id in gate_ids or gate_stage not in curriculum_ids:
            errors.append(f"invalid or duplicate hard gate: {gate_id}")
        gate_ids.add(gate_id)
        gates_by_stage.setdefault(gate_stage, []).append(gate_id)
        if len(gate_scenario_ids) != 2 or not set(gate_scenario_ids).issubset(scenario_ids):
            errors.append(f"hard gate {gate_id} must cover exactly two known scenarios")
        if gated_scenarios.intersection(gate_scenario_ids):
            errors.append(f"hard gate scenario is assigned more than once: {gate_id}")
        gated_scenarios.update(gate_scenario_ids)
        if not gate.get("detection_rule_ids") or not gate.get("required_controls") or not gate.get("concepts"):
            errors.append(f"hard gate {gate_id} lacks detection, control, or concept requirements")
    # Gate IDs carry readable suffixes, so validate order/rank and count
    # separately rather than requiring opaque IDs.
    if len(gates) != 75:
        errors.append(f"expected exactly 75 hard gates for 150 scenarios, found {len(gates)}")
    ranks = [gate.get("rank") for gate in gates]
    if ranks != list(range(1, len(gates) + 1)):
        errors.append("hard gate ranks must be contiguous and ordered")
    if set(gates_by_stage) != curriculum_ids or any(len(values) not in {7, 8} for values in gates_by_stage.values()):
        errors.append("each curriculum stage must contain seven or eight hard gates for its even scenario count")
    if gated_scenarios != scenario_ids:
        errors.append("every scenario must belong to exactly one hard gate")
    for stage_id, requirement in requirements.items():
        if requirement.get("hard_gate_ids") != gates_by_stage.get(stage_id, []):
            errors.append(f"stage {stage_id} hard_gate_ids do not match the ordered hard-gate manifest")
    if errors:
        raise ValueError("; ".join(errors))
    return {"scenarios": len(scenarios), "hard_gates": len(gates), "stages_with_requirements": len(requirements), "scenario_ids": sorted(scenario_ids), "by_stage": by_stage}


def scenario_map(document: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {str(item["id"]): item for item in document.get("scenarios", [])}


def requirement_for(document: dict[str, Any], stage_id: str) -> dict[str, Any]:
    requirement = document.get("stage_requirements", {}).get(stage_id)
    if requirement is None:
        raise KeyError(f"no scenario requirement for {stage_id}")
    return requirement


def step_for(scenario: dict[str, Any], index: int) -> dict[str, Any]:
    steps = scenario.get("steps", [])
    if index < 0 or index >= len(steps):
        raise IndexError("scenario is already complete")
    return steps[index]


def _digest(secret: bytes, *parts: str) -> bytes:
    return hmac.new(secret, ":".join(parts).encode("utf-8"), hashlib.sha256).digest()


def _pick(vocabulary: list[str], digest: bytes) -> str:
    index = int.from_bytes(digest[:4], "big") % len(vocabulary)
    return vocabulary[index]


def expected_for_step(
    secret: bytes,
    learner_id: str,
    scenario_id: str,
    step: dict[str, Any],
    nonce: str,
    step_index: int,
) -> dict[str, Any]:
    """Derive the per-run expected evidence values for a step.

    Step 2+ additionally expects ``proof``: the chained token issued when the
    previous step was accepted. The token is recomputed from the same HMAC so
    it cannot be forged without the secret.
    """
    expected: dict[str, Any] = {}
    step_id = str(step.get("id", ""))
    for key, value_type in step.get("evidence", {}).items():
        if key == PROOF_KEY:
            continue
        vocabulary = VOCABULARY[value_type]
        expected[key] = _pick(vocabulary, _digest(secret, learner_id, scenario_id, step_id, nonce, key))
    if PROOF_KEY in step.get("evidence", {}):
        expected[PROOF_KEY] = step_token(secret, learner_id, scenario_id, nonce, step_index - 1)
    return expected


def step_token(secret: bytes, learner_id: str, scenario_id: str, nonce: str, step_index: int) -> str:
    digest = _digest(secret, learner_id, scenario_id, nonce, "step-token", str(step_index))
    return f"ZB-STEP-{digest[:20].hex().upper()}"


def candidates_for_step(
    secret: bytes,
    learner_id: str,
    scenario_id: str,
    step: dict[str, Any],
    nonce: str,
    step_index: int,
) -> dict[str, dict[str, Any]]:
    """Candidate pools per evidence key: the correct value plus distractors.

    The correct value is always included; distractors are drawn deterministically
    from the same vocabulary so the pool is stable within a run.
    """
    expected = expected_for_step(secret, learner_id, scenario_id, step, nonce, step_index)
    candidates: dict[str, dict[str, Any]] = {}
    step_id = str(step.get("id", ""))
    for key, value_type in step.get("evidence", {}).items():
        if key == PROOF_KEY:
            continue
        vocabulary = VOCABULARY[value_type]
        correct = expected[key]
        pool = [correct]
        offset = int.from_bytes(_digest(secret, learner_id, scenario_id, step_id, nonce, key, "distract"), "big")
        for i in range(1, len(vocabulary)):
            candidate = vocabulary[(offset + i) % len(vocabulary)]
            if candidate not in pool:
                pool.append(candidate)
            if len(pool) >= min(5, len(vocabulary)):
                break
        candidates[key] = {"correct": correct, "candidates": pool}
    if PROOF_KEY in step.get("evidence", {}):
        candidates[PROOF_KEY] = {"correct": expected[PROOF_KEY], "candidates": [expected[PROOF_KEY]]}
    return candidates


def event_matches(step: dict[str, Any], event: str, evidence: dict[str, Any], expected: dict[str, Any]) -> bool:
    if event != step.get("event"):
        return False
    return set(evidence) == set(expected) and all(evidence.get(key) == value for key, value in expected.items())


def evidence_token(secret: bytes, learner_id: str, scenario_id: str, evidence: list[dict[str, Any]]) -> str:
    canonical = json.dumps(evidence, sort_keys=True, separators=(",", ":"))
    digest = hmac.new(secret, f"{learner_id}:{scenario_id}:{canonical}".encode(), hashlib.sha256).hexdigest()
    return f"ZB-EVIDENCE-{scenario_id.upper()}-{digest[:24].upper()}"


def contains_concepts(summary: str, concepts: list[str]) -> bool:
    lowered = summary.lower()
    return all(concept.lower() in lowered for concept in concepts)
