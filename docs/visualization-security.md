# Visualization Security

Codebase visualization is a useful reconnaissance and review aid, but the generated graph is also a high-value information source and an injection surface.

## Knowledge graph as an attack surface

The graph combines deterministic parser output with model-generated summaries, relationships, architectural layers, and tours. An attacker who can influence source files, documentation, dependencies, comments, or analysis inputs may poison any of those derived fields.

Treat nodes, edges, summaries, confidence values, and tours as untrusted derived data. Do not use them directly for authorization, release approval, or security conclusions.

## Codebase visualization leaking sensitive architecture

A dashboard can expose internal hostnames, service names, database boundaries, authentication components, deployment paths, feature flags, dependency versions, and likely trust relationships. A committed `.ua/knowledge-graph.json` may also preserve summaries or source excerpts that contain credentials.

Controls:

- Bind dashboards to localhost or require strong authentication.
- Redact secrets and sensitive source excerpts before sharing graphs.
- Apply repository and tenant access controls to graph files.
- Keep `.ua/` artifacts out of public distributions unless reviewed.
- Use a denylist and secret scanner before committing graph output.
- Record graph generation version, source commit, and access events.

## Diff impact analysis revealing exploit paths

Diff views can reveal which components change together, how a request reaches a privileged tool, and which validation or logging boundary was modified. This is valuable for defenders and attackers alike. A malicious change may also poison the graph delta so that a dangerous edge appears harmless or an important impact path is omitted.

Controls:

- Compare graph impact results with source-level tests and code review.
- Pin analysis to the exact commit being reviewed.
- Require independent review for authentication, tool, dependency, and CI changes.
- Show removed as well as added nodes and edges.
- Mark uncertainty and stale graph data prominently.
- Never let visualization output be the sole approval signal for a merge or release.

## Safe lab workflow

Generate the graph only on a local copy, inspect the `.ua/` diff, run secret scanning, and publish the dashboard only to the isolated lab host. On the build-only VPS, use static checks and do not run plugin installers, model-backed analysis, or dashboard commands.
