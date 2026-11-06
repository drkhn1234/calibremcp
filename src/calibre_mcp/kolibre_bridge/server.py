"""Restricted FastMCP entry point backed entirely by authenticated Kolibre APIs.

No process-global library switching; no direct Calibre filesystem access.
Only four read-only, permission-aware tools are registered.
"""

from __future__ import annotations

import os
from urllib.parse import urlsplit

from fastmcp import FastMCP
from fastmcp.server.auth import AccessToken, RemoteAuthProvider, TokenVerifier
from fastmcp.server.dependencies import get_access_token
from pydantic import AnyHttpUrl

from .client import KolibreClient, KolibreUnauthorizedError, KolibreUnavailableError


class KolibreTokenVerifier(TokenVerifier):
    """Validate each MCP bearer via Kolibre's /api/users/me endpoint.

    Never trusts a caller-supplied user ID or an unverified JWT payload.
    Service-to-service requests forward the *same* token to Kolibre, which
    checks current library permissions on every subsequent API operation.
    """

    def __init__(self, client: KolibreClient):
        super().__init__()
        self.client = client

    async def verify_token(self, token: str) -> AccessToken | None:
        try:
            account = await self.client.current_user(token)
        except (KolibreUnauthorizedError, KolibreUnavailableError):
            return None
        return AccessToken(
            token=token,
            client_id=f"kolibre-user-{account['id']}",
            scopes=[],
            claims={"kolibre_user_id": account["id"]},
        )


def _caller_bearer() -> str:
    """Resolve the *current request's* verified bearer, not a process global."""
    identity = get_access_token()
    if identity is None or not identity.token:
        raise KolibreUnauthorizedError("Authenticated Kolibre MCP HTTP session required")
    return identity.token


def _oauth_provider(client: KolibreClient) -> TokenVerifier | RemoteAuthProvider:
    """Use native bearer verification locally, or OAuth discovery via OIDC.

    RemoteAuthProvider delegates browser authorization + PKCE/client
    registration to the operator's OIDC issuer (which MUST support DCR).
    Kolibre verifies the issuer's signed JWT access token and explicitly
    maps its (issuer, subject) to an existing Kolibre user on every call.
    """
    verifier = KolibreTokenVerifier(client)
    issuer = os.environ.get("KOLIBRE_MCP_OIDC_ISSUER", "").strip().rstrip("/")
    if not issuer:
        return verifier
    base_url = os.environ.get("KOLIBRE_MCP_PUBLIC_URL", "").strip().rstrip("/")
    for label, value in (("issuer", issuer), ("MCP public URL", base_url)):
        url = urlsplit(value)
        if url.scheme != "https" or not url.hostname or url.username or url.password or url.fragment or url.query:
            raise ValueError(f"OIDC {label} must be a public HTTPS URL")
    # Cross-origin issuer is expected; the configured Kolibre backend itself
    # validates tokens against that issuer's JWKS and pins the audience.
    return RemoteAuthProvider(
        token_verifier=verifier,
        authorization_servers=[AnyHttpUrl(issuer)],
        base_url=base_url,
        # Empty metadata scopes cause MCP clients to register with scope="",
        # which standards-compliant providers reject. These are requested
        # OAuth scopes; Kolibre still enforces identity and library permissions.
        scopes_supported=os.environ.get("KOLIBRE_MCP_OIDC_SCOPES", "openid").split(),
    )


def create_bridge(client: KolibreClient | None = None) -> FastMCP:
    if client is None:
        url = os.environ.get("KOLIBRE_BACKEND_URL", "http://127.0.0.1:8081")
        allow_internal = os.environ.get("KOLIBRE_ALLOW_INTERNAL_HTTP") == "1"
        client = KolibreClient(url, allow_internal_http=allow_internal)

    mcp = FastMCP(
        name="Kolibre (authenticated read-only bridge)",
        auth=_oauth_provider(client),
    )

    @mcp.tool()
    async def kolibre_list_libraries() -> dict:
        """List ONLY the logged-in reader's shared and owned private libraries.

        Private libraries belonging to other users do not appear, even when
        the caller is Kolibre's founder or a system administrator.
        """
        return {"libraries": await client.libraries(_caller_bearer())}

    @mcp.tool()
    async def kolibre_list_books(library: str, query: str = "", limit: int = 25) -> dict:
        """Find books in an authorized Kolibre library; no raw paths or DB IDs.

        Call kolibre_list_libraries first to choose the library identifier.
        """
        return {
            "library": library,
            "books": await client.books(_caller_bearer(), library, query=query, limit=limit),
        }

    @mcp.tool()
    async def kolibre_search_fulltext(library: str, query: str, limit: int = 15) -> dict:
        """Search indexed passages in ONE authorized library.

        This is lexical FTS, NOT semantic vector RAG. Private RAG integration
        will be enabled only after its indexes are independently authorized.
        """
        return {
            "library": library,
            "results": await client.fulltext(_caller_bearer(), library, query, limit=limit),
        }

    @mcp.tool()
    async def kolibre_my_annotations(library: str | None = None, limit: int = 50) -> dict:
        """Read the signed-in user's own highlights and notes, not other readers'."""
        return {"annotations": await client.annotations(_caller_bearer(), library=library, limit=limit)}

    return mcp


def main() -> None:
    """Run a distinct, loopback-only MCP server for authorized HTTP clients.

    This intentionally DOES NOT register the legacy CalibreMCP mutating or
    unrestricted filesystem/RAG tools. Internet deployment remains disabled
    until OAuth/client compatibility and end-to-end privacy tests pass.
    """
    import uvicorn

    host = os.environ.get("KOLIBRE_MCP_BIND", "127.0.0.1")
    if host not in ("127.0.0.1", "::1", "localhost"):
        raise ValueError(
            "Internet-facing binding is disabled until MCP OAuth and client "
            "compatibility are audited. Bind only to loopback."
        )
    port = int(os.environ.get("KOLIBRE_MCP_PORT", "10721"))
    uvicorn.run(create_bridge().http_app(path="/mcp"), host=host, port=port)
