"""ChromaDB vector store (all-MiniLM-L6-v2).

Semantic recall half of the GraphRAG pattern: ORACLE's vector path and
SPECTRA's clause lookup. Metadata on every chunk:
{source, doc_type, clause_id, page, system} (subset as applicable).
"""
import json

import chromadb
from chromadb.utils import embedding_functions

from core import config

_COLLECTION = "nexus_dc"


def _sanitize_meta(meta: dict) -> dict:
    return {k: (v if isinstance(v, (str, int, float, bool)) else str(v))
            for k, v in meta.items() if v is not None}


class VectorStore:
    def __init__(self, persist_dir=None):
        self._client = chromadb.PersistentClient(path=str(persist_dir or config.CHROMA_DIR))
        self._ef = embedding_functions.SentenceTransformerEmbeddingFunction(
            model_name=config.EMBEDDING_MODEL
        )
        self._col = self._client.get_or_create_collection(
            _COLLECTION, embedding_function=self._ef
        )

    def upsert(self, ids: list[str], documents: list[str], metadatas: list[dict]) -> None:
        self._col.upsert(
            ids=ids, documents=documents,
            metadatas=[_sanitize_meta(m) for m in metadatas],
        )

    def query(self, text: str, n_results: int = 5, where: dict | None = None) -> list[dict]:
        res = self._col.query(query_texts=[text], n_results=n_results, where=where)
        hits = []
        for i in range(len(res["ids"][0])):
            hits.append({
                "id": res["ids"][0][i],
                "text": res["documents"][0][i],
                "metadata": res["metadatas"][0][i],
                "distance": res["distances"][0][i],
            })
        return hits

    def count(self) -> int:
        return self._col.count()

    def index_from_cache(self, cache_dir=None) -> dict:
        """Index spec clauses, RFI corpus and submittal pages from data/cache."""
        cache_dir = cache_dir or config.CACHE_DIR
        counts = {"spec_clauses": 0, "rfis": 0, "submittal_pages": 0}

        spec_path = cache_dir / "spec_requirements.json"
        if spec_path.exists():
            reqs = json.loads(spec_path.read_text())
            ids, docs, metas = [], [], []
            for i, r in enumerate(reqs):
                body = r.get("source_text") or (
                    f"{r.get('parameter', '')} {r.get('comparison', '')} "
                    f"{r.get('value', '')} {r.get('unit') or ''}"
                )
                ids.append(f"spec::{r['clause_id']}::{i}")
                docs.append(f"Clause {r['clause_id']} ({r.get('system', '')}): {body}")
                metas.append({"source": "specification.pdf", "doc_type": "spec_clause",
                              "clause_id": r["clause_id"], "page": r.get("page"),
                              "system": r.get("system")})
            if ids:
                self.upsert(ids, docs, metas)
            counts["spec_clauses"] = len(ids)

        rfi_path = cache_dir / "rfi_register.json"
        if rfi_path.exists():
            rfis = json.loads(rfi_path.read_text())
            ids, docs, metas = [], [], []
            for r in rfis:
                parts = [f"{r['rfi_id']}: {r.get('subject', '')}"]
                if r.get("question"):
                    parts.append(f"Question: {r['question']}")
                if r.get("response"):
                    parts.append(f"Response: {r['response']}")
                ids.append(f"rfi::{r['rfi_id']}")
                docs.append("\n".join(parts))
                metas.append({"source": "rfi_register.xlsx", "doc_type": "rfi",
                              "clause_id": r.get("spec_ref"), "system": r.get("discipline"),
                              "rfi_id": r["rfi_id"], "status": r.get("status"),
                              "linked_activity": r.get("linked_activity")})
            if ids:
                self.upsert(ids, docs, metas)
            counts["rfis"] = len(ids)

        sub_path = cache_dir / "submittal_document.json"
        if sub_path.exists():
            parsed = json.loads(sub_path.read_text())
            ids, docs, metas = [], [], []
            for p in parsed["text_by_page"]:
                if not p["text"]:
                    continue
                ids.append(f"submittal::page{p['page']}")
                docs.append(p["text"])
                metas.append({"source": parsed["filename"], "doc_type": "submittal",
                              "page": p["page"], "system": "Electrical"})
            if ids:
                self.upsert(ids, docs, metas)
            counts["submittal_pages"] = len(ids)

        return counts
