"""M0 benchmark: embed 50 chunks via Ollama, upsert into a throwaway Qdrant
collection, and report real throughput (docs/sec)."""

from __future__ import annotations

import sys
import time
import uuid

sys.path.insert(0, "src")

from mcp_code_indexer.config import load_config
from mcp_code_indexer.embedder import Embedder
from mcp_code_indexer.store import Store

from qdrant_client.models import Distance, PointStruct, VectorParams


def main() -> None:
    cfg = load_config()
    embedder = Embedder(cfg.ollama_url, cfg.embed_model, batch_size=cfg.embed_batch)

    # 50 synthetic "code chunks" (~600 chars each, realistic text).
    base = (
        "def handle_request(self, request: Request) -> Response:\n"
        "    \"\"\"Process an incoming HTTP request and dispatch to handlers.\"\"\"\n"
        "    payload = request.json()\n"
        "    if not payload:\n"
        "        return Response(status_code=400, content={'error': 'empty'})\n"
        "    action = payload.get('action', 'query')\n"
        "    handler = self._handlers.get(action)\n"
        "    if handler is None:\n"
        "        return Response(status_code=404, content={'error': 'unknown action'})\n"
        "    try:\n"
        "        result = handler(request.context)\n"
        "    except ValidationError as exc:\n"
        "        return Response(status_code=422, content={'error': str(exc)})\n"
        "    return Response(status_code=200, content=result)\n"
    )
    chunks = [base + f"# variant {i} — logging note {i}" for i in range(50)]

    t0 = time.time()
    vectors = embedder.embed(chunks)
    embed_s = time.time() - t0
    dim = len(vectors[0])
    print(f"embedding: 50 chunks in {embed_s:.2f}s -> {50 / embed_s:.2f} docs/s "
          f"(dim={dim}, batch_size={cfg.embed_batch})")

    store = Store(cfg.qdrant_url, upsert_batch=cfg.upsert_batch)
    coll = f"bench_{uuid.uuid4().hex[:8]}"
    store.client.create_collection(
        collection_name=coll,
        vectors_config=VectorParams(size=dim, distance=Distance.COSINE),
    )
    points = [
        PointStruct(id=str(uuid.uuid4()), vector=v, payload={"i": i})
        for i, v in enumerate(vectors)
    ]
    t1 = time.time()
    store.upsert_points(coll, points)
    upsert_s = time.time() - t1
    count = store.count_points(coll)
    print(f"upsert: {len(points)} points in {upsert_s:.2f}s -> {len(points) / upsert_s:.2f} pts/s; "
          f"count={count}")
    store.drop_collection(coll)
    print(f"total pipeline: {(embed_s + upsert_s):.2f}s, end-to-end "
          f"{50 / (embed_s + upsert_s):.2f} docs/s")


if __name__ == "__main__":
    main()