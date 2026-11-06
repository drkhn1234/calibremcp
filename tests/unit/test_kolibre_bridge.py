"""Security regression tests for the isolated, Kolibre-authenticated MCP bridge."""

from __future__ import annotations

import asyncio

import httpx
import pytest

from calibre_mcp.kolibre_bridge.client import (
    KolibreClient,
    KolibreUnauthorizedError,
    validate_backend_url,
)
from calibre_mcp.kolibre_bridge.server import KolibreTokenVerifier, create_bridge


@pytest.fixture
def fake_upstream():
    called = []
    state = {"founder_libraries": ["shared"]}

    def handler(request: httpx.Request) -> httpx.Response:
        token = request.headers.get("authorization", "")
        who = token.removeprefix("Bearer ")
        called.append((who, request.url.path, dict(request.url.params)))
        if who not in ("founder-token", "sister-token"):
            return httpx.Response(401, json={"detail": "Invalid token"})

        if request.url.path == "/api/users/me":
            return httpx.Response(
                200,
                json={
                    "id": 1 if who == "founder-token" else 2,
                    "username": "founder" if who == "founder-token" else "sister",
                    "is_admin": who == "founder-token",
                    "is_founder": who == "founder-token",
                },
            )

        if request.url.path == "/api/kolibre/libraries":
            folders = state["founder_libraries"] if who == "founder-token" else ["shared", "private_sister"]
            return httpx.Response(
                200,
                json=[
                    {
                        "folder_name": folder,
                        "name": "Private books" if folder.startswith("private_") else "Shared",
                        "isPrivate": folder.startswith("private_"),
                        "canUpload": True,
                        "path": "/secret/server/filesystem/path",  # deliberately not forwarded
                    }
                    for folder in folders
                ],
            )

        if request.url.path == "/api/kolibre/books":
            folder = request.url.params["library"]
            if folder == "private_sister" and who != "sister-token":
                return httpx.Response(404)
            return httpx.Response(
                200,
                json={
                    "items": [
                        {
                            "id": 1,
                            "title": "Private notes" if folder == "private_sister" else "Shared physics",
                            "author": "A",
                            "formats": ["EPUB"],
                            "file_path": "/do/not/expose",
                        },
                    ],
                    "total": 1,
                },
            )

        if request.url.path == "/api/kolibre/fulltext/search":
            return httpx.Response(
                200,
                json={
                    "results": [
                        {
                            "id": 1,
                            "title": "Search title",
                            "author": "A",
                            "format": "EPUB",
                            "snippet_html": "sample passage",
                        }
                    ]
                },
            )

        if request.url.path == "/api/kolibre/annotations":
            return httpx.Response(
                200,
                json=[
                    {"id": 1, "library": "shared", "text": "own shared note", "local_path": "/secret"},
                    {"id": 2, "library": "private_sister", "text": "sister's private note"},
                    {"id": 3, "library": None, "text": "orphan personal note"},
                ],
            )

        raise AssertionError(f"Unexpected Kolibre request: {request.url}")

    client = KolibreClient(
        "http://127.0.0.1:8081",
        transport=httpx.MockTransport(handler),
    )
    return client, called, state


@pytest.mark.asyncio
async def test_upstream_verifier_uses_verified_kolibre_user_not_role(fake_upstream):
    client, called, _ = fake_upstream
    verifier = KolibreTokenVerifier(client)
    founder = await verifier.verify_token("founder-token")
    sister = await verifier.verify_token("sister-token")
    assert founder.client_id == "kolibre-user-1"
    assert sister.client_id == "kolibre-user-2"
    assert founder.claims["kolibre_user_id"] == 1
    assert await verifier.verify_token("invalid-token") is None
    assert await verifier.verify_token("kolibre_tok_device-secret") is None
    assert all(path == "/api/users/me" for _, path, _ in called)


@pytest.mark.asyncio
async def test_founder_cannot_list_or_search_sisters_private_library(fake_upstream):
    client, calls, _ = fake_upstream
    accessible = await client.libraries("founder-token")
    assert [x["library"] for x in accessible] == ["shared"]
    assert "path" not in accessible[0]
    with pytest.raises(KolibreUnauthorizedError):
        await client.books("founder-token", "private_sister")
    with pytest.raises(KolibreUnauthorizedError):
        await client.fulltext("founder-token", "private_sister", "secret")
    assert not any(path.startswith("/api/kolibre/books") for _, path, _ in calls)
    assert not any(path.startswith("/api/kolibre/fulltext") for _, path, _ in calls)

    own = await client.books("sister-token", "private_sister")
    assert own[0]["title"] == "Private notes"
    assert "file_path" not in own[0]


@pytest.mark.asyncio
async def test_concurrent_users_do_not_share_library_or_auth_state(fake_upstream):
    client, calls, _ = fake_upstream
    founder, sister = await asyncio.gather(
        client.books("founder-token", "shared"),
        client.books("sister-token", "private_sister"),
    )
    assert founder[0]["title"] == "Shared physics"
    assert sister[0]["title"] == "Private notes"
    assert ("founder-token", "/api/kolibre/books", {"library": "shared", "q": "", "limit": "25"}) in calls
    assert ("sister-token", "/api/kolibre/books", {"library": "private_sister", "q": "", "limit": "25"}) in calls


@pytest.mark.asyncio
async def test_revoked_library_is_blocked_immediately(fake_upstream):
    client, _, state = fake_upstream
    assert (await client.books("founder-token", "shared"))[0]["title"] == "Shared physics"
    state["founder_libraries"] = []
    with pytest.raises(KolibreUnauthorizedError):
        await client.books("founder-token", "shared")


@pytest.mark.asyncio
async def test_annotations_are_owner_scoped_and_never_leak_paths(fake_upstream):
    client, _, _ = fake_upstream
    notes = await client.annotations("founder-token")
    assert {note["id"] for note in notes} == {1, 3}
    assert all("local_path" not in note for note in notes)
    with pytest.raises(KolibreUnauthorizedError):
        await client.annotations("founder-token", library="private_sister")
    notes = await client.annotations("sister-token", library="private_sister")
    assert [note["text"] for note in notes] == ["sister's private note"]


@pytest.mark.asyncio
async def test_fulltext_requires_authorized_library(fake_upstream):
    client, _, _ = fake_upstream
    results = await client.fulltext("sister-token", "private_sister", "wave equation")
    assert results[0]["snippet_html"] == "sample passage"
    assert results[0]["library"] == "private_sister"


def test_remote_cleartext_backend_is_rejected_by_default():
    with pytest.raises(ValueError):
        validate_backend_url("http://other-host:8081")
    assert validate_backend_url("https://books.example.com") == "https://books.example.com"
    assert validate_backend_url("http://other-host:8081", allow_internal_http=True) == "http://other-host:8081"
    for url in (
        "http://example.com:8081/redirect",
        "https://user:pass@example.com",
        "https://example.com?secret=1",
        "file:///etc/passwd",
    ):
        with pytest.raises(ValueError):
            validate_backend_url(url)


@pytest.mark.asyncio
async def test_missing_and_device_bearers_are_rejected_without_network(fake_upstream):
    client, calls, _ = fake_upstream
    for token in ("", "kolibre_tok_device", "hello\nAuthorization: stolen"):
        with pytest.raises(KolibreUnauthorizedError):
            await client.current_user(token)
    assert calls == []


@pytest.mark.asyncio
def test_oidc_auth_resource_advertises_external_authorization_server(fake_upstream, monkeypatch):
    from fastmcp.server.auth import RemoteAuthProvider

    client, _, _ = fake_upstream
    monkeypatch.setenv("KOLIBRE_MCP_OIDC_ISSUER", "https://login.example.test/realms/books")
    monkeypatch.setenv("KOLIBRE_MCP_PUBLIC_URL", "https://mcp.books.example.test")
    bridge = create_bridge(client)
    assert isinstance(bridge.auth, RemoteAuthProvider)


def test_remote_oidc_configuration_rejects_missing_or_insecure_urls(fake_upstream, monkeypatch):
    client, _, _ = fake_upstream
    monkeypatch.setenv("KOLIBRE_MCP_OIDC_ISSUER", "https://login.example.test/realms/books")
    for public in ("", "http://mcp.example.test", "https://user:pass@mcp.example.test"):
        monkeypatch.setenv("KOLIBRE_MCP_PUBLIC_URL", public)
        with pytest.raises(ValueError):
            create_bridge(client)
    monkeypatch.setenv("KOLIBRE_MCP_PUBLIC_URL", "https://mcp.example.test")
    monkeypatch.setenv("KOLIBRE_MCP_OIDC_ISSUER", "http://not-encrypted.example.test")
    with pytest.raises(ValueError):
        create_bridge(client)


@pytest.mark.asyncio
async def test_only_four_read_only_tools_are_registered(fake_upstream):
    client, _, _ = fake_upstream
    bridge = create_bridge(client)
    assert isinstance(bridge.auth, KolibreTokenVerifier)
    tools = await bridge.list_tools()
    assert {tool.name for tool in tools} == {
        "kolibre_list_libraries",
        "kolibre_list_books",
        "kolibre_search_fulltext",
        "kolibre_my_annotations",
    }
