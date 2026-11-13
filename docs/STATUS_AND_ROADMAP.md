# Status and roadmap

**As of November 12, 2026** — sanitized public source snapshot; the separately operated private gateway was verified active with 29 tools and zero pending transfer journals after final testing.

| Area | Current evidence | Remaining work |
|---|---|---|
| Steam Deck default tools | Working; original upstream 25 tools proxied | Test unexercised upstream operations when needed |
| Windows routing | Real SSH/MCP access validated | Measure cold-boot and network-change behavior |
| Smart wait | Five-second command completed in one tool call | Quantify real long-term call savings and interactive edge cases |
| Offline recovery | Simulated backend failure and real Windows-off responses passed | Test unexpected router/network drops over longer periods |
| IP-change diagnostics | Warns when Deck's current IP differs from configured allowance | Router DHCP reservation or narrowly scoped Windows firewall adjustment requires explicit network access |
| Single-file copy | Real Deck ↔ Windows SHA-256 verified | Larger-file stress and remote failure recovery testing |
| Folder transfer | Real bidirectional small folder and interrupted per-file resume passed | Reduce per-file SSH/Powershell overhead on thousands of files |
| Byte-level resume | Real 2 MiB uploads/downloads from 128 KiB partials passed | Larger-scale interruption tests and bounded long-running execution |
| GitHub distribution | Sanitized source, tests and docs | Build a guided installer and a secure configuration migration path |

## Recommended next development order

1. **Long-running transfers:** design a detached, bounded job manager with explicit cancellation, progress reports, and recovery after gateway timeout. Avoid blind automatic retries or invisible background work.
2. **Large-folder throughput:** benchmark batches and reduce excessive per-file PowerShell and SFTP process creation while retaining SHA-256 verification and no-clobber guarantees.
3. **Cold-boot/IP resilience:** test reconnect from fully powered-down Windows and a deliberately changed LAN address, without weakening SSH host-key validation or firewall restrictions.
4. **Setup tooling:** create a repeatable installer with explicit permission prompts, readable logs, credential-safe backups and clear rollback paths.
5. **Maintenance:** automated secret scanning, dependency updates, integration tests, release tags and upstream license review.

### Acceptance rules

Each change should have an offline regression test, an isolated integration test where feasible, a before/after measurement, a non-destructive rollback, and documented limits. Do not spend setup complexity on an optimization without evidence of user benefit.

## AI efficiency benchmark — Issue #2 (initial baseline)

The [first repeatable synthetic MCP-client benchmark](../benchmarks/BASELINE_2026-11-12.md) has been run: 13 strategies × 5 repetitions, all 65 task results passed. It measures client-facing tool calls, response UTF-8 bytes, response serialization size, latency p50/p95, failures, empty polls and repeated file reads. It **does not** expose true ChatGPT token counts or internal gateway→backend/SSH subprocess totals.

Initial evidence: an eight-file narrow review can be summarized in one call rather than eight, while a one-line log answer can return 136 bytes instead of the entire 22 KB log. However, compact subprocess-based directory counting was **slower** than native listing. **No live plugin optimization has been deployed from this benchmark yet.** Next: benchmark safe native batching/compact response prototypes, freshness-aware reads, and add backend instrumentation before choosing a deployment.

