# Knowledge Graph Attack Scenarios

These exercises target the local Understand-Anything graph generated under `.ua/`. Treat the graph as derived, untrusted analysis data rather than an authority.

## Graph manipulation via code injection

A malicious import, decorator, generated file, or dynamic loader can cause the analyzer to infer relationships that are not part of the intended architecture. An attacker may place a code comment or identifier that causes an LLM summarizer to recommend a privileged path.

**Test:** add a harmless canary import in an isolated branch, regenerate the graph, and compare node/edge changes. Check whether the graph records source locations and whether generated summaries quote the original code.

## Node poisoning

A false summary attached to a file, class, function, or service node can mislead onboarding, code review, and incident response. A poisoned node may claim that authentication is enforced when the implementation is permissive.

**Test:** alter a controlled summary or fixture description, regenerate, and verify that the dashboard shows provenance, generation time, graph version, and diff history.

## Edge injection

Fake imports, dependency metadata, package names, or generated call sites can create edges between trusted and untrusted components. The resulting graph may make an attacker-controlled package look like a required internal dependency.

**Test:** introduce a synthetic dependency edge in a disposable branch and verify that edge origin, parser confidence, and source location are visible and reviewable.

## Tour manipulation

Guided tours are ordered explanations of the architecture. A malicious or incorrect tour can omit a security boundary, place an untrusted component before authentication, or direct a reviewer away from an exploit path.

**Test:** compare the generated tour against an independently maintained critical-path checklist. Treat tour order as a hint, not proof of execution order or trust.

## Controls

- Keep generated graph data out of production authorization decisions.
- Verify graph updates against source-controlled code and signed commits.
- Require human review for large node/edge changes.
- Preserve parser/model provenance and confidence for every graph item.
- Separate generated summaries from source code and display both.
- Restrict dashboard access and avoid serving graphs containing secrets to broad audiences.
