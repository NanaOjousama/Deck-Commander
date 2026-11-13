# Deck Commander — AI efficiency benchmark

This is the **read-only Phase 1 benchmark harness** for [Issue #2](https://github.com/NanaOjousama/Deck-Commander/issues/2). It measures how many MCP tool calls and how much response text a synthetic task requires. The benchmark runs against a *separate gateway child process* and **does not change the active ChatGPT tunnel or its behavior**.

## Reproduce

Install Node.js and the repository's MCP SDK dependency (`npm install`), then from the repository root run:

```bash
node benchmarks/run.mjs --samples=5
```

Alternatively, on an authorized existing Deck Commander installation with the SDK already installed elsewhere:

```bash
DC_MCP_SDK_ROOT=/path/to/@modelcontextprotocol/sdk \
DC_NODE=/path/to/node node benchmarks/run.mjs --samples=5
```

Optional `DC_GATEWAY` selects the installed gateway source to launch. All fixture content lives in a temporary synthetic directory, is cleaned after the test, and must not reference personal projects.

The test harness records each task's ID, correctness verdict, direct MCP client call count, tool-text UTF-8 bytes, serialized tool-result bytes, elapsed time, API errors, empty process-output polls, and duplicate file reads. By default it repeats each approach three times; the published initial baseline used **five repetitions**. A simple estimated token proxy (`ceil(text bytes / 4)`) is provided, **not** a real ChatGPT tokenizer or context-usage reading.

Raw per-sample JSON results stay in private local `benchmarks/results/` and are **not** suitable for automatic public upload. The GitHub report contains only aggregated synthetic measurements. Please review any later benchmark output before publication.

## Interpreting comparisons

The pairs compare different **task strategies with the same narrow correctness goal**, *not* deployed-before vs deployed-after plugin revisions. For example, reading all 1,000 filenames and asking only for a count are different response contracts; the bounded-count alternative is valid only if the user wanted the count rather than the filenames.

Several compact strategies use a short Python subprocess and are **slower** than the native DC tool despite returning fewer bytes. The benchmark is intended to reveal these trade-offs and guide future API design, not to justify batching by default.

See [Initial baseline results](BASELINE_2026-11-12.md) for the measured values and caveats.