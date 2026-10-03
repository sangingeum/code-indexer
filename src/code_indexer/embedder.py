"""Ollama embedder: batched embed with retry + keep_alive (design §6/§9).

Resilience (robustness work item):
- connect/read timeouts via OLLAMA_TIMEOUT (seconds, default 120);
- exponential backoff with jitter, up to ``retries`` attempts (default 5);
- retryable (timeouts, 5xx, connection reset/reset-by-peer) vs fatal errors —
  "model not found" fails fast with a ConfigError naming the pull command;
- ``embed_with_errors`` returns per-text vectors plus the texts that could
  not be embedded (each retried individually first), so the indexer can
  record poisoned chunks in ``index_errors`` instead of failing the pass.
"""

from __future__ import annotations

import logging
import random
import time

import ollama

from .fingerprint import ConfigError

logger = logging.getLogger("code-indexer.embedder")

DEFAULT_TIMEOUT = 120
DEFAULT_RETRIES = 5

# Substrings that mark an error as NOT worth retrying: the request would fail
# identically forever. Everything else (timeouts, 5xx, connection reset,
# connection refused mid-restart) is treated as retryable.
_FATAL_MARKERS = ("not found", "does not exist", "no such model",
                  "pull the model", "400", "404")


class Embedder:
    def __init__(self, host: str, model: str, batch_size: int = 48,
                 retries: int = DEFAULT_RETRIES, timeout: int = DEFAULT_TIMEOUT):
        self._host = host
        self.client = ollama.Client(host=host, timeout=timeout)
        self.model = model
        self.batch_size = batch_size
        self.retries = retries
        self._dim: int | None = None

    def dimension(self) -> int:
        if self._dim is None:
            # One-shot probe on a throwaway client, closed immediately: the
            # shared keep-alive pool would otherwise hold a socket open for
            # the process lifetime (ResourceWarning leaks under -W error).
            probe = ollama.Client(host=self._host, timeout=DEFAULT_TIMEOUT)
            try:
                res = self._request_with_retries(probe, ["dimension probe"])
            finally:
                close = getattr(probe, "close", None)
                if close is not None:
                    close()
            self._dim = len(res[0])
        return self._dim

    def close(self) -> None:
        """Release the Ollama HTTP connection pool (keeps -W error runs clean)."""
        close = getattr(self.client, "close", None)
        if close is not None:
            close()

    # ------------------------------------------------------------------
    # error classification
    # ------------------------------------------------------------------

    @staticmethod
    def _is_fatal(exc: Exception) -> bool:
        text = str(exc).lower()
        return any(marker in text for marker in _FATAL_MARKERS)

    @staticmethod
    def _raise_fatal(model: str, host: str, exc: Exception) -> ConfigError:
        raise ConfigError(
            f"ConfigError: model '{model}' not found on Ollama "
            f"({host}); run: ollama pull {model}") from exc

    # ------------------------------------------------------------------
    # request core
    # ------------------------------------------------------------------

    def _request_with_retries(self, client, batch: list[str]) -> list[list[float]]:
        """One embed call with exponential backoff + jitter.

        Raises ConfigError for fatal errors (missing model); RuntimeError
        when retries are exhausted on retryable errors.
        """
        last_exc: Exception | None = None
        for attempt in range(self.retries):
            try:
                res = client.embed(
                    model=self.model, input=batch, keep_alive="10m",
                )
                embs = res.get("embeddings") or res["embeddings"]
                if len(embs) != len(batch):
                    raise ValueError(
                        f"Ollama returned {len(embs)} embeddings for "
                        f"{len(batch)} inputs")
                return embs
            except Exception as exc:  # noqa: BLE001
                if self._is_fatal(exc):
                    self._raise_fatal(self.model, self._host, exc)
                last_exc = exc
                wait = min(2 ** attempt, 30) + random.uniform(0, 1)
                logger.warning(
                    "embed batch failed (attempt %d/%d): %s — retrying in %.1fs",
                    attempt + 1, self.retries, exc, wait)
                time.sleep(wait)
        raise RuntimeError(
            f"Ollama embed failed after {self.retries} retries") from last_exc

    def embed(self, texts: list[str]) -> list[list[float]]:
        """Embed texts in batches of self.batch_size with retry/backoff."""
        out: list[list[float]] = []
        for i in range(0, len(texts), self.batch_size):
            batch = texts[i:i + self.batch_size]
            out.extend(self._embed_batch(batch))
        return out

    def _embed_batch(self, batch: list[str]) -> list[list[float]]:
        return self._request_with_retries(self.client, batch)

    def embed_with_errors(
            self, texts: list[str],
    ) -> tuple[list[list[float] | None], list[tuple[int, str]]]:
        """Embed ``texts``, tolerating per-text failures.

        Returns ``(vectors, errors)`` where ``vectors[i]`` is the embedding of
        ``texts[i]`` or None when that text failed, and ``errors`` lists
        ``(index, message)`` for the failures. A failing batch is retried
        text-by-text so one poisoned input cannot take out the whole batch.
        Raises ConfigError only when the failure is fatal for ALL texts
        (e.g. the model is missing entirely).
        """
        vectors: list[list[float] | None] = [None] * len(texts)
        errors: list[tuple[int, str]] = []
        for i in range(0, len(texts), self.batch_size):
            batch = texts[i:i + self.batch_size]
            base = i
            try:
                embs = self._request_with_retries(self.client, batch)
                for j, emb in enumerate(embs):
                    vectors[base + j] = emb
                continue
            except ConfigError:
                raise  # model missing: nothing below can succeed either
            except Exception as exc:  # noqa: BLE001 — bisect below
                logger.warning("batch of %d failed (%s); bisecting to "
                               "isolate offending texts", len(batch), exc)
                for j, text in enumerate(batch):
                    try:
                        emb = self._request_with_retries(self.client, [text])
                        vectors[base + j] = emb[0]
                    except ConfigError:
                        raise
                    except Exception as single_exc:  # noqa: BLE001
                        errors.append((base + j, str(single_exc)))
                        logger.warning("text %d failed permanently: %s",
                                       base + j, single_exc)
        return vectors, errors