# Deck-Commander — Gateway device access and revocation boundary

**Status: isolated implementation and disposable tests only. Not deployed, merged, approved for release, or a substitute for verified client authentication.** 2026-11-22.

## Actual trust boundary

- `gateway.mjs` is an MCP **stdio** process started by a trusted parent. It does not receive a verified end-user or per-client principal from the MCP messages it handles. **It cannot independently authenticate separate remote clients.**
- The ChatGPT-facing tunnel/connector is a **separate deployment component**. Authentication, session ownership, account access, token revocation and network exposure controls must be inspected and validated there. Do **not** expose high-privilege MCP command tools through an unauthenticated public endpoint or embed a shared token in ordinary tool arguments.
- On the Linux gateway machine, both upstream Desktop Commander and the Windows SSH route have the privileges of the OS user running them. A tool-name allowlist **does not sandbox `start_process` or other command-capable tools**. Least-privilege OS accounts, restricted filesystem access and independently verified host keys remain required.

## Source protections in this isolated branch

1. `gateway_access.mjs` validates `steamdeck`/`windows` explicitly; rejects `__proto__`, `constructor`, `toString`, unknown and non-string devices; rejects malformed operation arguments.
2. The gateway re-reads the *owner-private* `devices.json` through `O_NOFOLLOW`, verified regular-file ownership/mode and bounded UTF-8 JSON on **every tool call**, including calls using an already-connected backend. Both ends of transfers are checked. A malformed, insecure or unavailable Windows config denies access; the historic single-device default remains allowed only when no config exists and strict tool policy is disabled.
3. `windows.enabled:false` prevents **new** Windows tool operations and disconnects the cached backend. A best-effort filesystem watcher also closes idle Windows sessions following a config change. The gate is rechecked after asynchronous connection/probe steps, before tool dispatch.
4. Optional per-device `allowed_tools` lists restrict tool names at the **gateway instance**. Set `DC_REQUIRE_TOOL_ALLOWLIST=1` to fail closed if a device lacks a list; that is the recommended customer-security development setting. Existing personal single-user configurations remain legacy-compatible if the setting is absent. The same permission check applies to both devices for transfer tools.
5. All access checks are independent of an asserted `device` name's prototype properties. Device selection is never authorization by itself.

**Important operating precondition:** `devices.json` must be a regular file owned by the gateway process user with mode `0600`, not a symlink. This is stricter than older configurations. On Linux use a private gateway directory (`0700`) and owner-only config; verify before future deployment. Pairing writes staged private files but old manually copied configurations may need a secure migration.

## Revocation guarantees and limitations

- **Tested:** after revoking Windows in an atomically replaced config, subsequent calls deny and an already-cached *idle* mock backend is closed without a new tool request. Hostile device names and a removed tool permission are rejected by a real MCP test client; denied requests do not reach mock backends.
- **Not guaranteed:** cancelling already-started remote processes, child SSH/SFTP work, detached commands, network sessions outside this MCP process, or immediately revoking a malicious client still authorized at the external tunnel. `fs.watch` is best-effort; request-time policy checks are authoritative for **new dispatch only**. Commands already executing may have effects despite revocation. Do not replay interrupted commands automatically.
- The unit tests use temporary files; the end-to-end MCP test uses a real gateway process and **fake non-executing backends**. It never touches a live Deck, Windows SSH server, tunnel or user files.

## Required additional commercial-client gate (still OPEN)

1. Obtain a **read-only, sanitized** description of the actual connector/tunnel authentication, account binding, allowed callers, gateway process ownership, and how remote access is revoked. Verify controls at that boundary without requesting or printing usable secrets.
2. Prove an unauthorized connection fails **before** MCP tool discovery and that removing a client account/session prevents new connections. Verify token rotation and old-session termination using disposable identities.
3. Establish a **dedicated, non-admin OS account** and filesystem scope for customer gateways and a restricted Windows SSH account, with consent and audit/retention policy. Treat any command runner as full authority within that account's privileges.
4. Test forced cancellation of a running tool and its SSH/SFTP child-process tree, including simultaneous revocation. Disable access at the external tunnel and host firewall/authorized_keys when emergency revocation is needed; editing `devices.json` alone is not a full emergency shutdown.
5. Reconcile into a reviewed PR, then test a fresh private customer-like installation. Do not merge, deploy, or claim release readiness from unit tests alone.

## Secure operation / rollback

For a suspected compromise, first disable the **external connector/tunnel's authorization**; then terminate the gateway and any existing worker/SSH children, revoke/rotate the paired Windows SSH credentials if relevant and restrict host access. Mark `windows.enabled:false` in the **private** `devices.json` using an atomic owner-only file update. Verify the connection is refused. A config flag alone does not cancel already-running remote work.

To roll back the isolated source work, reset **only** the dedicated development branch to its recorded parent commit. The live production gateway and `main` are not changed by this implementation.
