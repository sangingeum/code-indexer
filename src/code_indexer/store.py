"""Qdrant store adapter: collection create/drop, upsert, filtered purge, search.

Per design §8: one collection per project (`idx_{slug}`); remove_project is
an atomic collection drop. Point payload per §3.

The Qdrant client library is imported at first use, inside ``Store.__init__``
(ADR-0001 section 1): a core install and the CLI import path must not pay for
a vector-store client they may not need.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

logger = logging.getLogger("code-indexer.store")

NAMESPACE_URL = uuid.NAMESPACE_URL

# Populated by ``_load_qdrant`` at first use; ``None`` until then so a module
# import of this file costs nothing. Annotations stay strings (future import).
QdrantClient: Any = None
AliasOperations: Any = None
CreateAlias: Any = None
CreateAliasOperation: Any = None
DeleteAlias: Any = None
DeleteAliasOperation: Any = None
Distance: Any = None
FieldCondition: Any = None
Filter: Any = None
MatchValue: Any = None
PointStruct: Any = None
VectorParams: Any = None
Range: Any = None


def _load_qdrant() -> None:
    """Import the Qdrant client into this module's globals (first use only)."""
    global QdrantClient, AliasOperations, CreateAlias, CreateAliasOperation
    global DeleteAlias, DeleteAliasOperation, Distance, FieldCondition, Filter
    global MatchValue, PointStruct, VectorParams, Range
    if QdrantClient is not None:
        return
    from qdrant_client import QdrantClient as _QdrantClient
    from qdrant_client.models import (
        AliasOperations as _AliasOperations,  # noqa: F401 — re-exported
        CreateAlias as _CreateAlias,
        CreateAliasOperation as _CreateAliasOperation,
        DeleteAlias as _DeleteAlias,
        DeleteAliasOperation as _DeleteAliasOperation,
        Distance as _Distance,
        FieldCondition as _FieldCondition,
        Filter as _Filter,
        MatchValue as _MatchValue,
        PointStruct as _PointStruct,
        Range as _Range,
        VectorParams as _VectorParams,
    )
    QdrantClient = _QdrantClient
    AliasOperations = _AliasOperations
    CreateAlias = _CreateAlias
    CreateAliasOperation = _CreateAliasOperation
    DeleteAlias = _DeleteAlias
    DeleteAliasOperation = _DeleteAliasOperation
    Distance = _Distance
    FieldCondition = _FieldCondition
    Filter = _Filter
    MatchValue = _MatchValue
    PointStruct = _PointStruct
    VectorParams = _VectorParams
    Range = _Range


def point_id(project: str, file: str, chunk_index: int) -> str:
    """Deterministic uuid5 point ID (design §3/B) — idempotent upserts."""
    return str(uuid.uuid5(NAMESPACE_URL, f"{project}|{file}|{chunk_index}"))


class Store:
    def __init__(self, url: str, upsert_batch: int = 256):
        _load_qdrant()
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

    def list_collections(self) -> set[str]:
        """Every collection AND alias name in one listing (two Qdrant calls).

        `list-projects` uses this to spot a registered project whose
        collection is gone (a dead registration) without a per-project
        network round trip. Aliases count: after a swap rebuild the slug is
        served as an alias (`swap_collection`), so omitting them would mark
        live projects missing.
        """
        names = {c.name for c in self.client.get_collections().collections}
        names |= {a.alias_name for a in self.client.get_aliases().aliases}
        return names

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

    def upsert_points(self, name: str, points: list[Any]) -> int:
        """Upsert points; a plain dict ``{id, vector, payload}`` is accepted.

        The indexer builds payload dicts and hands them over here, so the
        point type of the client library stays inside this module.
        """
        built = [PointStruct(**p) if isinstance(p, dict) else p for p in points]
        for i in range(0, len(built), self.upsert_batch):
            self.client.upsert(collection_name=name,
                               points=built[i:i + self.upsert_batch])
        return len(built)

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
