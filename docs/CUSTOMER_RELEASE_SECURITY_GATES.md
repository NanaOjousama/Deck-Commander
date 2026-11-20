# Customer-installable Deck Commander — release security gates

**Status (2026-11-20):** Development plan and initial hardening only. **Not approved for public installation or sale.** Pricing/business model deliberately undecided.

This document is an incremental workstream alongside the existing engineering roadmap, not a replacement for [Issue #1](https://github.com/NanaOjousama/Deck-Commander/issues/1) (transfer reliability/performance) or [Issue #2](https://github.com/NanaOjousama/Deck-Commander/issues/2) (tool efficiency and independent job safety).

## Ground rules

- Keep the owner's private Steam Deck/Windows gateway, tunnel, keys, live jobs, and other projects untouched.
- Develop and test isolated changes with disposable data and a rollback procedure. Use a separate branch and draft PR; no automatic deployment or merging.
- A ChatGPT connector/remote tunnel is **external** to this repository. Do not claim that the source snapshot alone provides authenticated internet access.
- Do not run paid Colab/RunPod resources, create a hosted service, publish releases, or incur costs without separate approval.
- The MIT-licensed upstream Desktop Commander is a high-privilege arbitrary-command executor, **not a security sandbox**. A trusted client, OS-level isolation, and explicit authorization boundaries are essential.

## Defined first supported scenario (proposed)

One personally owned Linux host (Steam Deck or regular Linux laptop) running the gateway plus one explicitly paired Windows host, reached over a trusted local network, using independently verified SSH host keys and a **separately authenticated** MCP client connection. This is the existing architecture generalized for non-Steam-Deck Linux hosts. It does not promise Windows-as-gateway, macOS, arbitrary devices, or internet-exposed command endpoints.

## Must-pass release gates

| Gate | Required proof | State |
|---|---|---|
| A — Trusted client access | Document the authentication boundary, device ownership/consent, account/session revocation, and network threat model. Test refusal of unauthorized connections. Never expose raw MCP command execution publicly. | Open |
| B — Principle of least privilege | Safe default installation under a dedicated OS account or isolated environment where appropriate; clear warnings that command/file guardrails are not isolation. No privilege escalation by default. | Open |
| C — Pairing / secrets | Fingerprint-verified SSH setup; fail closed; private config/key permissions; denied authentication leaves previous config intact; failed installation is recoverable. | Partial: staging and offline tests in development branch |
| D — Dependency security and licensing | Commit a reproducible lockfile, generate a full transitive dependency/license inventory and notices, vulnerability scans, and disclose or explicitly handle upstream installation telemetry. Keep MIT copyright notice. | Open |
| E — Installer / uninstaller | Guided setup using customer-owned credentials, clear permission prompts, idempotent reruns, rollback and safe uninstall; no default public port exposure; no accidental credential logging. | Open |
| F — Transfer reliability | Preserve SHA-256 and no-overwrite semantics; test interruption, crash consistency, symlinks/reparse points, disk-full, remote disconnect, path safety, and child-process cancellation. | Partial: 38 original offline tests |
| G — MCP gateway / client integration | Automated backend routing tests, explicit device targets, connection-error tests, timeout behavior, minimal permissions, and representative client compatibility tests. | Open |
| H — Fresh-device acceptance | Install on a clean supported Linux machine and a disposable Windows test machine; reproduce tests; demonstrate rollback and repair without developer assistance. | Open |
| I — Customer documentation | Supported platforms and versions, costs from third-party AI/tunnel services, privacy, expected limitations, security warnings, license/terms, support/update policy. | Open |

## Immediate implementation sequence

1. **Safe Windows pairing staging:** Validate the fingerprint and authenticate through disposable SSH configuration before replacing the live pairing files. Add success/failure tests. This still needs crash/partial multi-file commit rollback review; staging alone is not a complete transaction.
2. **Reproducibility:** Match runtime requirement to the exact upstream package, add lockfile and dependency/license/SBOM tooling, then evaluate installation telemetry.
3. **Threat model and installation permissions:** Decide which trusted-client authenticator and trust boundary the *local gateway* owns vs which is delegated to the separate tunnel/connector. Do not invent a homemade authentication layer unless needed.
4. **Fresh-install prototype:** Test non-Steam-Deck Linux + Windows against disposable machines/VMs; document manual onboarding gaps before writing a guided installer.
5. **Beta decision:** Proceed only once release gates are met and potential users demonstrate interest.

## Current verification

- Baseline handoff: 38 offline tests and real two-device transfer checks (November 16).
- The separate hardening branch adds five isolated Windows pairing tests; CI must pass before review.
- No main-branch merge, production configuration change, running-job change, or customer release is authorized by this document.
