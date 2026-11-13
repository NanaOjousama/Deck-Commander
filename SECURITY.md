# Security policy and data boundary

Deck Commander can execute commands and read/write files on trusted machines. Treat its MCP and SSH endpoints as **high-privilege interfaces** and limit them to the authorized user and trusted network.

## Never publish

- SSH private keys, usable public key identities tied to a private installation, authenticated `known_hosts`, actual fingerprints, machine usernames/LAN IPs, tunnel tokens, session IDs, endpoint URLs with credentials, passwords, `.env` with real values, service config carrying secrets, deployment backups, runtime journal contents or user project files.
- This repository's `config/*.example` files are placeholders only. `.gitignore` is a guardrail, **not** a substitute for reviewing all changes before commit.

## Safe operation

Use a dedicated SSH key, strict known-host verification, a local-only Windows SSH firewall allowlist, and an authenticated remote MCP tunnel. Do not open Windows SSH to the public internet. Respect consent for remote actions. Keep Windows credentials and router settings entirely outside the public checkout. Do not replay commands after ambiguous network failure; they may already have run.

Transfers validate hashes and refuse overwrites; this is not a substitute for backups. If a checksum mismatch or crash occurs, inspect the private recovery journal and partial rather than deleting arbitrary files. This software has not received an external security audit.

## Reporting problems

Open a GitHub issue for non-sensitive defects. **Do not post keys, host fingerprints, private paths, tokens, service logs, or exploit secrets in public issues.** For sensitive reports, use a private contact method made available by the repository owner; if none is configured, do not post sensitive details publicly.