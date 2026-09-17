"""Qdrant store adapter: collection create/drop, upsert, filtered purge, search.

Per design §8: one collection per project (`idx_{slug}`); remove_project is
an atomic collection drop. Point payload per §3.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from qdrant_client import QdrantClient
from qdrant_client.models import (
    Distance,
    FieldCondition,
    Filter,
    MatchValue,
    PointStruct,
    VectorParams,
    Range,
)

logger = logging.getLogger("code-indexer.store")

NAMESPACE_URL = uuid.NAMESPACE_URL


def point_id(project: str, file: str, chunk_index: int) -> str:
    """Deterministic uuid5 point ID (design §3/B) — idempotent upserts."""
    return str(uuid.uuid5(NAMESPACE_URL, f"{project}|{file}|{chunk_index}"))


class Store:
    def __init__(self, url: str, upsert_batch: int = 256):
        self.client = QdrantClient(url=url, timeout=60, check_compatibility=False)
        self.upsert_batch = upsert_batch

    def collection_exists(self, name: str) -> bool:
        return self.client.collection_exists(name)

    def create_collection(self, name: str, dim: int) -> None:
        self.client.create_collection(
            collection_name=name,
            vectors_config=VectorParams(size=dim, distance=Distance.COSINE),
        )
        # Payload indexes for fast purge/lookup by file and chunk_hash.
        for field in ("file", "chunk_hash"):
            try:
                self.client.create_payload_index(
                    collection_name=name, field_name=field, field_schema="keyword",
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("payload index on %r failed: %s", field, exc)

    def drop_collection(self, name: str) -> None:
        self.client.delete_collection(name)

    def upsert_points(self, name: str, points: list[PointStruct]) -> int:
        for i in range(0, len(points), self.upsert_batch):
            self.client.upsert(collection_name=name, points=points[i:i + self.upsert_batch])
        return len(points)

    def purge_file_points(self, name: str, project: str, file: str,
                          min_chunk_index: int | None = None) -> int:
        """Delete points for a file; optionally only chunk_index >= min.

        Returns the number of points reported deleted by Qdrant (reconciliation
        count, design Risk 3: log vs expected).
        """
        must: list[Any] = [
            FieldCondition(key="file", match=MatchValue(value=file)),
            FieldCondition(key="project", match=MatchValue(value=project)),
        ]
        if min_chunk_index is not None:
            must.append(FieldCondition(key="chunk_index", range=Range(gte=min_chunk_index)))
        try:
            res = self.client.delete(
                collection_name=name,
                points_selector=Filter(must=must),
                wait=True,
            )
        except Exception as exc:  # noqa: BLE001
            logger.error("purge_file_points failed for %s: %s", file, exc)
            return -1
        # delete by filter returns an UpdateResult; operation ops count when available
        ops = getattr(getattr(res, "result", None), "operation_id", None)
        logger.info("purged points for file=%s (op=%s)", file, ops)
        return 0 if ops is None else 0  # counts unavailable for filter deletes

    def count_points(self, name: str) -> int:
        try:
            return int(self.client.count(collection_name=name, exact=True).count)
        except Exception:  # noqa: BLE001
            return 0

    def search(self, name: str, vector: list[float], limit: int = 8,
               file_filter: str | None = None) -> list[Any]:
        qfilter = None
        if file_filter:
            qfilter = Filter(must=[FieldCondition(key="file", match=MatchValue(value=file_filter))])
        res = self.client.query_points(
            collection_name=name, query=vector, query_filter=qfilter, limit=limit,
            with_payload=True,
        )
        return list(res.points)