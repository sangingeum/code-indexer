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
import os
import random
import time

from .embed_text import EMBED_FORMAT
from .embed_cache import cache_key, stored_representation
from .errors import ExtensionUnavailableError
from .extensions import SEMANTIC_EXTENSION_MESSAGE
from .fingerprint import ConfigError

logger = logging.getLogger("code-indexer.embedder")

DEFAULT_TIMEOUT = 120
DEFAULT_RETRIES = 5

# The query path makes ONE embedding attempt with this short timeout; the
# configured OLLAMA_TIMEOUT (and its retry chain) belongs to the write path.
QUERY_ATTEMPT_TIMEOUT = 10.0

# Substrings that mark an error as NOT worth retrying: the request would fail
# identically forever. Everything else (timeouts, 5xx, connection reset,
# connection refused mid-restart) is treated as retryable.
_FATAL_MARKERS = ("not found", "does not exist", "no such model",
                  "pull the model", "400", "404")


class Embedder:
    def __init__(self, host: str, model: str, batch_size: int = 48,
                 retries: int = DEFAULT_RETRIES, timeout: int = DEFAULT_TIMEOUT,
                 cache=None):
        try:
            import ollama
        except ImportError as exc:  # the extra is not installed
            raise ExtensionUnavailableError(
                SEMANTIC_EXTENSION_MESSAGE) from exc
        self._host = host
        self._ollama = ollama
        self.client = self._ollama.Client(host=host, timeout=timeout)
        self.model = model
        self.batch_size = batch_size
        self.retries = retries
        self._dim: int | None = None
        # Content-addressed cache: consulted before Ollama; keyed on
        # the exact text + model + dim + embed-text version.
        self.cache = cache
        # Query instruction (eval-gated WIN): applied ONLY to
        # query-side embeddings via query_instruction_text(); document
        # embeddings are never instructed. Eval on this repo's 42-query set:
        # recall@1 +0.048, MRR +0.040, 9 improved / 5 regressed — the
        # default comes from the plan's instruction text. Set
        # QUERY_INSTRUCTION="" to disable (cache keys include the full
        # instructed text, so toggling is safe without reindexing).
        self.query_instruction = os.environ.get(
            "QUERY_INSTRUCTION",
            "Given a natural language question or identifier, retrieve "
            "relevant source code")

    def query_instruction_text(self, query: str) -> str:
        """Query-side embedding text: instruction prefix when configured.

        ``Instruct: <task>\nQuery: <text>`` per the qwen3-embedding model
        card; document embeddings stay raw (the indexer embeds chunk text
        directly). Cache keys include the instruction via the text itself.
        """
        if self.query_instruction:
            return f"Instruct: {self.query_instruction}\nQuery: {query}"
        return query

    def dimension(self) -> int:
        if self._dim is None:
            # One-shot probe on a throwaway client, closed immediately: the
            # shared keep-alive pool would otherwise hold a socket open for
            # the process lifetime (ResourceWarning leaks under -W error).
            probe = self._ollama.Client(host=self._host, timeout=DEFAULT_TIMEOUT)
            try:
                res = self._request_with_retries(probe, ["dimension probe"])
            finally:
                close = getattr(probe, "close", None)
                if close is not None:
                    close()
            self._dim = len(res[0])
        return self._dim

    def dimension_once(self) -> int:
        """One dimension probe with the query timeout; no retry chain.

        The read path uses this so a dead embedder cannot cost the retry
        chain's backoff before the answer. A failure propagates: the caller
        reports the fingerprint as unverified rather than guessing it.
        """
        probe = self._ollama.Client(host=self._host, timeout=QUERY_ATTEMPT_TIMEOUT)
        try:
            res = probe.embed(model=self.model, input=["dimension probe"],
                              keep_alive="10m")
            embs = res.get("embeddings") or res["embeddings"]
            self._dim = len(embs[0])
            return self._dim
        finally:
            close = getattr(probe, "close", None)
            if close is not None:
                close()

    def embed_query_once(self, text: str) -> list[float]:
        """Exactly ONE embedding attempt with a short timeout (query path).

        The retry chain belongs to the write path, where a retry can
        actually succeed. A query makes one attempt and reports the failure;
        an unreachable service is the caller's answer, not a 30-second
        backoff.

        The cache is CANONICAL for the read path, and that is the whole of
        the determinism this path can offer. The service's vector for one
        text is not a function of (text, model) alone: after a model reload
        the first call for a text returns one vector and an immediately
        repeated call returns another, and the same text interleaved with a
        different text returns the first one again (measured here: up to 8.4e-3
        per component, every component moving). A warm-up cannot cover that,
        because the odd value comes back after any other request -- measured,
        a probe call immediately before the query does not change which form
        the query receives, and two probe calls do not either. So this path
        fixes the BYTES instead: the vector is rounded to the stored
        representation (float16) and written on a MISS as well as served on a
        HIT, and the caller searches the stored bytes, so the second read of a
        text searches byte-identical values whatever the service is doing.

        The dimension probe exists to KEY that row. When it fails the
        dimension is taken from the vector just received, so the row is still
        written and the read is still stable; without that a failed probe left
        the vector raw and unstored, and the next read re-embedded it and
        answered with a different vector (measured: 2 members in and 2 out of
        a 400-hit answer). A read with the cache disabled has nowhere to fix
        the bytes: that is the residual limit, and it is documented rather
        than hidden.
        """
        key: str | None = None
        if self.cache is not None:
            if self._dim is None:
                try:
                    self.dimension_once()
                except Exception:  # noqa: BLE001 — the key is best effort
                    self._dim = None
            if self._dim is not None:
                key = cache_key(text, self.model, self._dim, EMBED_FORMAT)
                cached = self.cache.get(key)
                if cached is not None:
                    return cached
        client = self._ollama.Client(host=self._host, timeout=QUERY_ATTEMPT_TIMEOUT)
        try:
            res = client.embed(model=self.model, input=[text],
                               keep_alive="10m")
            embs = res.get("embeddings") or res["embeddings"]
            if len(embs) != 1:
                raise RuntimeError(
                    f"Ollama returned {len(embs)} embeddings for 1 input")
        finally:
            close = getattr(client, "close", None)
            if close is not None:
                close()
        vector = embs[0]
        if self.cache is not None:
            # A failed probe must not leave the vector unstored: the dimension
            # is a property of the vector we just received, so the canonical
            # form can still be keyed on it and stored. Without this the read
            # searched raw bytes that nothing could reproduce later.
            if self._dim is None:
                self._dim = len(vector)
            if key is None:
                key = cache_key(text, self.model, self._dim, EMBED_FORMAT)
            canonical = stored_representation(vector)
            self.cache.put(key, canonical)
            return canonical
        return vector

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
            f"model '{model}' not found on Ollama "
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
        """Embed texts in batches of self.batch_size with retry/backoff.

        Cache-first: each text is looked up by
        sha256(text)+model+dim+embed_text_version before Ollama; misses are
        embedded and stored. Documents and instructed queries share the same
        mechanism — the key IS the text, so an instructed query never collides
        with a raw document embedding.
        """
        out: list[list[float] | None] = [None] * len(texts)
        misses: list[tuple[int, str]] = []
        if self.cache is not None:
            if self._dim is None:
                # Cache keys need the dim; probe once before lookups.
                self._dim = len(self._embed_misses(["dimension probe"])[0])
            for i, text in enumerate(texts):
                cached = self.cache.get(cache_key(
                    text, self.model, self._dim or 0, EMBED_FORMAT))
                if cached is not None:
                    out[i] = cached
                else:
                    misses.append((i, text))
        else:
            misses = list(enumerate(texts))
        if misses:
            fetched = self._embed_misses([t for _i, t in misses])
            for (i, text), vec in zip(misses, fetched):
                out[i] = vec
                if self.cache is not None and self._dim is not None:
                    self.cache.put(cache_key(
                        text, self.model, self._dim, EMBED_FORMAT), vec)
        # Misses embedded in-order; failures would have raised inside
        # _embed_batch (retryable→RuntimeError, fatal→ConfigError), so every
        # slot is filled on return.
        return [v if v is not None else [] for v in out]

    def _embed_misses(self, texts: list[str]) -> list[list[float]]:
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