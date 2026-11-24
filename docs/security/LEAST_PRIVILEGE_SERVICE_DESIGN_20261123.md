# Deck-Commander — Least-privilege service design (isolated)

**November 23, 2026.** Design and disposable GitHub CI acceptance only. **No production installer, deployment, account change, tunnel restart, or release approval.**

## Verified prior foundation

Branch `security/systemd-crash-containment-20261122`, commit `2c5a8ce081567928135ebfe29be463f0a9673cdc`, proved that systemd `KillMode=control-group` can clean up a temporary gateway + Python/SFTP worker group when the gateway process receives SIGKILL. Earlier work proved controlled timeouts and active device revocation. The remaining practical issue was defining how a **connector** should own the stdio MCP gateway under a service without storing code and private SSH material in the same directory.

## Code, state and process boundaries

- Existing personal setups: `DC_HOME`-only remains backward-compatible. **No automatic migration.**
- Proposed customer-style installation: `DC_APP_ROOT=/opt/deck-commander` for immutable root-owned source and dependencies; `DC_HOME=/var/lib/deck-commander` for private service-owned SSH config, host-key pins, device policy, journals and recovery records. `gateway.mjs` uses `DC_APP_ROOT` for code/entrypoint and `DC_HOME` for `ssh_config`, `devices.json` and router state. `router.py` already reads state from `DC_HOME`.
- In `DC_STRICT_DEPLOYMENT=1`, the gateway refuses missing/relative/same code and state roots, service-owned or writable code, non-private/wrong-owner state, symlinks and absence of `DC_REQUIRE_TOOL_ALLOWLIST=1`. The read-only preflight additionally verifies source/config ownership and explicit device allowlists; it must execute as the dedicated service user so private 0700 state stays inaccessible to other accounts. These checks are not a sandbox against a privileged OS account.
- The separately authenticated **connector** is the systemd service's main process. It must spawn the MCP stdio gateway as its child in the **same** cgroup. Starting `node gateway.mjs` as a stand-alone systemd daemon would sever the connector's stdin/stdout MCP transport; do not do that.
- Proposed unit: dedicated `deckcommander` non-admin/no-login user, root-owned executable connector wrapper, `StateDirectoryMode=0700`, `UMask=0077`, `ProtectSystem=strict`, `ProtectHome=true`, `NoNewPrivileges=true`, capability drop, `KillMode=control-group`, `SendSIGKILL=yes`, short `TimeoutStopSec`, **`Restart=no` pending recovery acceptance**.
- `service/render_service.py` only generates a review-only unit. It does not install, enable, start, edit an existing service, configure authentication or accept shell fragments. The connector entrypoint must be an existing, separately reviewed executable; no supplier-specific tunnel command is invented.

## Acceptance in CI

A GitHub-hosted ephemeral Ubuntu runner will create a temporary system service account, root-owned app tree and executable test connector, and a private systemd-managed state directory. It runs a **non-networking connector stand-in**, launches a gateway process fixture using the actual production `runIsolatedProcess()`, completes a synthetic upload through actual **local** OpenSSH `sftp -D sftp-server`, verifies all five local processes are in one cgroup, then sends SIGKILL to the connector main PID.

Successful acceptance requires **all five** processes to exit, unrelated work to survive, synthetic uploaded data and a `commit_unknown` receipt to persist, correct app/state permissions, and the read-only preflight to pass. The test deletes only randomly named CI resources. It does not access any personal device, Windows endpoint, real network tunnel, secret, account or release channel.

## Unresolved release gates

1. **Real connector auth/session revocation:** requires an independently authorized disposable connector/tunnel with two client identities. The fake connector in CI **does not prove** this. Account binding, denial before `tools/list`, established-session disconnect, token rotation and rejected reconnection remain open.
2. **Actual installer/uninstaller and migration:** design exists, but no install/enable/uninstall/rollback on a fresh non-CI machine has been approved or demonstrated; strict mode must not be enabled on the user's live system without explicit migration.
3. **Production journal integrity after power loss:** synthetic receipt durability is weaker evidence than a real interrupted production transfer and post-reboot reconciliation. Restart defaults to `no` until safe recovery is established.
4. **Independent security review:** separate reviewer/human evaluation of new service layout, executable ownership, malicious paths, supply chain, service-account capabilities and tunnel boundary still required.
5. **Scope of process cancellation:** unrelated upstream Desktop Commander detached jobs, remote Windows commands that escape their cgroup, separate tunnel processes outside the unit and kernel-uninterruptible operations are not proven contained.

Keep this implementation and CI receipts on a dedicated security branch; **do not merge, publish a one-click installer, or alter the live gateway** based only on this gate.
