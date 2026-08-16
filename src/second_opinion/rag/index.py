"""RAG index over 10-K sections.

Chunks the Business / Risk Factors / MD&A sections, embeds them, and retrieves the top-k passages for a
query. Every retrieved chunk carries (item, chunk_no) so the report can cite "10-K Item 1A ¶12".

Embeddings: ChromaDB + sentence-transformers (all-MiniLM-L6-v2) when available. If those imports fail
(e.g. unsupported Python version), we fall back to a pure-Python TF-IDF retriever so the pipeline still
runs — the fallback is noted in the workspace log so it shows up in the architecture doc's limitations.
"""
from __future__ import annotations

import math
import re
from collections import Counter
from pathlib import Path
from typing import Any

from ..config import settings

CHUNK_WORDS = 220
CHUNK_OVERLAP = 40


def chunk_text(text: str, item: str) -> list[dict[str, Any]]:
    words = re.sub(r"\s+", " ", text).strip().split(" ")
    chunks, i, n = [], 0, 0
    while i < len(words):
        piece = " ".join(words[i:i + CHUNK_WORDS])
        if len(piece) > 80:
            chunks.append({"id": f"{item}#{n}", "item": item, "chunk_no": n, "text": piece})
            n += 1
        i += CHUNK_WORDS - CHUNK_OVERLAP
    return chunks


class _TfidfBackend:
    def __init__(self):
        self.docs: list[dict[str, Any]] = []
        self.df: Counter = Counter()

    @staticmethod
    def _tok(s: str) -> list[str]:
        return re.findall(r"[a-z][a-z0-9\-]{2,}", s.lower())

    def add(self, chunks: list[dict[str, Any]]) -> None:
        for c in chunks:
            toks = self._tok(c["text"])
            c["_tf"] = Counter(toks)
            self.df.update(set(toks))
            self.docs.append(c)

    def query(self, q: str, k: int) -> list[dict[str, Any]]:
        qt = Counter(self._tok(q))
        N = len(self.docs) or 1
        scored = []
        for d in self.docs:
            s = 0.0
            for t, qc in qt.items():
                if t in d["_tf"]:
                    idf = math.log((N + 1) / (self.df[t] + 1)) + 1
                    s += (1 + math.log(d["_tf"][t])) * idf * qc
            if s > 0:
                scored.append((s / (1 + math.log(1 + sum(d["_tf"].values()))), d))
        scored.sort(key=lambda x: -x[0])
        return [{**{k2: v for k2, v in d.items() if k2 != "_tf"}, "score": round(sc, 3)} for sc, d in scored[:k]]


class _ChromaBackend:
    def __init__(self, path: Path, name: str):
        import chromadb
        from chromadb.utils import embedding_functions
        self.client = chromadb.PersistentClient(path=str(path))
        ef = embedding_functions.SentenceTransformerEmbeddingFunction(model_name="all-MiniLM-L6-v2")
        self.col = self.client.get_or_create_collection(name, embedding_function=ef, metadata={"hnsw:space": "cosine"})

    def add(self, chunks: list[dict[str, Any]]) -> None:
        if not chunks:
            return
        existing = set(self.col.get(ids=[c["id"] for c in chunks])["ids"])
        new = [c for c in chunks if c["id"] not in existing]
        if new:
            self.col.add(ids=[c["id"] for c in new], documents=[c["text"] for c in new],
                         metadatas=[{"item": c["item"], "chunk_no": c["chunk_no"]} for c in new])

    def query(self, q: str, k: int) -> list[dict[str, Any]]:
        if self.col.count() == 0:
            return []
        res = self.col.query(query_texts=[q], n_results=min(k, self.col.count()))
        out = []
        for i, doc in enumerate(res["documents"][0]):
            meta = res["metadatas"][0][i]
            out.append({"id": res["ids"][0][i], "item": meta["item"], "chunk_no": meta["chunk_no"],
                        "text": doc, "score": round(1 - res["distances"][0][i], 3)})
        return out


class FilingIndex:
    """One index per ticker. `ingest()` is idempotent; `retrieve()` returns cited chunks."""

    def __init__(self, ticker: str, backend: str = "auto"):
        self.ticker = ticker.upper()
        self.backend_name = "tfidf"
        self._b: Any
        if backend in ("auto", "chroma"):
            try:
                self._b = _ChromaBackend(settings.data_dir / "rag", f"tenk_{self.ticker.lower()}")
                self.backend_name = "chroma"
            except Exception:  # noqa: BLE001
                if backend == "chroma":
                    raise
                self._b = _TfidfBackend()
        else:
            self._b = _TfidfBackend()
        self.n_chunks = 0
        self.meta: dict[str, Any] = {}

    def ingest(self, filing: dict[str, Any]) -> int:
        self.meta = {k: filing.get(k) for k in ("form", "filing_date", "period")}
        chunks: list[dict[str, Any]] = []
        for item, text in (filing.get("sections") or {}).items():
            chunks.extend(chunk_text(text, item))
        self._b.add(chunks)
        self.n_chunks += len(chunks)
        return len(chunks)

    def retrieve(self, query: str, k: int = 5) -> list[dict[str, Any]]:
        return self._b.query(query, k)

    def citation_label(self, chunk: dict[str, Any]) -> str:
        fd = self.meta.get("filing_date") or ""
        return f"{self.ticker} 10-K {self.meta.get('period') or ''} {chunk['item']} ¶{chunk['chunk_no']}".replace("  ", " ").strip() + (f" (filed {fd})" if fd else "")
