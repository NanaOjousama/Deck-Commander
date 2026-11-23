# Deck-Commander — systemd crash containment acceptance

**2026-11-22. Isolated, disposable CI only. Not a customer deployment, external pen test or release sign-off.**

## Previously reproduced risk

Gateway-owned transfer subprocesses run in independent POSIX process groups. On a controlled timeout or configuration revocation, the gateway signals and reaps that group. But an uncatchable `SIGKILL` of the gateway main process bypasses its JavaScript cleanup. A prior disposable experiment proved the Python router, OpenSSH SFTP client and OpenSSH SFTP server remain alive when their Node gateway parent is killed this way.

## Proposed containment boundary

A dedicated Linux systemd **service** should own the gateway *and all its descendants*, so all gateway-owned transfer workers remain in its cgroup even if they spawn a separate process group. Configure `KillMode=control-group`, `SendSIGKILL=yes`, bounded `TimeoutStopSec`, and explicit restart policy. For this acceptance test we use `Restart=no` to avoid automatically replaying ambiguous transfer operations. The production restart policy must be reviewed separately, with durable transfer reconciliation. See `systemd.kill(5)` and `systemd.service(5)`.

The integration test starts a random **transient system service** using `systemd-run` on a disposable GitHub-hosted Ubuntu runner, under its unprivileged runner account; no service is installed, enabled, deployed or configured on the owner's Steam Deck. A temporary Node process uses **the repository's actual `runIsolatedProcess()`** to launch a disposable Python router, which completes a synthetic upload via the real local `sftp -D sftp-server` client/server pair. SSH networking, personal paths, Windows hosts, live gateway settings, tunnel secrets and paired credentials are not used.

## Acceptance criteria

1. Service is `Type=exec`, `KillMode=control-group`, `Restart=no`, `SendSIGKILL=yes`, with explicit short stop timeout.
2. Node gateway fixture, Python router, SFTP client and SFTP server are all alive and **all in this one service cgroup**, even though the router has its own POSIX process group.
3. The synthetic SFTP upload completes and is verified. A separate synthetic `commit_unknown` receipt exists before the crash.
4. Test sends **SIGKILL to the service's main Node process only**. Systemd must stop all remaining service-group workers without requiring JavaScript callbacks.
5. An unrelated synthetic process remains alive. The recovery receipt's content and uploaded synthetic bytes remain intact.
6. A `finally` cleanup targets **only** the uniquely named test service; emergency cleanup verifies membership of the disposable cgroup before signaling any PID.

The integration should **fail closed** if systemd/cgroup support is unavailable or if one worker survives.

## What this does NOT prove

- Remote Windows jobs that intentionally detach, SSH multiplexing that has already escaped the service, tasks moved to another systemd unit or cgroup, kernel D-state workloads, forced power loss or disk corruption.
- Safe automatic restart, idempotent transfer reconciliation, rollback or crash consistency of the *production* journal (the receipt in this test is synthetic).
- Least-privilege customer installer, user identity authentication in the external connector, credential rotation or service account isolation on personal hardware.
- A fully secured service unit ready for distribution. Design/implementation of installer and non-admin account migration remain separate work.

**Operating decision:** Do not change any live service. Do not claim that the original process-group patch alone closes sudden-crash risk; only this distinct cgroup-supervision acceptance can support that narrower claim once GitHub CI passes. Keep the code/tests/CI evidence on the isolated security branch and summarize verified results in one Project Sources handoff.
