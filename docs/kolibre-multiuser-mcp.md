# Kolibre-authenticated MCP bridge (initial milestone)

**Status:** draft proof of integration; do **not** expose to the Internet or
mount alongside unrestricted legacy CalibreMCP tools. This is not yet the
final single-endpoint, OAuth-capable, permission-aware RAG server.

## Design

Kolibre remains the **source of truth** for identity and library permissions.

`
MCP client (Kolibre user JWT)
    │
    ▼
dedicated FastMCP HTTP server (loopback:10721/mcp)
    │  validates every incoming token against GET /api/users/me
    │  per operation: forwards same JWT only to configured Kolibre backend
    ▼
Kolibre HTTP API
    │  applies real per-user library authorization
    ▼
shared Calibre library + caller's own private Calibre library
`

Four read-only MCP tools are registered:
- `kolibre_list_libraries()`: only authorized libraries; no server paths.
- `kolibre_list_books(library, query, limit)`: catalog search, one authorized library.
- `kolibre_search_fulltext(library, query, limit)`: Kolibre's **lexical** FTS,
  not vector RAG. It depends on indexing enabled in that library.
- `kolibre_my_annotations(library?, limit)`: only the authenticated reader's
  own notes, stripped of filesystem paths.

The MCP server NEVER mounts unrestricted CalibreMCP commands like
`switch_library`, `calibre_rag`, `manage_files`, `manage_system`, or `rag_index_build`. All
MCP users must use their own Kolibre user access token, not the shared
MCP server's service token. A founder JWT confers no extra visibility into
another user's private library.

## Local development only

Prerequisites: an isolated Kolibre staging backend with temporary libraries
and a test account. Avoid real user libraries and your Oracle production
deployment until the multi-user Kolibre branch passes final security review.

```sh
export KOLIBRE_BACKEND_URL=http://127.0.0.1:8081
python -m calibre_mcp.kolibre_bridge
```

The listener defaults to `127.0.0.1:10721/mcp`. A non-loopback bind
is deliberately rejected. This is a separate process and does not replace
the existing CalibreMCP server.

Client configuration must supply the user's Kolibre **short-lived human JWT**
as `Authorization: Bearer ...` on each MCP HTTP request. Never put it in
a URL, source file, test fixture in a public repo, or log. KOReader's permanent
`kolibre_tok_...` **device token is deliberately rejected** by this bridge.

Only a backend on HTTPS or local loopback HTTP is permitted by default. A
private Docker network may set `KOLIBRE_ALLOW_INTERNAL_HTTP=1` when a
non-loopback internal hostname needs HTTP; never use this flag for an
untrusted network. Requests do not follow redirects and do not inherit
HTTP proxy environment variables.

## Optional OAuth / OIDC discovery (stacked draft)

The default **local-only** bridge still accepts a preissued Kolibre native user
JWT. For an actual browser-based OAuth Authorization Code + PKCE flow, use an
external OIDC authorization server that supports **Dynamic Client Registration
(DCR)**, and configure the following values on the CalibreMCP bridge:

```env
KOLIBRE_MCP_OIDC_ISSUER=https://login.example.test/realms/books
KOLIBRE_MCP_PUBLIC_URL=https://mcp.books.example.test
KOLIBRE_MCP_OIDC_SCOPES=openid books:read
```

With both variables set, FastMCP uses `RemoteAuthProvider` to advertise
the trusted external authorization server to remote MCP clients. The IdP
handles authorization code issuance, PKCE, refresh and client registration.
The bridge does **not** issue or accept arbitrary unsigned identity claims.
Every incoming user bearer is checked with Kolibre's
`GET /api/users/me`; every tool call reuses that same bearer against
Kolibre's authorized HTTP APIs. This assumes Kolibre's
`feature/mcp-oidc-identities` branch is deployed in a disposable staging
environment and configured to verify the IdP's **RS256 JWT access tokens**.
A normal ID token or opaque access token will not work.

On Kolibre, configure `KOLIBRE_OIDC_ISSUER`,
`KOLIBRE_OIDC_AUDIENCE`, and `KOLIBRE_OIDC_JWKS_URI`. The issuer must
match the bridge exactly, and the provider must issue JWT access tokens
for Kolibre's configured audience. No OIDC identity is auto-provisioned.
Each Kolibre user must first link their own externally authenticated subject
through the native-login/password-confirmed OIDC linking endpoint. Founder
accounts cannot change someone else's binding through application APIs.

`KOLIBRE_MCP_PUBLIC_URL` is the externally advertised **HTTPS** origin. The
origin must exclude the `/mcp` path, which FastMCP appends itself.
`KOLIBRE_MCP_OIDC_SCOPES` is a space-separated list of scopes to advertise
to MCP OAuth clients (default: `openid`). Configure these consistently with
the provider's client-registration and resource scope policies. These scopes
guide OAuth requests; Kolibre's verified identity and live ACLs remain the
authorization boundary.
The
process itself still listens only on loopback; a separately secured TLS proxy
would be required for remote MCP clients. **Do not enable this remotely with
personal data yet.** OAuth discovery and real end-to-end MCP client login
are not verified for a production IdP.

If your IdP does **not** support DCR, FastMCP's OIDC/OAuth proxy approach
and pre-registered client credentials may be needed instead; this is future
work, not silently assumed to function.

## Security properties and limits

1. FastMCP verifies each MCP bearer against the *live* Kolibre
   `GET /api/users/me` endpoint. There is no unsigned JWT decoding
   and no caller-selectable user ID.
2. Each tool fetches current authorized libraries, then accesses **only**
   those libraries via Kolibre. A recently revoked library is rejected
   on the next operation.
3. Multiple users' calls share no `current_library` global. There is
   no path selector or direct filesystem access.
4. Kolibre itself still requires its remaining security audit and staging
   tests. This design does not compensate for a compromised Kolibre API.
5. Optional RemoteAuthProvider advertises an external OIDC issuer supporting
   DCR, but production OAuth compatibility and client login are still
   unverified. No OAuth server or password login pages are provided by the
   bridge itself.
6. Fulltext is text search, **not** embedding/semantic RAG; per-user RAG
   storage and retrieval remain a separate milestone.
7. Root on the VPS can still access private book files. Cryptographic
   server-operator privacy is explicitly deferred.
8. Bind to loopback by default and use only test data. Do NOT make it
   Internet-facing until real OAuth, auditable authentication, TLS, threat
   modeling, load testing and cross-account end-to-end tests are complete.

## Next steps

- Adopt an OIDC/OAuth provider for MCP clients; bind each login to a specific
  Kolibre user without passing long-lived credentials to the LLM.
- Migrate the safe tools into the eventual single MCP surface **only after**
  the legacy path-selecting and mutating tools are ACL-gated or disabled.
- Add library-scoped vector indexes: shared embeddings computed once,
  owner-private indexes restricted by verified identity.
- Integration test with Kolibre TestClient + FastMCP HTTP/Client over separate
  test identities. Do not deploy with actual user data until successful.
