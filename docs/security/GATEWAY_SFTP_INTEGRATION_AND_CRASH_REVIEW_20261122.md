# Deck-Commander — Gateway/SFTP runtime acceptance and abrupt-crash risk

**Date:** November 22, 2026 · **Project:** Deck-Commander only  
**Scope:** isolated security review and tests only. Not a release, connector identity verification, or live deployment.

## What was actually tested

The actual MCP stdio gateway code, with disposable fake MCP backends, invoked a disposable Python transfer router through the real Linux process-tree runner. The Python fixture performed an **actual file upload** through the local OpenSSH `sftp -D sftp-server` client/server pair on the Steam Deck, without network exposure. A synthetic source file and temporary directory were used.

1. **Transfer timeout:** an operator-configured shorter timeout caused a tool error advising recovery-journal inspection; the Python router, OpenSSH SFTP client and local SFTP server all terminated.
2. **Device revocation while transferring:** after verifying an upload completed and the router remained active, an atomic owner-only `devices.json` update disabled Windows. The gateway returned an interrupted-operation error and all three local processes stopped.
3. **Invalid timeout configuration:** values outside 250–310000 milliseconds or malformed strings fail fast before MCP operations begin. The normal/default maximum remains 310000 milliseconds; overrides can only shorten it.
4. **Existing regression evidence:** the earlier 23 Node/MCP process-tree/authorization tests and 115 Python tests were rerun in an isolated worktree, with no user machine state changed.

**Scope limitation:** the SSH/WIndows backend was a fake non-executing MCP fixture. The transferred bytes were handled by the real **local SFTP client and SFTP server** directly, not an authenticated network SSH session or a real Windows destination, and the Python router was a test fixture rather than the production transfer implementation. This validates the actual gateway dispatch, cancellation and process-group teardown path while avoiding contact with any personal device.

## Deliberate crash-risk probe (confirmed residual risk)

A separate disposable probe started the same router/SFTP process group from a temporary Node parent. After a successful SFTP upload, that parent was killed with **SIGKILL**, preventing all JavaScript cleanup hooks. The Python router, SFTP client and SFTP server were each still active after the parent died. The probe then issued a targeted SIGKILL to the synthetic test process group and independently verified cleanup.

**Interpretation:** the isolated process-group cancellation logic handles controlled timeouts, revocations and graceful shutdown, **not** an uncatchable death of the Node gateway. Group isolation is not lifetime supervision. A service manager/cgroup that owns the gateway **and** all its children is needed for stronger crash containment; do not assume merely setting `KillMode=control-group` is sufficient until that service configuration is exercised with a real abrupt kill and recovery receipts.

## Adversarial review notes / unproven cases

- **Pass in tested cases:** no automatic replay; no active Python/SFTP descendants remain after controlled timeout or revocation; unrelated processes are not targeted by process-group signaling (prior tests).
- **Open:** gateway `SIGKILL`/host power loss, child `setsid`/daemon escape, lingering SSH ControlMaster sockets, remote Windows commands that detach, and OS service/cgroup enforcement.
- **Open:** per-client connector identity and revoked-session termination. Local `devices.json` and the per-device allowlist cannot authenticate a different ChatGPT caller. A disposable test tunnel and two independently authorized test identities are still needed; do not inspect, revoke or rotate the live connector's credentials.
- **Open:** third-party review. This is an adversarial internal review plus executable tests, not a separate security auditor's approval.
- **Open:** reproducible lockfile/dependencies, least-privilege customer OS accounts, installer migration, fresh-device acceptance, signed releases and customer security documentation.

## Safe engineering follow-up

1. Review service-manager/cgroup deployment with a dedicated non-admin Linux OS account and bounded child ownership. Require evidence that abrupt gateway termination kills **all** gateway-owned local transfers while preserving durable recovery records; verify rollback and no impact on unrelated processes.
2. Build a **separate disposable connector** for two-identity authentication, denial before tool discovery, token rotation and active-session revocation. Never reuse live tunnel credentials.
3. Obtain a separate code/security review of the isolated branch and acceptance evidence before any PR consolidation. Keep PR #6 Draft and the production gateway unchanged.

Keep raw CI logs in GitHub and preserve one concise milestone handoff in Deck-Commander Project Sources. Never store real identities, client secrets, tunnel endpoints, SSH keys or machine-specific private paths in the handoff.
