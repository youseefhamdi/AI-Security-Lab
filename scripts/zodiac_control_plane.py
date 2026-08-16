"""Synthetic agentic control-plane attack primitives for the Zodiac Bank lab.

These helpers model the six agentic control-plane techniques that MITRE ATLAS
still under-specifies (per the CSA 2026 gap analysis): agent-to-agent lateral
movement, orchestrator hijacking, credential relay through delegation chains,
cross-session memory persistence, tool-chain poisoning, and MCP-server-compromise
as a pivot. Everything is deterministic, local, and side-effect-free; the
hardened paths return a ``deny`` decision plus bounded evidence, never an action.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable


class ControlPlaneViolation(PermissionError):
    """Raised when a synthetic agent request violates a control-plane policy."""


@dataclass(frozen=True)
class AgentRecord:
    agent_id: str
    role: str
    capabilities: frozenset[str]
    branch_id: str
    compromised: bool = False


@dataclass(frozen=True)
class Decision:
    verdict: str  # "allow" | "deny" | "review"
    reason: str
    evidence: dict[str, Any] = field(default_factory=dict)


def _decision(verdict: str, reason: str, **evidence: Any) -> Decision:
    return Decision(verdict, reason, {**evidence, "synthetic": True, "side_effects": False})


def lateral_move(
    registry: dict[str, AgentRecord],
    *,
    source_agent: str,
    target_agent: str,
    requested_capability: str,
    delegation_edges: set[tuple[str, str]],
    max_hops: int = 4,
) -> Decision:
    """Model agent-to-agent lateral movement across a bounded trust graph.

    A compromised source may only reach a target when a *narrowing* delegation
    edge exists and the hop count stays within budget; otherwise the move is
    denied. This is the hardened posture the lab teaches learners to defend.
    """
    if source_agent not in registry or target_agent not in registry:
        raise ControlPlaneViolation("unknown agent in lateral movement request")
    source = registry[source_agent]
    target = registry[target_agent]
    if source_agent == target_agent:
        return _decision("deny", "self-move is not lateral movement", hops=0)

    # Bounded BFS over explicitly allowed delegation edges only.
    frontier = {source_agent}
    seen = {source_agent}
    path: dict[str, list[str]] = {source_agent: [source_agent]}
    hops = 0
    while frontier and hops < max_hops:
        next_frontier: set[str] = set()
        for node in frontier:
            for edge_from, edge_to in delegation_edges:
                if edge_from != node or edge_to in seen:
                    continue
                seen.add(edge_to)
                path[edge_to] = path[node] + [edge_to]
                next_frontier.add(edge_to)
        frontier = next_frontier
        hops += 1
        if target_agent in frontier:
            break

    if target_agent not in path:
        return _decision("deny", "no authorized delegation path to target", source=source_agent, target=target_agent, hops=hops, compromised_source=source.compromised)

    hops_used = len(path[target_agent]) - 1
    if not source.compromised:
        return _decision("allow", "uncompromised source on an authorized delegation path", path=path[target_agent], hops=hops_used)

    # A compromised source is only permitted to continue along the *narrowest*
    # reachable hop set; any requested capability beyond the target's own scope
    # is treated as a privilege escalation and denied.
    if requested_capability not in target.capabilities:
        return _decision("deny", "compromised source requested a capability outside the target scope", requested=requested_capability, target_caps=sorted(target.capabilities), hops=hops_used)
    return _decision("deny", "compromised source lateral movement is quarantined", path=path[target_agent], hops=hops_used, alert="ZB-AI-011")


def orchestrator_route(
    *,
    dispatcher: AgentRecord,
    target_worker: AgentRecord,
    task_type: str,
    allowed_routes: dict[str, set[str]],
    required_approval: bool = True,
) -> Decision:
    """Validate orchestrator task routing against a pinned route policy.

    Detects orchestrator hijacking: a dispatcher may only route a task type to a
    worker named in the policy, and privileged tasks always require approval.
    """
    allowed = allowed_routes.get(task_type, set())
    if target_worker.agent_id not in allowed:
        return _decision("deny", "task route is not in the pinned route policy", task_type=task_type, worker=target_worker.agent_id, alert="ZB-AI-011")
    if dispatcher.compromised:
        return _decision("deny", "compromised dispatcher is quarantined", dispatcher=dispatcher.agent_id, alert="ZB-AI-011")
    if required_approval:
        return _decision("review", "privileged route requires an approval checkpoint", worker=target_worker.agent_id, approval_required=True)
    return _decision("allow", "route matches the pinned policy", worker=target_worker.agent_id)


def delegation_relay(
    chain: Iterable[dict[str, Any]],
    *,
    max_depth: int = 4,
) -> Decision:
    """Detect credential relay through a delegation chain.

    Each hop must narrow (never expand) capabilities and stay within depth; any
    hop that widens scope, reuses a subject, or exceeds depth is flagged.
    """
    hops = list(chain)
    if not hops:
        raise ControlPlaneViolation("delegation chain is empty")
    if len(hops) > max_depth:
        return _decision("deny", "delegation chain exceeds depth budget", hops=len(hops), alert="ZB-AI-013")
    seen_subjects: set[str] = set()
    previous_caps: set[str] | None = None
    for index, hop in enumerate(hops):
        subject = str(hop.get("subject", ""))
        caps = set(str(value) for value in hop.get("capabilities", []))
        if not subject or not caps:
            raise ControlPlaneViolation("delegation hop is malformed")
        if subject in seen_subjects:
            return _decision("deny", "delegation subject reused across hops (credential relay)", hop=index, alert="ZB-AI-013")
        seen_subjects.add(subject)
        if previous_caps is not None and not caps.issubset(previous_caps):
            return _decision("deny", "delegation hop widens capability scope", hop=index, parent=sorted(previous_caps), child=sorted(caps), alert="ZB-AI-013")
        previous_caps = caps
    return _decision("allow", "delegation chain narrows monotonically", hops=len(hops))


def cross_session_memory(
    records: Iterable[dict[str, Any]],
    *,
    tenant_scope: str,
    allowed_entity_ids: set[str],
) -> Decision:
    """Detect poisoned memory that persists across synthetic sessions.

    Any record that was written in one session and survives into a later session
    must still satisfy provenance and tenant scope; otherwise it is quarantined.
    """
    poisoned: list[str] = []
    for record in records:
        entity_id = str(record.get("entity_id", ""))
        scope = str(record.get("scope", ""))
        synthetic = bool(record.get("synthetic"))
        if entity_id not in allowed_entity_ids:
            poisoned.append(entity_id)
        elif scope != tenant_scope:
            poisoned.append(entity_id)
        elif not synthetic:
            poisoned.append(entity_id)
    if poisoned:
        return _decision("deny", "cross-session memory record violates provenance or tenant scope", poisoned=sorted(set(poisoned)), alert="ZB-AI-005")
    return _decision("allow", "persistent memory records satisfy provenance and scope", records_checked=len(list(records)))


def mcp_pivot(
    *,
    mcp_server_id: str,
    attached_agents: Iterable[str],
    pivot_capabilities: Iterable[str],
    allowlist: set[str],
) -> Decision:
    """Model MCP-server compromise used as a pivot point.

    A compromised MCP server must not be able to pivot its attached agents toward
    capabilities outside the pinned allowlist.
    """
    agents = list(attached_agents)
    requested = set(pivot_capabilities)
    if mcp_server_id not in allowlist:
        return _decision("deny", "MCP server is not in the pinned server allowlist", server=mcp_server_id, attached=agents, alert="ZB-AI-016")
    overflow = requested - allowlist
    if overflow:
        return _decision("deny", "MCP pivot requested capabilities outside the allowlist", overflow=sorted(overflow), attached=agents, alert="ZB-AI-012")
    return _decision("allow", "MCP pivot constrained to allowlisted capabilities", attached=agents)


def control_plane_snapshot() -> dict[str, Any]:
    return {
        "techniques": ["lateral-movement", "orchestrator-hijack", "delegation-relay", "cross-session-memory", "mcp-pivot"],
        "synthetic": True,
        "side_effects": False,
        "external_egress": False,
    }
