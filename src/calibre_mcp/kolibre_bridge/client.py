"""Minimal, permission-aware HTTP client for Kolibre.

Never opens a Calibre metadata.db, accepts raw filesystem paths, or uses
CalibreMCP's process-global current_library. The caller's bearer is forwarded
ONLY to the configured Kolibre origin, and Kolibre applies its own ACLs.
"""

from __future__ import annotations

import ipaddress
from typing import Any
from urllib.parse import urlsplit

import httpx


class KolibreUnauthorizedError(Exception):
    """Missing, invalid or expired identity (or an inaccessible library)."""


class KolibreUnavailableError(Exception):
    """Kolibre could not supply a valid, authorized response."""


def validate_backend_url(url: str, *, allow_internal_http: bool = False) -> str:
    parts = urlsplit(url)
    if parts.scheme not in ("https", "http") or not parts.hostname:
        raise ValueError("KOLIBRE_BACKEND_URL must be an absolute HTTP(S) URL")
    if parts.username or parts.password or parts.query or parts.fragment:
        raise ValueError("Kolibre URL cannot contain credentials, query or fragment")
    if parts.path not in ("", "/"):
        raise ValueError("Kolibre backend URL must be an origin, without a path")
    if parts.scheme == "http":
        try:
            loopback = ipaddress.ip_address(parts.hostname).is_loopback
        except ValueError:
            loopback = parts.hostname == "localhost"
        if not loopback and not allow_internal_http:
            raise ValueError(
                "Unencrypted remote Kolibre URL rejected. Use HTTPS or explicitly "
                "allow HTTP only on a trusted internal Docker network."
            )
    return url.rstrip("/")


class KolibreClient:
    """Stateless per-request Kolibre API client with fail-closed access checks."""

    def __init__(
        self,
        backend_url: str,
        *,
        allow_internal_http: bool = False,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        self.base_url = validate_backend_url(backend_url, allow_internal_http=allow_internal_http)
        self.transport = transport

    async def _get(self, token: str, path: str, params: dict[str, Any] | None = None) -> Any:
        if (
            not token
            or not isinstance(token, str)
            or len(token) > 8192
            or token.startswith("kolibre_tok_")
            or "\r" in token
            or "\n" in token
        ):
            raise KolibreUnauthorizedError("A Kolibre user bearer token is required")
        if not path.startswith("/") or path.startswith("//"):
            raise ValueError("Only predefined absolute Kolibre API paths are allowed")
        try:
            async with httpx.AsyncClient(
                base_url=self.base_url,
                timeout=httpx.Timeout(8.0, connect=3.0),
                follow_redirects=False,
                trust_env=False,
                transport=self.transport,
            ) as client:
                response = await client.get(
                    path,
                    params=params,
                    headers={"Authorization": f"Bearer {token}"},
                )
        except httpx.RequestError as exc:
            raise KolibreUnavailableError("Kolibre backend unavailable") from exc

        if response.status_code in (401, 403, 404):
            raise KolibreUnauthorizedError("Not authorized for the requested Kolibre resource")
        if response.status_code != 200:
            raise KolibreUnavailableError("Kolibre did not return a successful response")
        try:
            return response.json()
        except ValueError as exc:
            raise KolibreUnavailableError("Kolibre returned invalid JSON") from exc

    async def current_user(self, token: str) -> dict[str, Any]:
        data = await self._get(token, "/api/users/me")
        if (
            not isinstance(data, dict)
            or type(data.get("id")) is not int
            or data["id"] <= 0
            or not isinstance(data.get("username"), str)
        ):
            raise KolibreUnavailableError("Invalid Kolibre account response")
        return {"id": data["id"], "username": data["username"]}

    async def libraries(self, token: str) -> list[dict[str, Any]]:
        data = await self._get(token, "/api/kolibre/libraries")
        if not isinstance(data, list):
            raise KolibreUnavailableError("Invalid Kolibre library list")
        allowed = []
        for item in data:
            if not isinstance(item, dict):
                raise KolibreUnavailableError("Invalid library entry")
            folder = item.get("folder_name")
            if not isinstance(folder, str) or not folder or "/" in folder or "\\" in folder:
                raise KolibreUnavailableError("Invalid library identifier")
            allowed.append(
                {
                    "library": folder,
                    "name": str(item.get("name") or folder),
                    "private": item.get("isPrivate") is True,
                    "can_upload": item.get("canUpload") is True,
                }
            )
        return allowed

    async def require_library(self, token: str, folder: str) -> dict[str, Any]:
        if not isinstance(folder, str) or not folder:
            raise KolibreUnauthorizedError("Select an accessible library")
        allowed = await self.libraries(token)
        result = next((lib for lib in allowed if lib["library"] == folder), None)
        if result is None:
            # Never disclose whether someone else's private folder exists.
            raise KolibreUnauthorizedError("Library not found")
        return result

    async def books(self, token: str, library: str, *, query: str = "", limit: int = 25) -> list[dict[str, Any]]:
        await self.require_library(token, library)
        limit = max(1, min(int(limit), 100))
        data = await self._get(
            token,
            "/api/kolibre/books",
            params={"library": library, "q": query[:200], "limit": limit},
        )
        if not isinstance(data, dict) or not isinstance(data.get("items"), list):
            raise KolibreUnavailableError("Invalid Kolibre catalog response")
        return [
            {
                "id": item.get("id"),
                "title": item.get("title"),
                "author": item.get("author"),
                "formats": item.get("formats") or [],
                "library": library,
            }
            for item in data["items"][:limit]
            if isinstance(item, dict)
        ]

    async def fulltext(self, token: str, library: str, query: str, *, limit: int = 15) -> list[dict[str, Any]]:
        await self.require_library(token, library)
        if not query.strip():
            return []
        limit = max(1, min(int(limit), 40))
        data = await self._get(
            token,
            "/api/kolibre/fulltext/search",
            params={"library": library, "q": query[:500], "limit": limit},
        )
        if not isinstance(data, dict) or not isinstance(data.get("results"), list):
            raise KolibreUnavailableError("Invalid Kolibre fulltext response")
        return [
            {
                "id": item.get("id"),
                "library": library,
                "title": item.get("title"),
                "author": item.get("author"),
                "format": item.get("format"),
                "snippet_html": item.get("snippet_html"),
            }
            for item in data["results"][:limit]
            if isinstance(item, dict)
        ]

    async def annotations(self, token: str, *, library: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        accessible = {lib["library"] for lib in await self.libraries(token)}
        if library is not None and library not in accessible:
            raise KolibreUnauthorizedError("Library not found")
        data = await self._get(token, "/api/kolibre/annotations")
        if not isinstance(data, list):
            raise KolibreUnavailableError("Invalid Kolibre annotations response")
        limit = max(1, min(int(limit), 100))
        items = []
        for item in data:
            if not isinstance(item, dict):
                continue
            folder = item.get("library")
            # Preserve personal orphan annotations (no known library), but
            # never resolve or return a note bound to an inaccessible catalog.
            if folder is not None and folder not in accessible:
                continue
            if library is not None and folder != library:
                continue
            items.append(
                {
                    "id": item.get("id"),
                    "library": folder,
                    "book_id": item.get("calibre_book_id"),
                    "text": item.get("text"),
                    "notes": item.get("notes"),
                    "chapter": item.get("chapter"),
                    "page": item.get("page"),
                }
            )
            if len(items) >= limit:
                break
        return items
