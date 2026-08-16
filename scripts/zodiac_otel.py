"""Synthetic OpenTelemetry GenAI semantic-convention telemetry for the lab.

Emits in-memory traces shaped like the OpenTelemetry GenAI semantic conventions
(gen_ai.* spans, agent spans, MCP tool spans, and security events) so the lab can
teach detection over the same telemetry shape the industry standardized in 2026.
No exporter, network, or collector is involved; everything is local and synthetic.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable


class TelemetryViolation(ValueError):
    pass


@dataclass(frozen=True)
class Span:
    span_id: str
    trace_id: str
    parent_span_id: str | None
    name: str
    kind: str  # "gen_ai.agent" | "gen_ai.tool" | "gen_ai.client" | "security.event"
    attributes: dict[str, Any] = field(default_factory=dict)


class GenAITraceStore:
    """Bounded in-memory trace store with correlation and counters."""

    def __init__(self, max_spans: int = 4096) -> None:
        self.max_spans = max(32, int(max_spans))
        self._spans: dict[str, Span] = {}
        self._by_trace: dict[str, list[str]] = {}

    def add(self, span: Span) -> None:
        if len(self._spans) >= self.max_spans:
            raise TelemetryViolation("trace span capacity reached")
        if span.span_id in self._spans:
            raise TelemetryViolation("duplicate span id")
        self._spans[span.span_id] = span
        self._by_trace.setdefault(span.trace_id, []).append(span.span_id)

    def metrics(self) -> dict[str, Any]:
        traces = len(self._by_trace)
        agent_spans = sum(1 for span in self._spans.values() if span.kind == "gen_ai.agent")
        tool_spans = sum(1 for span in self._spans.values() if span.kind == "gen_ai.tool")
        security_events = sum(1 for span in self._spans.values() if span.kind == "security.event")
        orphaned = sum(1 for span in self._spans.values() if span.parent_span_id is not None and span.parent_span_id not in self._spans)
        return {
            "spans": len(self._spans),
            "traces": traces,
            "agent_spans": agent_spans,
            "tool_spans": tool_spans,
            "security_events": security_events,
            "orphaned_spans": orphaned,
            "synthetic": True,
            "external_egress": False,
        }


def agent_span(*, trace_id: str, span_id: str, parent_span_id: str | None, agent_name: str, step: str, model: str | None = None, input_tokens: int = 0) -> Span:
    return Span(span_id, trace_id, parent_span_id, step, "gen_ai.agent", {
        "gen_ai.agent.name": agent_name,
        "gen_ai.agent.step": step,
        **({"gen_ai.request.model": model} if model else {}),
        "gen_ai.usage.input_tokens": int(input_tokens),
    })


def tool_span(*, trace_id: str, span_id: str, parent_span_id: str, tool_name: str, server_name: str, status: str) -> Span:
    return Span(span_id, trace_id, parent_span_id, f"tool.{tool_name}", "gen_ai.tool", {
        "gen_ai.tool.name": tool_name,
        "gen_ai.tool.server": server_name,
        "gen_ai.tool.status": status,
    })


def security_event_span(*, trace_id: str, span_id: str, parent_span_id: str, rule_id: str, outcome: str) -> Span:
    return Span(span_id, trace_id, parent_span_id, f"security.{rule_id}", "security.event", {
        "security.rule_id": rule_id,
        "security.outcome": outcome,
    })


def build_agent_trace(*, trace_id: str, agent_name: str, steps: Iterable[str], tool_calls: Iterable[tuple[str, str, str]], rule_id: str | None = None) -> GenAITraceStore:
    """Build a coherent trace tree: root agent span -> step spans -> tool spans -> optional security event."""
    store = GenAITraceStore()
    root = agent_span(trace_id=trace_id, span_id=f"{trace_id}-root", parent_span_id=None, agent_name=agent_name, step="root")
    store.add(root)
    for index, step in enumerate(steps):
        step_span = agent_span(trace_id=trace_id, span_id=f"{trace_id}-step-{index}", parent_span_id=root.span_id, agent_name=agent_name, step=step, input_tokens=index + 1)
        store.add(step_span)
        for tool_name, server_name, status in tool_calls:
            store.add(tool_span(trace_id=trace_id, span_id=f"{trace_id}-step-{index}-{tool_name}", parent_span_id=step_span.span_id, tool_name=tool_name, server_name=server_name, status=status))
    if rule_id:
        store.add(security_event_span(trace_id=trace_id, span_id=f"{trace_id}-security", parent_span_id=root.span_id, rule_id=rule_id, outcome="alert"))
    return store
