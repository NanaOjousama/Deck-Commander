# Architecture

```text
ChatGPT / authorized MCP client
  └── separately configured secure MCP tunnel (not in this repo)
        └── gateway.mjs (MCP stdio)
              ├── steamdeck (default): upstream Desktop Commander MCP backend
              ├── windows (explicit): pinned-host-key OpenSSH to Windows launcher
              ├── list_devices + connection backoff + stale session recovery
              └── Python router.py (short-lived transfer command)
                    ├── transfer_reliability.py (single file)
                    ├── folder_transfer.py + folder_backend.py (per-file resume)
                    └── resumable_file_transfer.py (SFTP byte-level resume)
```

## Routing

`gateway.mjs` dynamically exposes the 25 upstream Desktop Commander tool schemas with an optional `device` argument. The default is `steamdeck`; Windows requires `device:"windows"`. Three additional transfer tools always require explicit source/destination device names. Backend connection failures are never automatically replayed as commands, because operations may be non-idempotent.

## Authentication and trust boundaries

All Windows operations originate from the Deck over OpenSSH using a dedicated client key and strict pinned host-key checks. The private SSH configuration is separate from the public source. The application is **not** an SSH server, VPN, public API, or authentication service. The secure ChatGPT-facing tunnel is separately configured and must not be exposed without authentication.

## Transfer records

Temporary remote files and private journals allow inspection of interrupted work. Standard copy performs verification then attempts cleanup on failure. Folder copy maintains a job manifest, and resuming verifies completed destination hashes. Byte-level resume checks a retained partial against the source prefix before using `reput`/`reget`. Final files are committed without overwriting existing destinations, and every successful result includes a final digest.

## Code vs deployment

Public source intentionally uses environment variables for Windows host/launcher and Deck backend location. Its deployment configuration, keys, host fingerprints, service/tunnel definition and authorized Windows installation are **not** stored in Git. The published code mirrors the production feature set but does not automatically reproduce the user's machine configuration.