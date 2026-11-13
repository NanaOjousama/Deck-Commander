# Deck Commander

A multi-device [Model Context Protocol (MCP)](https://modelcontextprotocol.io/) gateway that routes Desktop Commander operations to a **Steam Deck (Linux)** and an explicitly selected **Windows** computer, with verified file and folder transfers.

> **Status: experimental source release (November 12, 2026).** The original installation was validated on its paired machines. This repository is a **sanitized source snapshot**, not a one-click public installer. Setup requires an existing ChatGPT-compatible MCP tunnel, a paired Windows OpenSSH Server, a Windows MCP launcher, and a private local configuration. Never expose a raw command-execution MCP endpoint to the public internet.

## Features

- **Device-aware tools:** 25 upstream Desktop Commander actions routed to the Steam Deck by default or Windows when explicitly selected, plus `list_devices` and three transfer tools (**29 total** in the current gateway).
- **Low-poll process waiting:** bounded waits for common short commands, while long-running processes still return a PID.
- **Windows recovery:** stale-session reset, offline retry cooldown, and bounded `.local` hostname lookup. No blind replay of commands.
- **Network diagnostics:** optional warning if the Deck's current Wi-Fi IPv4 differs from the Windows firewall's last-known permitted source IP.
- **Reliable single-file transfer:** SHA-256 verification, no-overwrite commit, cleanup of failed temporary files when reachable, and private recovery records.
- **Folder copying:** recursive copy with SHA-256 verified **per-file resume**, explicit `resume` flag, safe filename/symlink rules, empty-directory preservation, and interruption journals.
- **Byte-level resume:** explicit `resume` for one large file via SFTP `reput`/`reget`, source and partial-prefix hash checks, and no-overwrite commits.
- **SSH session reuse:** optional OpenSSH multiplexing through a private control socket to reduce repeated authentication overhead.

## Quick navigation

| Document | Purpose |
|---|---|
| [Development log](docs/DEVELOPMENT_LOG.md) | Chronological record of what changed, tests, and benchmarks |
| [Current status & roadmap](docs/STATUS_AND_ROADMAP.md) | What works, known limits, and upcoming work |
| [Architecture](docs/ARCHITECTURE.md) | Gateway, SSH backend, transfer journal, and call flow |
| [Setup and safety](docs/SETUP.md) | How to configure a private deployment without publishing credentials |
| [Security](SECURITY.md) | Network exposure, secrets, and responsible handling |
| [Changelog](CHANGELOG.md) | Versioned summary of milestones |

## Running the code

**Do not use repository config examples as live trusted credentials.** Put real SSH keys, `known_hosts`, `ssh_config`, `devices.json`, and any tunnel configuration **outside this Git checkout** in a private deployment directory. Install dependencies using a supported Node.js release; Node 24 and Python 3.13 were used during development.

```bash
npm install
python3 -m unittest discover -s tests -p 'test_*.py' -v
node --check gateway.mjs
```

The router's offline self-test expects a privately paired configuration and key, so it is **not** part of public CI until pairing is configured. See [Setup](docs/SETUP.md) for environmental settings such as `DC_HOME`, `DC_WINDOWS_HOSTNAME`, and `DC_WINDOWS_LAUNCHER`, plus examples in `config/`.

For a configured private installation, the router offers:

```bash
python3 router.py devices
python3 router.py selftest
python3 router.py pending-transfers
python3 router.py pending-folder-transfers
python3 router.py pending-resumable-files
```

The MCP gateway serves stdio via `node gateway.mjs`. The **MCP tunnel or ChatGPT app registration is external to this repository**; the user's existing tunnel remains separately managed. The source does not grant remote access by itself.

## Transfer semantics

- `copy_file_between_devices`: ordinary single-file copy, SHA-256 verified and no overwrite.
- `copy_folder_between_devices`: fresh folder copy or explicit `resume:true` to skip completed files **only if their hashes match**. A partially copied individual file restarts from zero in this mode.
- `copy_resumable_file_between_devices`: new single-file copy or `resume:true` to continue bytes already present in a unique recorded partial, after prefix validation.

Never assume a transfer succeeded from the absence of an error alone. Check the returned result and file hashes. Important operations are best rehearsed on disposable files.

## Public-repository boundaries

This release **does not** contain working SSH private/public keys, known-host entries, authentic host fingerprints, machine-specific IPs/usernames, tunnel tokens, service credentials, runtime data, deployment backups, or personal manga/AI project files. These remain on the authorized machines only. Do not add them in future commits.

## License

No open-source license has been selected yet. Source is published for review; redistribution and reuse are not automatically granted beyond applicable law. The upstream Desktop Commander and MCP dependencies retain their own licenses.