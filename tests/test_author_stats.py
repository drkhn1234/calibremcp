"""Test author stats."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent / "src"))

library_path = r"L:\Multimedia Files\Written Word\Calibre-Bibliothek"
db_path = library_path + r"\metadata.db"


@pytest.mark.skipif(not Path(db_path).exists(), reason="Requires local Calibre library")
def test_author_stats():
    from calibre_mcp.db.database import init_database
    from calibre_mcp.services.author_service import author_service

    init_database(db_path)

    stats = author_service.get_author_stats()
    for _idx, _author in enumerate(stats["top_authors"][:10], 1):
        pass


if __name__ == "__main__":
    test_author_stats()
