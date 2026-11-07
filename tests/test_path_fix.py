"""Test the path fix."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent / "src"))

db_path = r"L:\Multimedia Files\Written Word\Calibre-Bibliothek\metadata.db"


@pytest.mark.skipif(not Path(db_path).exists(), reason="Requires local Calibre library")
def test_path_fix():
    from calibre_mcp.db.database import init_database
    from calibre_mcp.services.book_service import book_service

    init_database(db_path)

    # Get book 6696
    book = book_service.get_by_id(6696)
    if book:
        for fmt in book.get("formats", []):
            path = Path(fmt.get("path"))
            assert path is not None


if __name__ == "__main__":
    test_path_fix()
