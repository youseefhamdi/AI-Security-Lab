"""Synthetic agentjacking chain modeling for the Zodiac Bank lab.

Agentjacking (Tenet Security / CSA, June 2026) injects instructions into an
open, write-only telemetry endpoint (a Sentry-style DSN) so an MCP-connected AI
coding agent later treats attacker content as authoritative diagnostics and
executes commands with the developer's own privileges.

This module models the *decision chain* only. It never issues network requests,
never executes commands, and never touches a real telemetry endpoint. It teaches
learners to distinguish trusted telemetry from attacker-controlled content and
to refuse tool actions derived from unverified input.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterable

# Harmless, clearly-labeled markers stand in for real payloads so the module can
# detect the *pattern* without shipping or executing anything dangerous.
INSTRUCTION_PATTERN = re.compile(
    r"(?i)\b(?:run|execute|install|npx|pip install|npm install|curl|download|read env|exfiltrate|ignore previous|override)\b"
)


@dataclass(frozen=True)
class TelemetryEvent:
    event_id: str
    source: str  # "trusted-diagnostic" | "attacker-controlled" | "unknown"
    fields: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class AgentjackingDecision:
    verdict: str  # "allow" | "review" | "block"
    reason: str
    evidence: dict[str, Any] = field(default_factory=dict)


def extract_instruction_markers(text: str) -> list[str]:
    """Return harmless marker tokens that indicate instruction-like content."""
    if not isinstance(text, str):
        return []
    return sorted(set(INSTRUCTION_PATTERN.findall(text)))


def classify_telemetry_event(event: TelemetryEvent) -> dict[str, Any]:
    """Classify whether an event carries attacker-controlled instruction content."""
    markers: list[str] = []
    combined: list[str] = []
    for key, value in event.fields.items():
        if isinstance(value, str):
            combined.append(value)
            markers.extend(extract_instruction_markers(value))
        elif isinstance(value, dict):
            for nested in value.values():
                if isinstance(nested, str):
                    combined.append(nested)
                    markers.extend(extract_instruction_markers(nested))
    return {
        "event_id": event.event_id,
        "source": event.source,
        "instruction_markers": sorted(set(markers)),
        "has_markdown_payload": any("\n" in text for text in combined),
        "attacker_controlled": event.source == "attacker-controlled",
        "synthetic": True,
    }


def decide_tool_action(event: TelemetryEvent, *, allowed_sources: set[str]) -> AgentjackingDecision:
    """Refuse tool actions derived from attacker-controlled telemetry content."""
    if event.source not in allowed_sources:
        classification = classify_telemetry_event(event)
        if classification["instruction_markers"]:
            return AgentjackingDecision("block", "attacker-controlled telemetry carries instruction-like content", classification)
        return AgentjackingDecision("block", "telemetry source is not in the trusted allowlist", classification)
    classification = classify_telemetry_event(event)
    if classification["instruction_markers"]:
        return AgentjackingDecision("review", "trusted source unexpectedly carries instruction-like content", classification)
    return AgentjackingDecision("allow", "trusted diagnostic content with no instruction markers", classification)


def agentjacking_chain(events: Iterable[TelemetryEvent], *, allowed_sources: set[str]) -> dict[str, Any]:
    """Run the full six-stage decision chain over a bounded event list."""
    decisions: list[dict[str, Any]] = []
    blocked = 0
    for event in events:
        decision = decide_tool_action(event, allowed_sources=allowed_sources)
        decisions.append({"event_id": event.event_id, "verdict": decision.verdict, "reason": decision.reason, "evidence": decision.evidence})
        if decision.verdict == "block":
            blocked += 1
    return {
        "events": len(decisions),
        "blocked": blocked,
        "decisions": decisions,
        "verdict": "block" if blocked else "allow",
        "synthetic": True,
        "external_egress": False,
        "command_executed": False,
    }


def snapshot() -> dict[str, Any]:
    return {
        "technique": "agentjacking-mcp-telemetry-injection",
        "synthetic": True,
        "side_effects": False,
        "command_executed": False,
    }
