# Deck Commander — unified engineering plan

**Updated November 15, 2026.** This is the **single tracking plan** that preserves **all** previously proposed improvements. It supersedes *priority order* in older notes, not their technical evidence. Detailed work continues under [Issue #2: AI efficiency](https://github.com/NanaOjousama/Deck-Commander/issues/2) and [Issue #1: transfer reliability](https://github.com/NanaOjousama/Deck-Commander/issues/1). Read alongside the [minimal-work contract](AI_EFFICIENCY_CONTRACT.md).

## The goal

Get more **correct, useful, safely completed work per ChatGPT interaction** while limiting conversation-context size, remote-call usage, execution time, wasted activity and risk when a chat closes mid-job. In particular, a Colab GPU session must never depend on ChatGPT still having tool calls to enforce its runtime budget **when independent enforcement is technically available**. We cannot claim a Colab termination guarantee without confirming a worker/provider mechanism.

**Explicit user requirement:** Keep the *entire* previously proposed roadmap. Prioritizing call/context efficiency does **not** cancel independent execution, safe recovery, transfer optimization, connection resilience, progress tracking, testing or maintainability.

## Workstreams — retained, with evidence and current status

| Workstream | Existing capability / evidence | Remaining engineering work |
|---|---|---|
| A. AI call efficiency | Smart Wait deployed; synthetic task benchmark: 65/65 correct across 13 variants; separate MCP-client call/response measurements | Phase 1B gateway/backend/SSH instrumentation; bounded native read-only inspection batching; fewer duplicate reads/empty polls; measure success per interaction |
| B. Conversation/context efficiency | Existing benchmark measures UTF-8 output bytes and serialized tool results; 29 exposed tools roughly 65.9 KB JSON schema | Compact status summaries, bounded output, change-aware reads, freshness receipts, deterministic pagination and opt-in drill-down; evaluate tool catalog consolidation without reducing discoverability |
| C. Independent long-running execution | Existing synchronous gateway and transfer journals; no general-purpose independently supervised job manager is claimed | Explicitly launched bounded jobs; separate worker not dependent on chat process; durable job ID and immutable work scope; independent timeouts/cost-stop capability when enforceable; progress/checkpoints/heartbeats; explicit cancel and verifiable termination |
| D. Reconnect / continuity after chat expires | Existing Windows reconnection and transfer-resume mechanisms; saved test/deployment records | A new chat can use a job ID to inspect state and checkpoints, get result/stop receipt, explicitly resume *only safe unfinished work* or cancel; no blind non-idempotent replay |
| E. No unnecessary work | Gateway static source has no apparent scheduled `setInterval` loop; on-demand request path | No auto background scans, hidden jobs, purposeless monitoring, unbounded polls or retries; optimize stop conditions, output size and retry budgets; inspect other components separately if evidence suggests waste |
| F. Transfers and large-folder throughput | Single-file hash/no-clobber, per-file folder resume, byte-level SFTP resume, SSH connection reuse, real Windows synthetic tests | Benchmark 100/500/1,000-file transfers, reduce process/SSH overhead, persistent transfer progress and cancel/resume across gateway timeout, integrity/failure-injection tests |
| G. Connection and network resilience | Bounded Windows DNS checks, stale-session handling, source-IP drift warning | Cold boot, network change and power-off failure tests; preserve strict host-key pinning, key-only authentication and firewall constraints |
| H. Quality, security, and records | Organized private Deck install, sanitized GitHub repository, 38 offline transfer tests, GitHub CI and history | Regression tests for context/calls/job lifecycle, safety review, staged deployment+rollback, secure setup tooling, secret scanning and dated benchmark/decision reports |

## Priority sequence (not a removal list)

**P0 — Design independent job safety alongside measurement.** Specify the minimum worker/provider-side execution contract, time bounds, stop/cancel behavior and checkpoint/receipt format *before* using a paid GPU workload as a test. Not all Colab session limits are enforceable externally: document the mechanism's actual guarantees and do not pretend a local watcher guarantees GPU termination during a network failure.

**P1 — Benchmark and reduce ChatGPT interaction/context cost.** Instrument observable MCP client, gateway/backend and SSH traffic separately. Build one small, typed **read-only** inspection-batch prototype with bounded results and compare task correctness, calls, response bytes and p50/p95 latency. Test change-aware reads; no invisible polling. Avoid proliferating overlapping tools.

**P2 — Implement and exercise the bounded durable job controller on synthetic tasks.** Explicit launch only, job ID, local durable receipt, max runtime, independently enforced kill where possible, status, progress, output/checkpoint location, cancellation, final disposition and recovery from another chat. Failure-inject timeout, gateway restart, Deck disconnect, expired chat, remote disconnection, failed cancel and partial output. Never claim a safe stop without a verified receipt.

**P3 — Adapt carefully to paid Colab/RunPod workflows.** Apply a proven controller only with clear user approval, pre-set resource/time/budget boundaries, a worker/provider-side stop mechanism, and clear limitations. A provider or session disconnection must not silently change the billed-resource assumption. Test stop and checkpoint semantics with small bounded workloads first.

**P4 — Finish transfer performance and resilience.** Benchmark real large-folder loads, optimize process churn, add per-job progress/receipts and explicit recovery; preserve all no-clobber and hash checks. Continue Windows offline/reconnect/IP-drift cases.

**P5 — Maintain and publish.** Offline tests, real-device smoke tests where applicable, before/after report, backups/rollback, sanitized GitHub history and a short “next step” handoff. Keep private credentials, tokens, notebook URLs and user project files off GitHub.

P0 and P1 can proceed together; independent job safety is **not** postponed indefinitely behind cosmetic optimization. Priorities can change on proven risk or measured bottlenecks, but none of the workstreams is canceled merely because a user didn't mention it again.

## Minimal job contract — what “independent execution” really means

A future job submission should carry: a unique ID, task description and allowed operation type, device/provider, resource/time budget, exact authorized scope, start deadline, output/receipt directory, checkpoint policy and stop method. The worker should record durable states such as `queued`, `running`, `checkpointing`, `completed`, `cancel_requested`, `stopped_verified`, `failed`, `expired` and `unknown_remote_state` (last state explicitly warns when no reliable stop receipt exists).

The controller must expose **submit, status, explicit cancel, retrieve receipts, and safe explicit resume** from a later chat, with minimal summary output and optional evidence expansion. A stop request is **not** proof of remote GPU termination. Runtime/cost limits must be enforced by the component actually controlling the paid resource where possible; otherwise state the limitation clearly before starting. No indefinite automatic retries or re-submission of uncertain commands.

## Acceptance criteria

1. Each measured optimization produces **at least equal task correctness**, with fewer calls or lower useful-response bytes and no unacceptable latency/safety regression. Show calls/bytes **per successful or safely stopped task**, not arbitrary per-command statistics; do not promise reductions in internal ChatGPT token quotas that Deck Commander cannot observe.
2. A chat-limit interruption should not prevent inspection of an already-created durable receipt in a new chat. The job's preconfigured stopping rules must not require a subsequent ChatGPT message.
3. No unapproved or purposeless background execution; every ongoing task has an owner-approved purpose, stop condition and bounded lifetime.
4. Transfer integrity, SHA-256 checks, no-overwrite semantics, host-key verification, local privacy and rollback remain intact.
5. Development is staged off the live gateway until tests and deployment checks pass. Evidence is recorded in GitHub without private operational details.

## Immediate next deliverable

A small **Phase 1B measured prototype** plus a **worker/job lifecycle safety specification**. Show what it saves, what it costs, whether it actually preserves correctness, and which remote-stop guarantees are still unavailable. Then decide what to deploy—do not add machinery simply because it is possible.
