# Deck Commander — canonical project handoff

**Last audited: 2026-11-16.** **Read this first when a new chat takes over.**

**Scope:** This document concerns **Deck Commander only** (our self-hosted Steam Deck + Windows MCP gateway). It is **not** a status report for the manga adaptation, Ty, Stardew RNG, or any other project. Never inspect or alter those projects while doing Deck Commander maintenance unless specifically requested.

## Source-of-truth rules

- **This page is the single canonical *project status and next-action* handoff.** Update it whenever scope, implementation status, key test evidence, priorities or material blockers change.
- [Unified Engineering Plan](UNIFIED_ENGINEERING_PLAN.md) is the **complete, retained scope** and priority framework. Keep all its workstreams; do not silently drop suggestions because the owner later discussed a different improvement.
- [AI Efficiency Contract](AI_EFFICIENCY_CONTRACT.md) states operating rules and measurable acceptance criteria.
- [Issue #2](https://github.com/NanaOjousama/Deck-Commander/issues/2) tracks call/context efficiency and safe remote-job supervision. [Issue #1](https://github.com/NanaOjousama/Deck-Commander/issues/1) tracks long-running transfer jobs and throughput.
- [Development Log](DEVELOPMENT_LOG.md) and [Benchmark Report](../benchmarks/BASELINE_2026-11-12.md) hold historical evidence. Old dated priorities are historical; this page plus the unified plan determine current next steps.
- **The actual private Steam Deck installation is authoritative for runtime configuration and operational state.** The public repository is a **sanitized source snapshot** and decisions ledger, not a byte-for-byte copy of the live installation. Keep secrets, host details, runtime journals and Colab URLs out of GitHub.
- Keep a **sanitized local copy** of this handoff at `docs/PROJECT_HANDOFF.md` on the Deck for offline continuity. Reconcile it with GitHub when updating either.

## Verified status (2026-11-16 audit)

| Area | Evidence | Status / caveat |
|---|---|---|
| Gateway | Live service queried read-only; `ActiveState=active`, `SubState=running`, `NRestarts=0` | **Working**; no restart or live gateway change made |
| Authorized devices | `list_devices` returned Steam Deck and Windows connected (25 upstream tools each) | **Working when audited**; device availability can change |
| Gateway surface | Prior measured 29 exposed tools; 25 upstream + 4 gateway tools | **Implemented**, not all operations individually validated |
| Regression suite | `PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -p 'test_*.py' -q` | **38 tests passed** locally |
| Transfer recovery | `pending-transfers`, `pending-folder-transfers`, `pending-resumable-files` each returned an empty list | **Zero pending transfer jobs**. Historical `.lock` files still exist; do **not** delete them merely because they exist |
| GitHub CI | Latest checked successful workflow on commit `6205fbc` | **Passed** when checked; verify new commits separately |
| AI-efficiency baseline | Synthetic 13 workflow variants × 5 samples, 65/65 correctness checks | **Completed**. Measures test-client calls/returned bytes/latency; **does not** measure real ChatGPT context tokens |
| Original vs gateway performance | Direct local upstream-vs-gateway test: 12 repetitions each of 3 tasks; all 72 calls succeeded | Basic operations roughly comparable. Gateway adds extra startup/tool catalog overhead; no proven global ChatGPT context savings |
| Colab control | Owner reported that another chat successfully ran Colab operations through Deck Commander | **User-reported working workflow**; connection method and currently running jobs were **not audited** here. No extra Colab setup or troubleshooting requested |

## Explicit user decisions (retain across chats)

1. **Use our Deck Commander by default.** Preserve the paid original Desktop Commander allowance as a **fallback** for verified capability/connection failures, not routine inspection. This does **not** bypass ChatGPT's separate context/tool-call limits.
2. The user's major pain point: a chat/tool limit interrupted remote control during a potentially billable Colab GPU workflow. Reduce AI-facing calls and returned context **and** build independent job safety so paid work is not dependent on a chat remaining open. Root cause of the previous ChatGPT limit is **unproven**.
3. **Keep *all* prior improvements in scope:** intelligent batching, compact output, change-aware reads, independent bounded execution, durable receipts/checkpoints, progress, explicit stop/cancel and recovery from a new chat, no unnecessary background activity, better large-folder transfers, reconnect resilience, tests, backups and GitHub history.
4. Owner reports **“Couldn't open app — Retry”** appearing in ChatGPT while work often continued. It may be an app/UI-opening error rather than backend failure; **not proven**. Do not interrupt healthy Colab work for the banner alone. If work stalls, tool execution fails, commands fail or connectivity drops, investigate.
5. **Do not reconfigure an already-working Colab connection** merely to troubleshoot theoretically. The owner explicitly said no setup change is needed now. Do not run a GPU or create paid sessions for Deck Commander tests without explicit approval.
6. Keep Steam Deck's private credentials, SSH host-key pinning and recovery journals intact; maintain clean separation from all other projects. No live deployment without isolated tests and rollback.

## Implemented versus planned — do not confuse them

**Implemented/tested:** Multi-device proxy; original Desktop Commander operations on both devices; Smart Wait; stale Windows reconnect safeguards; SHA-256/no-overwrite single-file transfers; per-file folder resume; byte-level SFTP resume; private recovery journals; 38 offline tests; sanitized GitHub source and benchmarks.

**Planned/not deployed:** General independent Colab/RunPod GPU execution controller; guaranteed remote shutdown or spend cap; universal cross-chat job recovery; new bounded read-only batch-inspection tool; change-aware compact-output endpoints; full gateway/backend/SSH call attribution; large-scale folder performance optimization. Do not describe these as operational features yet.

## Next actions, in dependency order

**0. Preserve stability before experiments.** Work in isolated source/tests; leave the active gateway, tunnel and any Colab jobs untouched. Record baseline/rollback. No scans or background jobs without a concrete purpose.

**1. Phase 1B — benchmark observability.** Instrument a *separate* gateway instance to distinguish ChatGPT-facing test-client MCP calls, internal backend RPCs, SSH subprocesses, schema bytes, response bytes, latency, retry/empty polls and task correctness. Measure what is visible; do not invent ChatGPT internal tokens or infer a quota cause.

**2. Read-only compact batch prototype.** Provide one carefully typed inspection endpoint for bounded file metadata/log tails/multiple safe inspections, with per-step limits, explicit failures, truncation and drill-down. Compare against original upstream DC and our existing gateway for identical verified tasks; use p50/p95 and output-per-success. Only deploy if it improves end-to-end useful work without loss of correctness or unreasonable latency.

**3. Independent execution safety design in parallel with 1–2.** Specify explicit submit/status/cancel/receipt/recover-by-job-ID, durable state and checkpoint location, bounded execution and worker/provider-side termination when enforceable. A stop *request* is not a verified GPU shutdown. Define failure injection for chat cutoff, gateway restart, Deck offline, lost worker and failed cancellation.

**4. Prove job control safely.** Run synthetic bounded jobs locally first. Test failure and recovery before touching a paid Colab/RunPod runtime. Secure approval and hard provider/resource constraints where possible before any paid test.

**5. Continue retained workstreams.** Large-folder (100/500/1000-file) throughput, SSH/process overhead, Windows reconnect/offline/IP changes, installer/maintenance, secret scanning and regression coverage. Preserve transfer safety semantics.

## Definition of done for any improvement

Correct work must be **completed or safely stopped**; no hidden side effects, indefinite retries, user-project reads, unauthorized paid jobs or speculative background activity. Show before/after calls, returned bytes, latency, failure rates, and a clear rollback. Publish only sanitized evidence. If the optimization is unproven, keep the working gateway unchanged.

## New-chat resumption instruction

> Read `docs/PROJECT_HANDOFF.md` in the Deck Commander GitHub repository first, then `docs/UNIFIED_ENGINEERING_PLAN.md` and Issues #1/#2. Verify today's actual gateway/test state before changing code. Continue **Phase 1B isolated instrumentation + compact inspection benchmark**, and design **independently bounded job supervision** in parallel. Do not touch active Colab sessions, unrelated projects, or the live gateway until a safe implementation has passed tests and has a rollback.
