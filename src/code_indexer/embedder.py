"""Ollama embedder: batched embed with retry + keep_alive (design §6/§9)."""

from __future__ import annotations

import logging
import time

import ollama

logger = logging.getLogger("code-indexer.embedder")


class Embedder:
    def __init__(self, host: str, model: str, batch_size: int = 48, retries: int = 3):
        self.client = ollama.Client(host=host)
        self.model = model
        self.batch_size = batch_size
        self.retries = retries
        self._dim: int | None = None

    def dimension(self) -> int:
        if self._dim is None:
            res = self.embed(["dimension probe"])
            self._dim = len(res[0])
        return self._dim

    def embed(self, texts: list[str]) -> list[list[float]]:
        """Embed texts in batches of self.batch_size with retry/backoff."""
        out: list[list[float]] = []
        for i in range(0, len(texts), self.batch_size):
            batch = texts[i:i + self.batch_size]
            out.extend(self._embed_batch(batch))
        return out

    def _embed_batch(self, batch: list[str]) -> list[list[float]]:
        last_exc: Exception | None = None
        for attempt in range(self.retries):
            try:
                res = self.client.embed(
                    model=self.model, input=batch, keep_alive="10m",
                )
                embs = res.get("embeddings") or res["embeddings"]
                if len(embs) != len(batch):
                    raise ValueError(
                        f"Ollama returned {len(embs)} embeddings for {len(batch)} inputs"
                    )
                return embs
            except Exception as exc:  # noqa: BLE001 — retry any transport error
                last_exc = exc
                wait = 2 ** attempt
                logger.warning(
                    "embed batch failed (attempt %d/%d): %s — retrying in %ds",
                    attempt + 1, self.retries, exc, wait,
                )
                time.sleep(wait)
        raise RuntimeError(f"Ollama embed failed after {self.retries} retries") from last_exc