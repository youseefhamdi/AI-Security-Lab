"""Deterministic graph primitives for the synthetic Zodiac Bank domain.

The graph is derived from canonical JSON and is never an authorization source.
Every node and edge retains provenance and a trust classification so graph
results can be treated as evidence by context assembly and workflows.
"""

from __future__ import annotations

from collections import deque
from typing import Any, Iterable

SCHEMA_VERSION = 1
GRAPH_ID = "zodiac-bank-canonical-graph"

ENTITY_SPECS = (
    ("branches", "branch_id", "branch"),
    ("staff", "staff_id", "staff"),
    ("customers", "customer_id", "customer"),
    ("products", "product_id", "product"),
    ("accounts", "account_id", "account"),
    ("policies", "policy_id", "policy"),
    ("cases", "case_id", "case"),
)


def _node(node_id: str, node_type: str, attributes: dict[str, Any], source: str, trust: str = "canonical") -> dict[str, Any]:
    return {
        "id": node_id,
        "type": node_type,
        "label": str(attributes.get("name", node_id)),
        "attributes": dict(attributes),
        "provenance": {"source": source, "synthetic": True},
        "trust": trust,
    }


def _edge(source: str, relation: str, target: str, provenance: str, trust: str = "canonical") -> dict[str, Any]:
    return {
        "from": source,
        "relation": relation,
        "to": target,
        "provenance": {"source": provenance, "synthetic": True},
        "trust": trust,
    }


def build_graph(bank: dict[str, Any], workflows: dict[str, Any]) -> dict[str, Any]:
    """Build a stable property graph from canonical bank and workflow records."""
    nodes: dict[str, dict[str, Any]] = {}
    edges: dict[tuple[str, str, str], dict[str, Any]] = {}
    bank_source = "bank-data/zodiac-bank.json"
    workflow_source = "bank-data/workflows.json"

    def add_node(node_id: str, node_type: str, attributes: dict[str, Any], source: str, trust: str = "canonical") -> None:
        if node_id in nodes and nodes[node_id]["type"] != node_type:
            raise ValueError(f"graph node type collision: {node_id}")
        nodes[node_id] = _node(node_id, node_type, attributes, source, trust)

    def add_edge(source: str, relation: str, target: str, provenance: str, trust: str = "canonical") -> None:
        key = (source, relation, target)
        edges[key] = _edge(source, relation, target, provenance, trust)

    for collection, id_key, node_type in ENTITY_SPECS:
        for record in bank.get(collection, []):
            node_id = str(record[id_key])
            add_node(node_id, node_type, record, bank_source)

    for worker in workflows.get("workers", []):
        worker_id = str(worker["worker_id"])
        add_node(worker_id, "worker", worker, workflow_source, "control")

    for workflow in workflows.get("workflows", []):
        workflow_id = str(workflow["workflow_id"])
        add_node(workflow_id, "workflow", workflow, workflow_source, "control")
        for branch in workflow.get("branches", []):
            branch_id = f"{workflow_id}::{branch['branch_id']}"
            add_node(branch_id, "workflow_branch", branch, workflow_source, "control")
            add_edge(workflow_id, "has_branch", branch_id, workflow_source, "control")
            for worker_id in branch.get("route", []):
                add_edge(branch_id, "routes_to", str(worker_id), workflow_source, "control")

    for relationship in bank.get("relationships", []):
        add_edge(str(relationship["from"]), str(relationship["relation"]), str(relationship["to"]), bank_source)

    branches = {item["branch_id"]: item for item in bank.get("branches", [])}
    staff = {item["staff_id"]: item for item in bank.get("staff", [])}
    customers = {item["customer_id"]: item for item in bank.get("customers", [])}
    workers = {item["worker_id"]: item for item in workflows.get("workers", [])}

    for member in staff.values():
        add_edge(member["staff_id"], "works_at", member["branch_id"], bank_source)
        if member.get("worker_id") in workers:
            add_edge(member["staff_id"], "operates_as", member["worker_id"], bank_source)
    for customer in customers.values():
        add_edge(customer["customer_id"], "home_branch", customer["home_branch_id"], bank_source)
    for account in bank.get("accounts", []):
        add_edge(account["account_id"], "owned_by", account["customer_id"], bank_source)
        add_edge(account["account_id"], "held_at", account["branch_id"], bank_source)
        add_edge(account["account_id"], "uses_product", account["product_id"], bank_source)
    for policy in bank.get("policies", []):
        add_edge(policy["policy_id"], "owned_by", policy["owner_worker_id"], bank_source)
    for case in bank.get("cases", []):
        add_edge(case["case_id"], "concerns", case["customer_id"], bank_source)
        add_edge(case["case_id"], "at_branch", case["branch_id"], bank_source)
        add_edge(case["case_id"], "assigned_to", case["assigned_worker_id"], bank_source)

    return {
        "schema_version": SCHEMA_VERSION,
        "graph_id": GRAPH_ID,
        "bank_id": bank.get("bank_id"),
        "classification": "synthetic-training-only",
        "node_count": len(nodes),
        "edge_count": len(edges),
        "nodes": sorted(nodes.values(), key=lambda item: item["id"]),
        "edges": sorted(edges.values(), key=lambda item: (item["from"], item["relation"], item["to"])),
    }


def validate_graph(graph: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    nodes = graph.get("nodes", [])
    edges = graph.get("edges", [])
    node_ids = [node.get("id") for node in nodes]
    node_set = set(node_ids)
    if graph.get("schema_version") != SCHEMA_VERSION:
        errors.append("unsupported graph schema")
    if len(node_ids) != len(node_set):
        errors.append("graph contains duplicate node IDs")
    for node in nodes:
        if not node.get("provenance", {}).get("source") or node.get("trust") not in {"canonical", "control"}:
            errors.append(f"node lacks provenance or trust: {node.get('id')}")
    for edge in edges:
        if edge.get("from") not in node_set or edge.get("to") not in node_set:
            errors.append(f"edge references an unknown node: {edge}")
        if not edge.get("provenance", {}).get("source"):
            errors.append(f"edge lacks provenance: {edge}")
    if graph.get("node_count") != len(nodes) or graph.get("edge_count") != len(edges):
        errors.append("graph counts do not match graph contents")
    return errors


def neighborhood(graph: dict[str, Any], roots: Iterable[str], depth: int = 1, max_nodes: int = 32, allowed_ids: set[str] | None = None) -> dict[str, Any]:
    """Return a bounded undirected neighborhood, optionally restricted to IDs."""
    depth = max(0, min(int(depth), 3))
    max_nodes = max(1, min(int(max_nodes), 64))
    node_map = {node["id"]: node for node in graph.get("nodes", [])}
    roots = [root for root in roots if root in node_map and (allowed_ids is None or root in allowed_ids)]
    selected: set[str] = set(roots[:max_nodes])
    distances = {root: 0 for root in selected}
    queue: deque[str] = deque(selected)
    while queue and len(selected) < max_nodes:
        current = queue.popleft()
        if distances[current] >= depth:
            continue
        for edge in graph.get("edges", []):
            if edge["from"] == current:
                neighbor = edge["to"]
            elif edge["to"] == current:
                neighbor = edge["from"]
            else:
                continue
            if neighbor not in node_map or (allowed_ids is not None and neighbor not in allowed_ids) or neighbor in selected:
                continue
            selected.add(neighbor)
            distances[neighbor] = distances[current] + 1
            queue.append(neighbor)
            if len(selected) >= max_nodes:
                break

    selected_edges = [edge for edge in graph.get("edges", []) if edge["from"] in selected and edge["to"] in selected]
    return {
        "roots": roots,
        "depth": depth,
        "nodes": [node_map[node_id] for node_id in sorted(selected)],
        "edges": sorted(selected_edges, key=lambda item: (item["from"], item["relation"], item["to"])),
        "truncated": bool(queue) or len(selected) >= max_nodes,
    }
