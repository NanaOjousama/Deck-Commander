# Private setup guide (manual, experimental)

This is a **developer-oriented checklist**, not a secure one-command installer. Only connect devices you own or administer. The existing user's working setup was configured independently of this GitHub repository.

## Prerequisites

A Steam Deck or another Linux machine with Python 3.13 (tested), Node.js 24 (tested), OpenSSH client and SFTP; a Windows machine with OpenSSH Server; the `@wonderwhy-er/desktop-commander` backend on both machines; and an authenticated MCP tunnel/connector configured separately.

## Keep credentials outside the repo

Use a private installation directory, for example `~/.local/share/deck-commander-multidevice` with mode 0700. Create `keys/` (0700), the client private key (0600), a trusted `known_hosts` (0600), `ssh_config` (0600), and `devices.json` (0600). Populate them **only from independently verified pairing**, never from values found on the internet or GitHub. Do not put tunnel tokens in `.env` inside your Git checkout.

A configuration example lives in `config/devices.json.example`; SSH examples are in `config/ssh_config.example`. These are not functional without your own approved host/user/fingerprint/key details. The Windows script `config/windows-start-dc.cmd.example` is a launcher template and requires adjustment to the actual installed backend.

The `pair_windows.py` helper expects an already-created client key and known-host file. Its `--host-fingerprint` must come from a trusted check on Windows, not a remote scan alone. Treat it as an advanced helper, verify its effects in a lab, and avoid open inbound SSH firewall exposure.

## Environment variables

| Setting | Meaning |
|---|---|
| `DC_HOME` | Private installation folder containing `router.py`, `ssh_config`, `devices.json` and keys |
| `DC_WINDOWS_HOSTNAME` | Paired Windows LAN hostname for bounded IPv4 lookup (e.g. `workstation.local`) |
| `DC_WINDOWS_LAUNCHER` | Absolute Windows `.cmd` launcher path, supplied to `cmd.exe /c` |
| `DC_STEAMDECK_NODE` | Optional absolute Node binary path; defaults to current Node executable |
| `DC_STEAMDECK_ENTRYPOINT` | Optional upstream backend entrypoint; defaults to `DC_HOME/node_modules/...` |
| `DC_DECK_NETWORK_INTERFACE` | Wi-Fi network interface name (default `wlan0`) |
| `DC_DECK_FIREWALL_ALLOWED_IPV4` | Optional advisory: last-known Deck IP permitted by Windows SSH firewall |

`gateway.mjs` reads environment variables from its process. It does **not** parse `.env` itself. You must pass them through an appropriately secured service environment or shell without exposing secrets.

## Development tests

```bash
npm install
PYTHONPATH=. python3 -m unittest discover -s tests -p 'test_*.py' -v
node --check gateway.mjs
```

These offline checks require no Windows device and should pass before attempting a real transfer. After private pairing, run `python3 router.py selftest`, then a small disposable file-copy round trip and compare SHA-256 hashes. Only after those pass should the MCP gateway be switched over, with a verified rollback configuration.

## ChatGPT tool list

The gateway advertises 29 tools in this source version. A previously opened ChatGPT chat can hold an older cached connector schema. Refresh/reconnect your custom DC plugin and start a new conversation if newly added tools are missing. Avoid reinstalling the connector unnecessarily if the live backend is working.

## Operations and rollback

Maintain timestamped backups of your private gateway/router and tunnel configuration **outside Git**. Do not restart or reconfigure the network while your only access path depends on it. Transfer errors may leave private journals; inspect them with `pending-transfers`, `pending-folder-transfers`, and `pending-resumable-files`. Never delete an unknown partial or overwrite destination content merely to make a retry succeed.