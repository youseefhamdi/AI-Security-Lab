# Security Policy

## Supported versions

Only the latest commit on the `master` branch is supported. The lab is a
rapidly evolving training environment; issues are fixed forward, not
backported.

## The lab is intentionally vulnerable — please read this

Zodiac Bank AI Security Lab is a **synthetic, local-only training
environment**. The challenge surfaces are *deliberately* vulnerable so
students can learn from them:

- debug/metadata endpoints that leak synthetic information
- synthetic (fake) credentials and honeypot secrets
- unauthenticated protocol exercises
- prompt-injection and guardrail weaknesses
- memory/retrieval attack fixtures

None of these are real vulnerabilities in a production sense — they are the
teaching material. Run the lab only on a machine you control, keep every
service bound to `localhost`, and never reuse its synthetic credentials for
anything real.

## What we *do* want to hear about

Please report defects that would make the lab unsafe or broken for a *real*
user, for example:

- Code that contacts external hosts or enables egress outside the declared
  `localhost` boundary
- A way to escape the progression/authorization control plane
  (e.g. obtaining a hard-gate flag without completing the required evidence)
- A way to persist to or exfiltrate the host filesystem from inside a
  container
- A regression that causes strict security mode to accept placeholder secrets
- Embedded real credentials or secrets committed to the repository

## Reporting a vulnerability

Do **not** open a public issue for a suspected vulnerability. Instead:

1. Open a **private security advisory** on GitHub:
   **Security → Advisories → New draft security advisory**.
2. Describe the issue, the affected service/profile, and steps to reproduce.
3. Include whether it requires strict mode, a specific profile
   (`core`/`lite`/`full`), or a running inference provider.

We aim to acknowledge reports within 5 business days and to confirm or triage
within 14 days.

## Disclosure policy

- Please give us a reasonable window to address an issue before public
  disclosure.
- When an advisory is resolved, credit is given to the reporter unless they
  request anonymity.
