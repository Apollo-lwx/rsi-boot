"""In-memory inverted retrieval with intent expansion (no exact title match)."""

from rsi_boot.rag.index import build_index, search, tokenize
from rsi_boot.rag.query import INTENT_SYNONYMS, expand_query
from rsi_boot.rag.retriever import retrieve

__all__ = [
    "INTENT_SYNONYMS",
    "build_index",
    "expand_query",
    "retrieve",
    "search",
    "tokenize",
]
