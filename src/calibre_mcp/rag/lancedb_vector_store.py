"""
LanceDB + fastembed vector store for Calibre content RAG (portmanteau ingest / full-text chunks).

Self-contained in calibre-mcp — no dependency on other repos. FTS-derived indexes use
``indexer``/``retriever`` with table ``books_rag`` under ``{library}/lancedb``; neural
ingest uses tables ``calibre_media`` / ``calibre_fulltext`` under ``{library}/lancedb_calibre``.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import lancedb

logger = logging.getLogger(__name__)


class LanceVectorStore:
    """Document embeddings and retrieval using LanceDB (same role as legacy external BaseVectorStore)."""

    def __init__(
        self,
        db_path: str | Path,
        embedding_model_name: str | None = "BAAI/bge-small-en-v1.5",
        table_name: str = "documents",
    ) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.db = lancedb.connect(str(self.db_path))
        if embedding_model_name is not None:
            from calibre_mcp.rag.fastembed_gpu import create_text_embedding, repo_root_from_here

            cache_dir = str(self.db_path / "cache")
            self.embedding_model, self.embed_device, self.embed_batch_size = create_text_embedding(
                embedding_model_name,
                cache_dir,
                repo_root=repo_root_from_here(),
            )
        self.table_name = table_name

    def add_documents(self, documents: list[dict[str, Any]], overwrite: bool = True) -> None:
        """
        Embed and index documents.

        Each item: ``id``, ``content``, ``metadata`` (dict); optional ``source``.
        """
        if not documents:
            return

        logger.info("Embedding %s items into '%s'...", len(documents), self.table_name)

        contents = [doc["content"] for doc in documents]
        all_embeddings: list[Any] = []
        batch = self.embed_batch_size
        for start in range(0, len(contents), batch):
            chunk = contents[start : start + batch]
            all_embeddings.extend(list(self.embedding_model.embed(chunk)))

        data: list[dict[str, Any]] = []
        for doc, emb in zip(documents, all_embeddings, strict=True):
            vec = emb.tolist() if hasattr(emb, "tolist") else list(emb)
            entry: dict[str, Any] = {
                "id": doc.get("id"),
                "vector": vec,
                "content": doc.get("content"),
                "metadata": doc.get("metadata", {}),
            }
            if "source" in doc:
                entry["source"] = doc["source"]
            data.append(entry)

        if overwrite or self.table_name not in self.db.table_names():
            self.db.create_table(self.table_name, data=data, mode="overwrite")
        else:
            tbl = self.db.open_table(self.table_name)
            tbl.add(data)

        logger.info("Indexed %s items into LanceDB table '%s'.", len(data), self.table_name)

    def search(self, query: str, limit: int = 5, where: str | None = None) -> list[dict[str, Any]]:
        """Semantic search with optional LanceDB prefilter."""
        if self.table_name not in self.db.table_names():
            logger.warning("Table '%s' not found.", self.table_name)
            return []

        tbl = self.db.open_table(self.table_name)
        qemb = list(self.embedding_model.embed([query]))[0]
        qvec = qemb.tolist() if hasattr(qemb, "tolist") else list(qemb)

        search_req = tbl.search(qvec).limit(limit)
        if where:
            search_req = search_req.where(where)

        return search_req.to_arrow().to_pylist()

    def get_table_metadata(self) -> dict[str, Any]:
        if self.table_name not in self.db.table_names():
            return {"exists": False, "row_count": 0}

        tbl = self.db.open_table(self.table_name)
        return {"exists": True, "row_count": tbl.count_rows()}

    def replace_book_vectors(self, book_id: int, rows: list[dict[str, Any]]) -> None:
        """Atomically replace one book; caller owns embeddings and authorization."""
        book_id = int(book_id)
        if not rows:
            if self.table_name in self.db.table_names():
                self.db.open_table(self.table_name).delete(f"metadata.book_id = {book_id}")
            return
        if any(int(r["metadata"]["book_id"]) != book_id for r in rows):
            raise ValueError("Mixed-book replacement")
        if self.table_name not in self.db.table_names():
            self.db.create_table(self.table_name, data=rows)
            return
        table = self.db.open_table(self.table_name)
        (
            table.merge_insert("id")
            .when_matched_update_all()
            .when_not_matched_insert_all()
            .when_not_matched_by_source_delete(f"metadata.book_id = {book_id}")
            .execute(rows)
        )

    def search_vector(self, vector: list[float], *, where: str, limit: int = 100) -> list[dict[str, Any]]:
        """Precomputed query, mandatory caller-built prefilter. No identity inference."""
        if not where or self.table_name not in self.db.table_names():
            return []
        table = self.db.open_table(self.table_name)
        return (
            table.search(vector)
            .distance_type("cosine")
            .where(where, prefilter=True)
            .limit(limit)
            .to_arrow()
            .to_pylist()
        )
