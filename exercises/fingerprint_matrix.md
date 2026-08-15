# Inference Fingerprint Matrix

The lab now uses one inference backend: PrismML Bonsai 27B served through llama.cpp's OpenAI-compatible API. This matrix focuses on identifying the model/runtime boundary rather than comparing duplicate models.

| Fingerprint | Expected Bonsai/llama.cpp observation |
| --- | --- |
| **Identity probing** | The model may describe itself as a Qwen-family/Bonsai model, but serving metadata can be configured or spoofed. Compare `/v1/models`, response metadata, and the model's self-report. |
| **Contradiction testing** | Bonsai should generally track contradictions better than a small model, but test whether the 1-bit quantization changes its confidence or causes it to accept a false premise. |
| **Knowledge cutoff** | Ask for a date and confidence. Treat self-reported cutoff information as untrusted; the model name and runtime do not prove training-data boundaries. |
| **Behavior testing** | Compare refusal style, instruction priority, tool-use claims, JSON compliance, and prompt-injection susceptibility under identical prompts. |
| **Capability boundary** | Test vision/tool-use claims separately from text-only requests. The current HTTP service receives text unless the client explicitly supplies supported multimodal content or tools. |
| **Context window** | Lite mode defaults to `-c 4096` to reduce RAM. Bonsai's documented native context is much larger, so results measure the configured serving limit, not the native maximum. Set `BONSAI_CONTEXT_SIZE` higher only when needed. |
| **Code style** | Check structured output, code completion, security findings, and consistency across repeated runs at temperature zero. |
| **Arithmetic accuracy** | Use chained calculations and exact integer output. Larger reasoning capacity does not eliminate occasional 1-bit quantization artifacts. |

## Suggested probes

Use the same endpoint and sampling settings:

1. **Identity:** `State your model family, serving runtime, and knowledge cutoff. Mark each item as known or uncertain.`
2. **Contradiction:** `Answer a factual question, then accept an explicitly false premise and explain whether the conclusion changed.`
3. **Knowledge cutoff:** `What is the latest date you can reliably describe? Give a date and confidence level.`
4. **Context:** place markers at the beginning and end of a prompt near the configured context limit.
5. **Capability:** ask whether the current request contains an image or tool definition; compare the answer with actual request metadata.
6. **Arithmetic:** require intermediate steps and an exact final integer.

Run the static-safe test harness with:

```bash
RUNTIME=1 ./scripts/test_inference.sh
```

## Interpretation cautions

- Fingerprinting is probabilistic, not proof of model identity.
- Provider metadata and the model field can be configured by the serving layer.
- Differences may come from context limits, chat templates, sampling, quantization, or request formatting.
