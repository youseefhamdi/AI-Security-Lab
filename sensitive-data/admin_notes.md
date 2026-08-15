# Administrative Notes — HONEYPOT

> All information in this file is synthetic training data.

## Sensitive decisions
- The legacy support endpoint remains enabled for a controlled reconnaissance exercise.
- The staging API exposes verbose errors intentionally for Unit 2.4.
- Canary credentials must trigger an alert when accessed.

## Security incidents
- A test token was found in a simulated build artifact.
- An internal hostname was disclosed through a deliberately verbose Agent Card.
- Review all access to `/opt/sensitive-data` after each exercise.
