# Inference Fingerprint Matrix

This matrix is a hypothesis for Unit 2.3 reconnaissance. Capture actual outputs with `RUNTIME=1 ./scripts/test_inference.sh` and compare them against these expected differences. Results can vary with prompt wording, sampling settings, model revisions, and system prompts.

| Fingerprint | `llama3.2:1b` via Ollama | `bonsai-27b` via llama.cpp |
| --- | --- | --- |
| **Identity probe responses** | More likely to give a short, generic identity answer, confuse its exact runtime/provider, or repeat the configured model name. | More likely to provide a detailed model-family answer, but may identify the GGUF/runtime imperfectly because the serving layer is llama.cpp. |
| **Contradiction testing behavior** | More likely to lose the original premise, accept a contradiction without clearly separating assumptions, or provide a shallow correction. | More likely to track both premises and explicitly distinguish the original answer from the hypothetical contradiction. |
| **Knowledge cutoff dates** | May provide a broader or less consistent cutoff claim; validate whether it knows the configured model's actual training boundary. | May claim a later-looking cutoff because of the underlying Qwen-family training data; treat self-reported dates as untrusted observations. |
| **Context window** | The lab configuration is intentionally limited to approximately 2K tokens by `OLLAMA_CONTEXT_LENGTH=2048`. Long prompts may truncate, lose early instructions, or fail sooner. | The lab server is configured to 8K tokens with `-c 8192`, while Bonsai's native context is documented as 262K. The lab result therefore measures the 8K serving limit, not the native maximum. |
| **Code style differences** | More likely to produce terse, repetitive, or syntactically plausible but incomplete code; security review explanations may be shallow. | More likely to produce structured reviews, preserve requested formatting, and explain trade-offs, though 1-bit quantization can introduce odd tokens or brittle details. |
| **Arithmetic accuracy** | The 1B model is expected to make more multi-step arithmetic and counting mistakes, especially when the prompt contains distracting context. | The larger model should generally reason more accurately, but 1-bit quantization artifacts can cause occasional arithmetic slips, malformed numbers, or unstable exact calculations. |

## Suggested probes

Use the same temperature, token budget, and prompt for both backends:

1. **Identity:** `State your model family, serving runtime, and knowledge cutoff. Mark each item as known or uncertain.`
2. **Contradiction:** `Answer A, then accept an explicitly false premise and explain whether your conclusion changed.`
3. **Knowledge cutoff:** `What is the latest date you can reliably describe? Give a date and confidence level.`
4. **Context:** Place a marker at the beginning and end of an 8K-plus prompt; ask the model to return both markers.
5. **Code style:** Ask both models to review the same short function and require JSON findings with identical keys.
6. **Arithmetic:** Ask for several chained calculations and require an exact final integer plus intermediate steps.

## Interpretation cautions

- Fingerprinting is probabilistic, not proof of model identity.
- Provider metadata and the model field can be configured or spoofed by the serving layer.
- A response difference may come from context limits, templates, quantization, sampling, or prompt formatting rather than model weights alone.
