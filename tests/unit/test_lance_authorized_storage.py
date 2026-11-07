"""Actual LanceDB updates, using externally generated vectors, no model download."""

from calibre_mcp.rag.lancedb_vector_store import LanceVectorStore


def row(book, text, vector):
    return {
        "id": str(book),
        "content": text,
        "vector": vector,
        "metadata": {"book_id": book, "digest": "version", "section": "c.xhtml", "word_start": 0},
    }


def test_atomic_book_replacement_and_prefilter(tmp_path):
    store = LanceVectorStore(tmp_path / "lance", embedding_model_name=None)
    store.replace_book_vectors(1, [row(1, "old", [1.0, 0.0])])
    store.replace_book_vectors(2, [row(2, "other", [0.0, 1.0])])
    store.replace_book_vectors(1, [row(1, "new", [1.0, 0.0])])
    result = store.search_vector([1.0, 0.0], where="metadata.book_id = 1")
    assert [r["content"] for r in result] == ["new"]
    assert store.get_table_metadata()["row_count"] == 2
    store.replace_book_vectors(1, [])
    assert not store.search_vector([1.0, 0.0], where="metadata.book_id = 1")
    assert store.search_vector([1.0, 0.0], where="metadata.book_id = 2")[0]["content"] == "other"
