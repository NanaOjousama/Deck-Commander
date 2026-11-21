# Deck-Commander — Cross-check of Gemini and Claude Security Audits

**Date:** November 20, 2026  
**Source:** `NanaOjousama/Deck-Commander`, branch `hardening/customer-installation-security`, commit `d13d3c0351965af9d7aa63a0c9cc94a0f446c115`  
**Scope:** Read-only assessment and disposable fixture checks; **not** a live tunnel/Windows penetration test or an approved software release.

## Bottom line

Gemini's attached 16-page report performed a genuine source-based assessment; it is substantially more useful than the previous ZIP metadata report. Claude also performed two read-only audits. The reports are not mutually exclusive: Gemini caught a Windows-disable control failure that Claude missed, while Claude caught two concrete crash-recovery bugs that Gemini's priorities did not cover.

**No evidence of an actual compromise or a confirmed internet-reachable unauthenticated gateway has been established.** However, code-level flaws exist. Do not ship a customer version until fixes, regression tests, separate tunnel authentication assessment, and fresh-device testing are complete.

## Source-backed findings, with evidence quality

| Priority | Issue | Evidence | Practical meaning |
|---|---|---|---|
| P0 | PowerShell path injection from Unicode curly quotes (`router.py:53-56`) | Real code returns single-quoted path without escaping U+2019. Microsoft's PowerShell documentation says curly quotes act as quotation marks; related vulnerability CVE-2026-71312 is **in rclone, not Deck-Commander**. No Deck-Commander Windows exploit executed. | A cleverly named file can turn data into executable PowerShell syntax during copy/list/hash; block untrusted transfers until safely encoded and tested on disposable Windows. |
| P0 | Windows disable/paired gate is not enforced by gateway (`gateway.mjs:102-150`, `pair_windows.py:111-116`, `router.py:27-31`) | Verified current checkout: the gateway has zero `devices.json` references; the router does check `windows.enabled`. | On a configured working connection, disabling Windows via `devices.json` does not itself prevent command routing. Must enforce at gateway on every new call and tear down active Windows transport when revoked. |
| P0/P1 | Excessive tool authority and external tunnel trust boundary (`gateway.mjs:156-166,280-307,328`) | Architectural design verified. It intentionally proxies powerful Desktop Commander tools; actual client authentication is external and **not audited**. | Do not confuse authorized remote control with secure isolation. Prove who may connect, with revocation and explicit device/tool boundaries for customer use; do not assume the tunnel is unsafe without inspecting it. |
| P1 | Inherited-object `device` keys (`gateway.mjs:102-104,282-305`) | Source uses `backends[device]` on a normal JS object without own-key validation. Isolated Node.js semantics test showed `backends['__proto__'] === Object.prototype` and writing properties pollutes other objects. Did **not** run against the live MCP server. | Unexpected device argument could affect process-global objects. Reject any device value other than `steamdeck` or `windows`, including `__proto__`, `constructor`, `toString`; add runtime test. |
| P1 | Direct single-file source symlink is followed (`transfer_reliability.py:119-121`) | Reproduced in disposable temp directory with fake Windows backend: calling `copy_v2` with a symlink transferred contents of its target. | A user-visible path can unexpectedly copy a different file. Use strict validation of source and parent directories, and guard TOCTOU in folder copies. |
| P1 | Failed pairing rollback deletes last backup (`pair_windows.py:117-149`) | Previously reproduced by isolated failure injection: active known_hosts changed, no backup retained. | An error during rollback can erase the prior usable config; preserve backups and test rollback failure recovery. |
| P1 | Lost transfer-commit acknowledgement deletes recovery journal (`transfer_reliability.py:140-163`) | Previously reproduced using synthetic remote move followed by simulated lost acknowledgement. | A file may be transferred successfully but reported as failed with no durable receipt; preserve ambiguous state until verified. |
| P1 | Transfer subprocess tree lifecycle (`gateway.mjs:219-229`) | Gateway kills Python PID only; children are separate processes. Actual orphaned SSH/SFTP processes not reproduced. | Long transfers or timeouts may leave SFTP workers running. Add bounded process groups and deterministic cancellation tests. |
| P1 | SSH HostKeyAlias missing and inconsistent disable semantics (`gateway.mjs:112-124`, `pair_windows.py:84-92`) | Earlier `ssh -G` fixture verified missing HostKeyAlias and weaker fallbacks with empty ssh_config. | Likely fail-closed on IP changes, not proof of host-key bypass. Add alias together with correctly keyed known_hosts entry and test SSH verification end to end. |
| P2 | Folder copy includes unresolved partials (`folder_transfer.py:48-53`, `transfer_reliability.py:171`, `resumable_file_transfer.py:152`) | Reproduced with actual `canonical_entries()`: both an 8-character `.dc-part-*` filename and a `.dc-resume-<32hex>` filename were accepted as content. | Incomplete files can be copied as if they were user files. Harmonize partial patterns and add tests. |
| P2 | SFTP batch wildcard/path behavior (`router.py:82-103`, `resumable_file_transfer.py:61-84`) | Code constructs SFTP batch scripts; exact double-quote/glob behavior on these clients not dynamically reproduced. | Common bracketed filenames may fail or match incorrectly. Test with disposable `Panel [1].png` and `Panel 1.png` fixtures before changing implementation. |
| P2 | Missing Node lockfile, runtime integration tests, chosen project license and customer installer | `package.json`, Git tree, CI workflow, README corroborate. Existing 46 Python tests passed; no Node runtime tests in current workflow. | Customer-readiness blockers, not evidence of remote compromise. |

## Important distinction in severity

Gemini's "two Critical" findings combine **one serious unsafe filename-to-command interpolation boundary** with **one intentionally high-privilege remote-control architecture**. A tool that can run shell commands must be protected by authenticated client access and appropriate OS isolation; the existence of those tools alone is not an exploit. Likewise, potential DNS spoofing is not proof that strict SSH host-key validation can be bypassed; it may cause connection failures instead.

## Independently checked in this review

- Reopened the precise public Git checkout at commit `d13d3c0`, verified it was clean.
- Confirmed gateway has no `devices.json` reference while `router.py` enforces the Windows enabled flag.
- Exercised actual `folder_transfer.canonical_entries()` using two temporary-file naming conventions; both passed.
- Exercised actual `transfer_reliability.copy_v2()` with a synthetic symlink source and fake remote backend; symlink target bytes were copied.
- Exercised the exact JavaScript object-property issue in an isolated Node.js example, no MCP process or production network.
- Inspected `router.quote_ps()` output for a curly-apostrophe path; the character remains unescaped. Cross-checked Microsoft PowerShell documentation: https://learn.microsoft.com/en-us/powershell/module/microsoft.powershell.core/about/about_quoting_rules . A related rclone vulnerability is CVE-2026-71312 but that **does not make it an assigned CVE for Deck-Commander**.
- Relied on earlier documented read-only audit fixture reproductions for rollback failure, post-commit acknowledgement loss, and missing HostKeyAlias. Did **not** rerun a live gateway or Windows connection.

## Recommended engineering order

1. **First fix, in a new isolated branch:** PowerShell literal path encoding, and enforce Windows `devices.json` gate in the Node gateway with tests. Validate that current authorized Windows behavior still works in controlled fixtures.
2. Harden allowed device names, symlink/ancestor guards, private backup retention and ambiguous transfer recovery; establish targeted regression tests.
3. Test process-group cancellation and SFTP filename handling; fix real failure modes, preserving no-replay and SHA-256/no-clobber behavior.
4. Review the **actual** authenticated tunnel boundary and OS privileges separately, without changing private config; design least-privilege customer profiles and revocation.
5. Add a lockfile, JavaScript runtime integration CI, dependency/licenses/privacy notices, secure installer/uninstaller and fresh-machine acceptance.

## Immediate personal-use precautions until fixes

- Keep the MCP tunnel private and available only to the authorized account; do **not** publish a raw endpoint.
- Avoid transfer/copy of untrusted third-party folder trees into Windows and unpredictable/unusual filenames until path handling is repaired.
- Do not rely on `devices.json` `windows.enabled=false` to revoke command access; actually shut down the authorized Windows SSH route or Windows host if you must disable it before the fix.
- Keep customer distribution on hold. No evidence indicates an active compromise, but absence of evidence is not proof of safety.

**Repository state:** No files modified or PR merged by this review. Live gateway, tunnel, pairing keys, Windows host and other projects untouched.