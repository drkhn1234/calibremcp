"""Test database init with actual library."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent / "src"))

library_path = r"L:\Multimedia Files\Written Word\Calibre-Bibliothek"
db_path = library_path + r"\metadata.db"


@pytest.mark.skipif(not Path(db_path).exists(), reason="Requires local Calibre library")
def test_db_init():
    from calibre_mcp.db.database import init_database
    from calibre_mcp.services.tag_service import tag_service

    init_database(db_path)

    stats = tag_service.get_tag_statistics()
    assert stats is not None


if __name__ == "__main__":
    test_db_init()
