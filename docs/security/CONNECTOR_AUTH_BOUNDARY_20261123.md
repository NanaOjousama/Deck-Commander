# Deck-Commander — Connector authentication boundary and isolated test

**Date:** November 23, 2026 · **Release status:** Open security gate, not commercial approval.

## Confirmed installed connector architecture

Read-only inventory identified `openai/tunnel-client` v0.0.20 as the real connector process. The Deck-Commander profile uses a **stdio** MCP command. The tunnel runtime API key authenticates this process to the OpenAI hosted tunnel control plane; it is **not** per-customer MCP authentication. The local gateway and the pre-existing `guarded_dc.py` do not independently establish a ChatGPT caller principal.

Source: [OpenAI Secure MCP Tunnel guide](https://developers.openai.com/api/docs/guides/secure-mcp-tunnels), [tunnel-client trust and data flow](https://github.com/openai/tunnel-client/blob/master/docs/architecture.md), and [Connector behavior](https://github.com/openai/tunnel-client/blob/master/docs/connectors.md).

The original personal tunnel and its credentials remain unchanged. No live token, profile value, tunnel URL, account ID, secret, key or private endpoint was printed, recorded, copied, or sent in the test.

## Actual executable local test

`security_tests/secure_tunnel_http_auth_probe.py` starts the **real unmodified** `tunnel-client v0.0.20 dev proxy` in a temporary HOME with an empty environment (no OpenAI API credentials, profiles or network tunnels). A disposable protected HTTP MCP server listens only on `127.0.0.1`. It allows unauthenticated MCP `initialize` for dev proxy readiness but requires an authorized synthetic bearer token for `tools/list` and `tools/call`.

Eight positive/negative checks:

1. Synthetic caller A with authorized token discovers a harmless tool.
2. Caller B is denied before tool discovery.
3. Missing bearer token is denied before tool discovery.
4. Caller A can invoke a harmless synthetic tool.
5. After in-memory revocation/rotation, old caller A is denied tool discovery.
6. The old caller A cannot call a tool.
7. Caller A with rotated token is accepted.
8. Caller B remains denied.
   
The test also asserts that the denied/revoked requests never increased the protected tool execution counter. The bearer tokens are random and RAM-only; they are never logged, written, or uploaded. The local proxy and server are explicitly shut down in `finally`. Output is a sanitized pass/fail summary.

For repeatability in GitHub Actions, the workflow downloads the **official v0.0.20 Linux amd64 dev-capable release**, pinned by SHA-256 `2f06ce5a17425c74a827e7038692936e973ae388e7eec728c40efee1fb5f139d`, checks archive paths before extraction and runs the test on a disposable runner. The workflow does not create an OpenAI tunnel, use an API key, inspect live profiles or contact another user's device.

## What is still NOT proven

- These are **two synthetic bearer identities**, not separate authenticated ChatGPT accounts or the hosted OpenAI tunnel. The simulated local control plane does not prove OpenAI user/workspace authorization or hosted session teardown.
- This is **not OAuth 2.1**: no authorization-code exchange, PKCE, resource audience validation, token issuer/JWKS verification, refresh token handling, or end-to-end OpenAI ChatGPT OAuth login was tested.
- Revocation is proven for **subsequent HTTP requests** at the local backend, not already executing work or remote TCP/MCP sessions. Gateway transfer cancellation and systemd teardown are separate, previously tested mechanisms.
- The personal **stdio** MCP binding has no HTTP-header hop; do not claim this HTTP bearer guard is active in the user's installed stdio gateway.
- The local tunnel proxy is for development and is expected to be unauthenticated as a proxy. That observation alone is **not a hosted tunnel security finding**.
- Public plugin distribution is **not supported directly over private Secure MCP Tunnel**. Public customer distribution would require a supported public HTTPS MCP endpoint plus appropriate OAuth/application authorization and customer consent. See the official Secure MCP Tunnel guide.

## Commercial architecture guidance

Keep the user's working personal stdio tunnel unchanged. For a **future customer-facing prototype**, evaluate a protected HTTP Streamable MCP endpoint with standard OAuth 2.1, per-account/device permissions, immediate server-side token revocation, and durable task ownership. Security testing must demonstrate that unauthorized callers are denied before tool discovery and cannot retrieve data or start work, and that revoked callers cannot continue via a previously established session. Do not build a homegrown OAuth provider merely to make tests pass; reuse an established identity provider and follow the product's documented OAuth requirements.

## Actual hosted two-identity gate remains OPEN

Require a **separate disposable tunnel and separate test account/workspace identities**, verified in the supported ChatGPT/Platform UI, before making claims about hosted authorization. The acceptance needs a negative identity, a legitimately authorized identity, session revocation and reconnect denial, old token rotation refusal, and evidence that disabled clients can't list or run tools.

Do not rotate the owner's actual tunnel key, disable their current connector, expose personal SSH credentials, or change the live gateway to attempt this test.

## Other open release requirements

Independent security review, production OAuth implementation, production journal crash recovery, safe installer/uninstaller with rollback, least-privilege OS/file migration, support for public distribution, and documented privacy/consent remain open.

**Project Sources recommendation:** keep this concise boundary handoff in Deck-Commander Project Sources once CI passes; keep runnable test source and raw CI receipts in GitHub.
