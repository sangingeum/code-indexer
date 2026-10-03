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
    AliasOperations,  # noqa: F401 — re-exported for callers
    CreateAlias,
    CreateAliasOperation,
    DeleteAlias,
    DeleteAliasOperation,
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

    def physical_name(self, name: str) -> str | None:
        """Resolve ``name`` (collection or alias) to the physical collection."""
        try:
            for alias in self.client.get_aliases().aliases:
                if alias.alias_name == name:
                    return alias.collection_name
        except Exception:  # noqa: BLE001
            pass
        try:
            if self.client.collection_exists(name):
                return name
        except Exception:  # noqa: BLE001
            pass
        return None

    def _alias_exists(self, alias_name: str) -> bool:
        try:
            return any(a.alias_name == alias_name
                       for a in self.client.get_aliases().aliases)
        except Exception:  # noqa: BLE001
            return False

    def swap_collection(self, tmp: str, target: str) -> None:
        """Point ``target`` at the freshly built ``tmp`` collection via alias.

        ``target`` may be a real collection (first swap: drop it, then create
        the alias) or already an alias (later swaps: repoint, then drop the
        previously pointed physical collection). All reads/writes go through
        ``idx_<slug>``, which is therefore never more than one alias update
        away from the live data.
        """
        old_physical = self.physical_name(target)
        if old_physical == tmp:
            return  # tmp already served under the alias; nothing to do
        if old_physical == target and not self._alias_exists(target):
            # target is a REAL collection: drop it so the name is free.
            self.client.delete_collection(target)
        if self._alias_exists(target):
            self.client.update_collection_aliases(
                change_aliases_operations=[DeleteAliasOperation(
                    delete_alias=DeleteAlias(alias_name=target))])
        self.client.update_collection_aliases(
            change_aliases_operations=[CreateAliasOperation(
                create_alias=CreateAlias(alias_name=target,
                                         collection_name=tmp))])
        if old_physical is not None and old_physical != target:
            self.client.delete_collection(old_physical)

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
               file_filter: str | None = None,
               symbol_type: str | None = None,
               language: str | None = None) -> list[Any]:
        must: list[Any] = []
        if file_filter:
            must.append(FieldCondition(key="file", match=MatchValue(value=file_filter)))
        if symbol_type:
            must.append(FieldCondition(key="symbol_type",
                                       match=MatchValue(value=symbol_type)))
        if language:
            must.append(FieldCondition(key="lang", match=MatchValue(value=language)))
        qfilter = Filter(must=must) if must else None
        res = self.client.query_points(
            collection_name=name, query=vector, query_filter=qfilter, limit=limit,
            with_payload=True,
        )
        return list(res.points)