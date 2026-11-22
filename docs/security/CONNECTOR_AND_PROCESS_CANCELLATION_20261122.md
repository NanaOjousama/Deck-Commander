# Deck-Commander — Connector authentication and process-tree cancellation gate

**Date:** 2026-11-22 (America/Toronto). **Stage:** isolated engineering only. No live tunnel, credentials, OS services, personal devices or customer gateway configuration were modified.

## A. Actual connector/client-authentication audit — **INCOMPLETE**

A read-only check found that the Deck-Commander connector can list a connected Steam Deck backend with 25 tools. The Windows endpoint was unavailable from that read-only check. A sanitized process inventory reported two running processes whose executable name is `tunnel-client`. No command-line arguments, environment variables, credential files, tokens, full private configuration, listening endpoints or customer data were read or exported.

This evidence **does not prove** the connector authenticates specific ChatGPT account identities, requires session consent, validates authorization before `tools/list`, or terminates a revoked client's existing connection. Those checks cannot be inferred from the local MCP stdio gateway, GitHub source, or successful tool access by the account already authorized.

**Required controlled future acceptance (separate from this implementation):** using disposable authorized/unauthorized test identities, demonstrate rejection before discovery, explicit single-client/session revocation, removal of an established connection, token rotation invalidating old tokens, and replay/reconnect refusal. The connector/tunnel provider's auth and account bindings must be documented without exposing usable secrets. **Do not request a live tunnel outage or rotate real credentials without separate approval.**

## B. Gateway-owned transfer-process cancellation — isolated implementation

The prior gateway used `spawn('/usr/bin/python3',...)` and on timeout called `child.kill('SIGKILL')`. This only targets the Python router PID; its SSH/SFTP descendants may persist. The gateway did not await an entire local process tree.

The isolated patch introduces:

- `process_tree.mjs`: run each transfer command in a separate POSIX session/process group (`detached:true` on Linux). Enforce the bounded timeout and output size; on timeout, cancellation, output limit, or normal parent exit, send SIGTERM to the group followed by SIGKILL after a small grace period. Do not replay uncertain operations. Pass the existing recovery-journal warning to the caller.
- `transfer_registry.mjs`: track each gateway-launched transfer's source/destination devices and operation name, with an `AbortSignal`. On a disabled device or an operation-specific permission revocation, cancel only affected active transfers. An unrelated denied tool name does not cancel an otherwise permitted transfer.
- `gateway.mjs`: use the group runner rather than directly spawning the router, reconcile running transfers on config-file changes and denied access, and abort/drain tracked transfers before graceful SIGTERM shutdown.
- New disposable Node tests exercise real parent/child/grandchild processes, SIGTERM-ignoring descendants, cancellation signals, bounded output, parent exit, unaffected unrelated processes, concurrent shutdown, device-specific revocation and independent tool-permission changes.

**Scope limitations:** These protections cover local transfer subprocesses launched by the gateway's `copy_*_between_devices` routes. They do **not** establish cancellation of arbitrary `start_process` work forwarded to upstream Desktop Commander, orphan cleanup after uncatchable `SIGKILL` or power loss of the gateway, remote commands that daemonize/new-session on the Windows host, existing SSH multiplexed sessions outside this process group, or a revoked external ChatGPT client. A real end-to-end MCP transfer timeout test on disposable VMs and a power-loss recovery test remain necessary before claiming complete cancellation.

## Security and release status

- Nothing has been merged into `main`, deployed to the user's gateway or Windows desktop, or added to Draft PR #6.
- The previous `security/gateway-access-20261122` branch remains the tested device-authorization baseline.
- The current cancellation work belongs on a separate isolated `security/process-tree-cancellation-20261122` branch with a dedicated GitHub Actions workflow.
- Not customer release ready. Remaining work includes external client identity/revocation verification, least-privilege accounts, fresh-device acceptance, dependency lockfile/SBOM, pairing migration and full crash/process cancellation tests.

**Project Sources:** Add one concise handoff after CI results are confirmed. Keep source, exact test logs and CI recipes in GitHub. Never add credential material or private tunnel configuration.
