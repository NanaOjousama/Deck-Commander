# Deck Commander — AI efficiency and minimal-work contract

**Status:** design requirements, November 15, 2026. **Not yet an implemented guarantee.** This document governs planned improvements to the private gateway and sanitized public source. Track the work in [Issue #2](https://github.com/NanaOjousama/Deck-Commander/issues/2).

## The actual problem

The owner has hit both ChatGPT conversation/context limits and a tool-call limit during remote work, including a Colab GPU session that outlived useful chat control. Optimizing a few SSH commands is not enough. Our success measure is **verified useful work completed or safely stopped per AI-facing interaction**, with recoverable execution when the conversation ends.

The gateway cannot inspect ChatGPT's internal token accounting or bypass platform limits. Fewer MCP calls and less returned data may help, but actual platform improvement must not be promised without evidence. Keeping a GPU worker safe must not depend on a chat being available to issue its next call.

## Operating rules

1. **Do nothing by default.** Do not start unnecessary inspection loops, periodic scans, background analysis, remote shells, auto-refresh, or costly workers just because the service is connected. Work begins in response to a concrete user action or a separately authorized scheduled/conditional job.
2. **Use the shortest correct path.** Prefer one scoped inspection over repeated broad reads, choose an existing fast tool over spawning a new process when it produces the required result, and stop once the task's correctness condition is met.
3. **Avoid duplicate reads.** Reuse already available observations only when freshness is validated (mtime, size, hash, revision or equivalent). Never treat live-changing content as immutable merely to save a call.
4. **Return minimal sufficient data.** Default to a bounded status summary, changed counts, clear errors, a stable receipt, and a way to request more. Return full logs, entire file trees, or full tool output only when needed. Clearly mark truncation, omission, source timestamp and freshness.
5. **Bound everything.** Every remote operation or explicitly approved watcher needs a defined scope, operation timeout, output cap, retry cap and stopping criterion. No unbounded automatic retries, hidden work or command replay.
6. **Do not pretend a stopped chat stops a GPU.** Colab or other GPU jobs need independently enforced limits when the provider supports them, explicit stop/cancel controls, checkpoints and minimal durable receipts. If an independent stop limit cannot be enforced, warn before a paid workload starts; a Deck-side watchdog alone cannot guarantee shutdown during network failure.
7. **Protect continuity.** Persist only the minimal safe data to inspect and explicitly resume/cancel a job in a new chat: job identifier, authorized scope, state, timestamps, progress/checkpoint, last error and stop/cleanup receipt. Do not store secrets, prompts or private datasets in public telemetry.
8. **No feature sprawl.** Prefer one constrained read-only batch/inspection interface over many overlapping tools. Preserve standard operations for cases where detailed data are genuinely required.
9. **No optimization that changes semantics.** Preserve error reporting, correctness, no-overwrite semantics, hash validation, strict SSH host-key checking, user approvals and bounded costs.
10. **Keep public/private separation.** Only sanitized benchmark aggregates, technical design and test code go to GitHub. Real runtime records, Colab URLs, token-bearing configs, SSH identities, model files and project paths remain local.

## Four distinct efficiency dimensions

| Dimension | Measure | Must not confuse with |
|---|---|---|
| AI-facing interactions | MCP requests per successfully completed or safely stopped task; empty polls; duplicate reads | SSH subprocesses or internal backend RPCs |
| Context pressure | Returned UTF-8 bytes, serialized response bytes, measured tool-schema JSON size, rough estimated tokens | Actual ChatGPT context-token accounting (not observable inside the plugin) |
| Execution cost | Elapsed task p50/p95, gateway/backend calls, processes spawned, wasted retries, GPU runtime when independently reported | Low output bytes automatically meaning fast execution |
| Safety/recoverability | Explicit completion/stop receipts, bounded worker lifetime, ability to reconnect, correct result or safe failure | Background monitoring without guarantees |

## Audit at the start of this requirement

- November 12 synthetic baseline: 13 workflow variants × 5 trials = **65/65 correctness checks passed**, with observable differences in calls, output and execution time. That test was not a ChatGPT platform-token benchmark.
- November 15 read-only live gateway catalog measurement: **29 tool definitions; 65,896 UTF-8 bytes of JSON when serialized**. This is a surface-size measurement, **not** a claim that ChatGPT pays or retains that entire amount as tokens. Test compacting descriptions/schemas only if it preserves tool usability and correctness.
- Static source inspection found no direct `setInterval` periodic loop in the gateway and identified on-demand backend handling. This is **not** an audit of all tunnel, OS service, desktop/Windows or Colab background behavior.
- A previous measured delayed-process task required repeated short waits; Smart Wait reduced AI-facing calls in the isolated MCP benchmark without adding an always-on polling agent.

## Ordered next actions

1. **Phase 1B, instrumentation:** safely count client-facing, gateway→backend and SSH subprocess operations separately; measure tool-catalog size and responses. Keep private execution traces local.
2. **Read-only compact inspection prototype:** typed bounded batch requests for safe status/file metadata/log-tail operations; correct drill-down and truncation. Benchmark against the existing gateway with identical success criteria.
3. **Stale/unchanged response controls:** test freshness-aware read receipts and suppress duplicate data only when valid.
4. **Durable job supervision design:** submit / check status / explicit cancel / recover by job ID, independent worker time bounds and checkpoint receipts; failure-inject chat expiration, disconnect, restart and failed stop.
5. **Deploy selectively:** use measured before/after evidence, security review, rollback and regression tests. If no material improvement in the correct task, do not deploy.

## Definition of done

Compare representative workflows including short command, eight-file review, bounded log query, large folder metadata, delayed process, offline Windows, interrupted file transfer and a bounded synthetic long-running job. Report success rate, calls per safe outcome, returned bytes per safe outcome, latency p50/p95 and failures. Record backend/SSH counts separately. Show that tool-call reduction does not conceal failures, lose needed evidence or cause extra work in the background. **Do not invent a guaranteed percentage reduction in ChatGPT chat-limit use.**

## Scope-preservation note

The user's mention of a new efficiency goal **does not revoke earlier requested improvements**. Keep independent long-running execution, remote-worker/provider-side time limits, checkpointing, durable job receipts, explicit progress/status/cancel/recovery, connection resilience, safe file transfers, and verified GitHub history in scope. Workstreams and ordering are consolidated in the [Unified Engineering Plan](UNIFIED_ENGINEERING_PLAN.md). No background worker or GPU stopping capability is considered implemented until explicitly tested and deployed.

