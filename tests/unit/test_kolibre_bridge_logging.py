"""The restricted bridge starts without needing a writable personal home."""

import json
import os
import subprocess
import sys


def test_bridge_uses_explicit_log_file_when_default_home_path_is_unusable(tmp_path):
    blocked = tmp_path / "blocked-home-data"
    blocked.write_text("Not a directory")
    log = tmp_path / "service-logs" / "bridge.jsonl"
    env = {
        **os.environ,
        "LOCALAPPDATA": str(blocked),
        "CALIBRE_MCP_LOG_FILE": str(log),
        "KOLIBRE_MCP_OIDC_ISSUER": "",
        "CALIBRE_LIBRARY_PATH": str(tmp_path / "no-user-library"),
    }
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            """
import asyncio, json
from calibre_mcp.kolibre_bridge.server import create_bridge
async def main():
    tools = await create_bridge().list_tools()
    print(json.dumps(sorted(tool.name for tool in tools)))
asyncio.run(main())
""",
        ],
        env=env,
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == [
        "kolibre_list_books",
        "kolibre_list_libraries",
        "kolibre_my_annotations",
        "kolibre_search_fulltext",
    ]
    assert log.is_file()
    assert blocked.is_file()
