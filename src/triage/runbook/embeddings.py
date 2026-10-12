"""Embeddings for runbook retrieval.

``HashingEmbeddings`` is a *lexical* (bag-of-words), not semantic, embedder: it knows
that "429" and "HTTP:TOO_MANY_REQUESTS" are the same words as in the runbook, but not
that "late" ~ "delayed". That is the right trade for this domain: failure modes are
identified by error codes and distinctive phrases, the runbook is small (~7-20
entries), and it is free, deterministic and offline, so it backs mock mode, tests and
the default config. Real semantic embeddings are a config swap
(``EMBEDDINGS_PROVIDER``); nothing else in the pipeline changes.
"""

from __future__ import annotations

import hashlib
import math
import re
from collections import Counter

from langchain_core.embeddings import Embeddings

from triage.config import Settings

# Error codes are the strongest signal here, so keep them whole as extra tokens:
# MULE:EXPRESSION, HTTP:TOO_MANY_REQUESTS, INVALID_SESSION_ID, 429.
_CODE = re.compile(r"[A-Za-z0-9]+(?:[:_][A-Za-z0-9]+)+|\b\d{3}\b")
_WORD = re.compile(r"[a-z0-9]+")

_STOPWORDS = frozenset(
    """a an and are as at be but by for from has have i if in into is it its of on or
    our so that the their then there these they this to was we were what when which
    with you your not no""".split()
)

# Whole error-code tokens count more than ordinary words and bigrams.
_CODE_WEIGHT = 2.0
_BIGRAM_WEIGHT = 0.5


def _features(text: str) -> Counter[str]:
    feats: Counter[str] = Counter()
    for code in _CODE.findall(text):
        feats[f"code:{code.lower()}"] += _CODE_WEIGHT
    words = [w for w in _WORD.findall(text.lower()) if w not in _STOPWORDS]
    for w in words:
        feats[w] += 1.0
    for a, b in zip(words, words[1:], strict=False):
        feats[f"{a} {b}"] += _BIGRAM_WEIGHT
    return feats


def _bucket(feature: str, dim: int) -> tuple[int, float]:
    # blake2b, not hash(): Python's str hash is salted per process, which would make
    # an index built yesterday unusable today.
    h = int.from_bytes(hashlib.blake2b(feature.encode(), digest_size=8).digest(), "big")
    return h % dim, 1.0 if (h >> 63) & 1 else -1.0


class HashingEmbeddings(Embeddings):
    """Deterministic feature-hashing embedder. See module docstring for the trade-offs."""

    def __init__(self, dim: int = 1024) -> None:
        self.dim = dim

    def _embed(self, text: str) -> list[float]:
        vec = [0.0] * self.dim
        for feat, weight in _features(text).items():
            idx, sign = _bucket(feat, self.dim)
            # Sublinear tf: repeating a word 10x should not drown out everything else.
            vec[idx] += sign * (1.0 + math.log(weight)) if weight >= 1.0 else sign * weight
        norm = math.sqrt(sum(v * v for v in vec))
        return [v / norm for v in vec] if norm else vec

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._embed(t) for t in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._embed(text)


def get_embeddings(settings: Settings) -> Embeddings:
    """Pick the embedder from config. The index must be rebuilt after changing it."""
    provider = settings.embeddings_provider
    if provider == "hashing":
        return HashingEmbeddings()
    if provider == "openai":
        try:
            from langchain_openai import OpenAIEmbeddings  # noqa: PLC0415
        except ImportError as exc:
            raise RuntimeError(
                "EMBEDDINGS_PROVIDER=openai requires langchain-openai: uv add langchain-openai"
            ) from exc
        return OpenAIEmbeddings(model="text-embedding-3-small")
    if provider == "voyage":
        raise NotImplementedError("TODO(dustin): wire Voyage embeddings if TrxNow policy allows")
    raise ValueError(f"unknown embeddings provider: {provider!r}")
