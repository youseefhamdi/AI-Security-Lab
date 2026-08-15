# Lab Application Attack Exercises

These exercises target the deliberately vulnerable Aurora and Phoenix applications in the isolated local lab. All payloads are synthetic and must remain within the authorized environment.

## Aurora prompt injection

Aurora uses structured context engineering by default: the user request, canonical graph packet, retrieved documents, LightRAG output, and Mem0 results are separated and marked by trust. Set `CONTEXT_ENGINEERING_MODE=legacy` only for the controlled comparison; legacy mode concatenates retrieved context with the user request and intentionally preserves the prompt-injection weakness. A user can then attempt to override the support persona, request hidden context, or make retrieved text act as an instruction.

**Exercise:** send the prompt-injection payload in `exercises/app_attacks.sh` and inspect whether Aurora reveals its system prompt, internal backend details, or memory content.

**Defenses to compare:** structured message roles, the bounded `/v1/context/assemble` packet, untrusted-context delimiters, provenance validation, instruction-like content detection, output filtering, and human approval for sensitive actions.

## Aurora debug endpoint exploitation

`GET /debug` intentionally returns the system prompt, fake admin credentials, internal API-key canaries, active conversations, and backend URLs without authentication. This demonstrates direct information disclosure and how leaked conversation state can amplify prompt-injection impact.

**Exercise:** fetch `/debug` and check whether session/user isolation is visible in the response.

## Phoenix guardrail bypass

Phoenix asks Bonsai not to emit `approved`, `LGTM`, or `ship it`, then performs a naive exact-text check on the final generated output. The guardrail is intentionally bypassable through encoding, token splitting, indirect approval language, or output transformations.

**Exercise:** provide code comments that ask the model to split an approval phrase, encode it, or place it in a structured field. Compare the response with a direct blocked phrase.

**Defenses to compare:** validate semantic approval state rather than strings, use deterministic policy checks, refuse auto-approval as a state transition, and require a human reviewer.

## Static/runtime separation

On the build-only VPS, only run `bash -n`, Python compilation, and file checks. Run `RUNTIME=1 ./exercises/app_attacks.sh` only on the isolated local machine after the applications and their pre-pulled model backends are running.
