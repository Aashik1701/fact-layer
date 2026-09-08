"""
Retrieval configuration — every knob this layer introduces, read from the
environment once and passed around explicitly (same shape as
`fact_layer.storage.backend_from_env()`: one function, `os.environ.get`
with defaults, no config framework).

RETRIEVAL_ENABLED=false is the default and preserves the pre-existing
behavior of `Store.ingest()` exactly (relationship_mode="bruteforce" —
cluster-dict grouping + `adjudicate_cluster()`, byte-for-byte unchanged).
Setting it true switches candidate generation to this package without
touching `comparability.gate()` or `adjudicate.adjudicate()` at all.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from dotenv import load_dotenv

load_dotenv()

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_DEFAULT_INDEX_DIR = os.path.join(_REPO_ROOT, "data", "retrieval_index")
_DEFAULT_MODEL_CACHE_DIR = os.path.join(_REPO_ROOT, "cache", "embeddings", "models")


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        return int(raw)
    except ValueError:
        return default


@dataclass(frozen=True)
class RetrievalConfig:
    enabled: bool = False
    top_k: int = 50
    lexical_weight: float = 0.45
    semantic_weight: float = 0.55
    # How many candidates each individual channel (lexical, semantic)
    # fetches before fusion narrows to top_k — must be >= top_k so fusion
    # has something to rank beyond whatever the smaller channel returned.
    channel_fanout: int = 100
    embedding_model: str = "BAAI/bge-small-en-v1.5"
    # "live" (default): download the model on first use if not cached.
    # "replay": never touch the network — HF_HUB_OFFLINE is forced on, and
    # a missing local model cache fails loudly. Mirrors llm.py's
    # LLM_MODE=live|replay so a test/replay run is provably network-free.
    embedding_mode: str = "live"
    embedding_model_cache_dir: str = field(default_factory=lambda: _DEFAULT_MODEL_CACHE_DIR)
    index_dir: str = field(default_factory=lambda: _DEFAULT_INDEX_DIR)

    @property
    def lexical_db_path(self) -> str:
        return os.path.join(self.index_dir, "lexical.sqlite3")

    @property
    def vector_store_path(self) -> str:
        return os.path.join(self.index_dir, "vectors.npz")

    @property
    def vector_meta_path(self) -> str:
        return os.path.join(self.index_dir, "vector_meta.json")

    @property
    def embedding_cache_path(self) -> str:
        return os.path.join(self.index_dir, "embedding_cache.sqlite3")


def load_config() -> RetrievalConfig:
    return RetrievalConfig(
        enabled=_env_bool("RETRIEVAL_ENABLED", False),
        top_k=_env_int("RETRIEVAL_TOP_K", 50),
        lexical_weight=_env_float("RETRIEVAL_LEXICAL_WEIGHT", 0.45),
        semantic_weight=_env_float("RETRIEVAL_SEMANTIC_WEIGHT", 0.55),
        channel_fanout=_env_int("RETRIEVAL_CHANNEL_FANOUT", 100),
        embedding_model=os.environ.get("EMBEDDING_MODEL", "").strip() or "BAAI/bge-small-en-v1.5",
        embedding_mode=os.environ.get("EMBEDDING_MODE", "live").strip().lower() or "live",
        embedding_model_cache_dir=os.environ.get("EMBEDDING_MODEL_CACHE_DIR", "").strip() or _DEFAULT_MODEL_CACHE_DIR,
        index_dir=os.environ.get("RETRIEVAL_INDEX_PATH", "").strip() or _DEFAULT_INDEX_DIR,
    )
