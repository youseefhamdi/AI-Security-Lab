"""Mutation-based evolutionary evaluation primitives for the Zodiac Bank lab.

Models the 2026 shift from static benchmarks to evolutionary red-teaming: attack
templates are mutated deterministically, run across synthetic surfaces, and any
detector that regresses is surfaced for remediation. No model is called and no
real jailbreak payload is shipped or executed.
"""

from __future__ import annotations

from typing import Any, Callable, Iterable

BASE_MUTATIONS = ("case", "whitespace", "encoding", "zero-width", "split")


def mutate(template: str, mutation: str) -> str:
    """Apply a deterministic, harmless mutation to an abstract attack template."""
    if mutation == "case":
        return template.upper()
    if mutation == "whitespace":
        return template.replace(" ", "  ")
    if mutation == "encoding":
        return template.replace(" ", "%20")
    if mutation == "zero-width":
        return "\u200b".join(template)
    if mutation == "split":
        return " ".join(template)
    raise ValueError(f"unknown mutation: {mutation}")


def _fitness(detector: Callable[[str], bool], variants: Iterable[str], *, should_alert: bool) -> float:
    hits = sum(1 for variant in variants if bool(detector(variant)) == should_alert)
    return hits / max(len(list(variants)), 1)


def evolve(
    templates: Iterable[dict[str, Any]],
    detector: Callable[[str], bool],
    *,
    generations: int = 3,
) -> dict[str, Any]:
    """Evolve attack templates and report detector regression per generation."""
    population = list(templates)
    if not population:
        raise ValueError("evolution requires at least one template")
    history: list[dict[str, Any]] = []
    surviving: list[dict[str, Any]] = []
    for generation in range(int(generations)):
        failures: list[dict[str, Any]] = []
        next_population: list[dict[str, Any]] = []
        for case in population:
            marker = str(case.get("marker", ""))
            should_alert = bool(case.get("should_alert"))
            variants = [marker] + [mutate(marker, mutation) for mutation in BASE_MUTATIONS]
            fitness = _fitness(detector, variants, should_alert=should_alert)
            if fitness < 1.0:
                failures.append({"marker": marker, "fitness": fitness, "variants": len(variants)})
            next_population.append(case)
        history.append({"generation": generation, "failures": len(failures), "population": len(population)})
        surviving = failures
        population = next_population
    return {
        "generations": int(generations),
        "history": history,
        "surviving_failures": len(surviving),
        "regression_found": bool(surviving),
        "synthetic": True,
        "model_calls": 0,
        "external_egress": False,
    }


def transfer_matrix(
    cases: Iterable[dict[str, Any]],
    detectors: dict[str, Callable[[str], bool]],
) -> dict[str, Any]:
    """Measure attack-family transfer across detectors."""
    rows: dict[str, dict[str, bool]] = {}
    for case in cases:
        marker = str(case.get("marker", ""))
        should_alert = bool(case.get("should_alert"))
        rows[marker] = {}
        for name, detector in detectors.items():
            rows[marker][name] = bool(detector(marker)) == should_alert
    failures = {marker: [name for name, ok in row.items() if not ok] for marker, row in rows.items()}
    return {
        "cases": len(rows),
        "detectors": len(detectors),
        "results": rows,
        "failures": {marker: names for marker, names in failures.items() if names},
        "transfer_gap_found": any(failures.values()),
        "synthetic": True,
    }


def snapshot() -> dict[str, Any]:
    return {"techniques": ["mutation", "evolution", "transfer"], "synthetic": True, "model_calls": 0, "external_egress": False}
