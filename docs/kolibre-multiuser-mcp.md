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
5. FastMCP TokenVerifier accepts an already-issued bearer but does not
   implement OAuth 2.1 authorization flows or token issuance. Many remote
   MCP clients (including ChatGPT's connector flow) need a real OAuth/DCR
   setup before they can log in ergonomically.
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
