# Development log — Deck Commander

This is the running **human-readable project record**, assembled on November 12, 2026 from the implementation and test reports. It is intended to make follow-up work easier without exposing machine credentials or private network configuration.

## November 10 — Two-computer foundation

The first milestone preserved the existing Steam Deck MCP service as the default and added an explicitly selected Windows backend over key-only SSH. The Windows OpenSSH host key was independently fingerprint-verified at pairing time; the Windows firewall was restricted to the approved LAN source. A small synthetic file was copied Steam Deck → Windows → Steam Deck, and SHA-256 hashes agreed.

The authorized tunnel then started the multi-device gateway rather than the original single-device process. The initial gateway exposed the upstream 25 Desktop Commander tools plus `list_devices` and single-file copy (27 total). Original tunnel configuration was backed up on the Deck. Already-open ChatGPT sessions could still cache an earlier tool catalog until refreshed.

## November 11 — Diagnose excessive output polling

An audit of a recent 1,000-call history found 187 `read_process_output` checks, of which **128 returned no new lines** (~68%). Not all were necessarily unnecessary. A five-second command started with a three-second timeout needed two ChatGPT calls to retrieve completion.

A smart-wait gateway adjustment was tested on Steam Deck and Windows: routine short `start_process`/offset-zero output-read waits were extended, with a ten-second cap and exemptions for some interactive commands. The five-second command completed in one tool call in the validation. This trades a possibly longer first wait for fewer repeated checks. Process IDs remained available for long-running work.

## November 12 — Local installation housekeeping

After the sanitized GitHub source release, the private Steam Deck working directory was reorganized for maintainability. **108 historical items** were moved into dedicated documentation, backups, logs, state, test and archive folders; the original 123 top-level items were reduced to 21 immediately after the moves. Live gateway/router files, pairing configuration, SSH keys and three recovery-journal directories retained their operational paths.

A private local manifest records the original/new paths and SHA-256 fingerprints. Two regression-test import paths were adjusted for their new `tests/` location. **All 38 offline transfer tests passed**, and fresh Steam Deck and Windows connectivity checks passed. No personal project files, network configuration or live secrets were changed or uploaded.

The **private** organizational layout and move manifest remain on the Steam Deck. The GitHub repository continues to contain only reviewed sanitized source, setup templates, CI tests and general development history.

## November 12 — Reconnection and network robustness

- **Session recovery:** on Windows MCP transport closure, the stale client is discarded; the next explicit request reconnects. Interrupted commands are **not** automatically replayed.
- **Offline cooldown:** after failed Windows contact, subsequent checks in the short retry window return rapidly instead of launching SSH repeatedly.
- **Hostname timeout:** an offline Windows `.local` lookup previously took ~10.1 seconds; a bounded lookup reduced the first offline response to ~3.6 seconds in tests (about 64% less first-offline wait). This is not a claim about all commands.
- **Deck IPv4 drift notice:** an advisory warns when the Deck's source IP no longer matches the firewall's last-known allowed IP. It cannot repair the remote firewall or DHCP reservation by itself.
- **SSH multiplexing:** repeated successful remote commands on an authenticated reused connection were ~0.26–0.34 seconds each versus an initial connection around six seconds in one test. SSH host-key verification remained enforced.

### Single-file reliability

New copy logic creates randomized `.dc-part-*` temporary destinations, verifies SHA-256, checks that the source has not changed, and refuses to overwrite an existing final destination even on a race. Failed transfers try to remove only their own partial; if cleanup is impossible, a private record remains for inspection. **18 simulated tests passed** before deployment.

### Folder copy and per-file resume

Recursive folder copies preserve empty directories, reject symlinks, invalid names and case-insensitive collisions, and maintain a private manifest. An explicit resume verifies every existing destination file before skipping it; incomplete files are sent afresh. **20 offline folder tests** passed after hardening. A simulated **144-file** interruption resumed correctly by verifying/skipping 71 complete files and copying the remaining 73.

A real Windows validation initially timed out after two of three test files because each file required repeated SSH operations. After enabling SSH multiplexing, the interrupted Deck → Windows folder copy resumed in **~4.5 seconds** (two files skipped and one copied). The clean Windows → Deck copy completed in **~4.0 seconds**, and all three file hashes matched; an empty directory was preserved.

**Bug discovered and fixed:** an orphaned `.dc-part-*` from the earlier timeout was included when recursively copying the Windows folder. The inventory now rejects unresolved DC-owned partials rather than accidentally treating them as user content. That synthetic partial was verified and cleaned, and the round trip was repeated successfully.

### Large-file byte-level resume

A separate `copy_resumable_file_between_devices` operation was added rather than changing the legacy copy semantics. It records the exact endpoints, a source checksum and a random partial path. A resume requires an unchanged source and a prefix hash match before SFTP continues from the byte offset. The commit still refuses overwrite and verifies SHA-256.

Real Windows checks used a **2 MiB synthetic file** and **128 KiB partial**, both directions. Built-in resume verified/reused 131,072 bytes and transferred the remaining 1,966,080 bytes, with matching final SHA-256. Fresh start passed in both directions. Mismatched prefixes, changed source, unrecognized resume job, and pre-existing destination were rejected. The original single-file tool was retested against Windows after the final deployment.

**End-of-day state:** the private gateway was active with **29 tools**; its code and router were backed up locally; all three types of recovery journal reported zero unfinished jobs. Only disposable test files were used for all transfer validation. An already-open chat may still show 27 cached tools until app catalog refresh.

## Test coverage and reproducibility

`tests/test_transfer_reliability.py` and `tests/test_folder_transfer.py` provide **38 offline tests**. They cover successful transfers, corruption, interruptions, path traversal, symlinks, no-clobber races, immutable source checks, private journals, crash evidence, and verified resume. The GitHub Actions workflow runs those offline tests and checks JavaScript syntax.

Hardware-dependent smoke tests (real SSH, SFTP, tool-catalog refresh, power-on timing, firewall/DHCP behavior) require a paired lab computer and are intentionally not executed in public CI.

## What wasn't done

- No blanket synchronization, automatic file overwrite, blind retry of commands, or automatic deletion of recovery data.
- No public posting of SSH credentials, auth tokens, real known-host fingerprints, tunnel secrets, or machine-specific configuration.
- No editing or relocation of unrelated user projects or datasets.
- No promise that all upstream 25 operations have been exhaustively tested on Windows.

## For the next work session

Start at [Status and roadmap](STATUS_AND_ROADMAP.md). Confirm the live gateway and router revisions, check unresolved transfer journals, and test the exact operation before changing production files. Keep backups outside the Git repository and never upload local operational configuration.