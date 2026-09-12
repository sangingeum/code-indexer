"""Connectivity probe: check Ollama + Qdrant reachability (env-driven URLs)."""

import os
import urllib.request

OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://192.168.X.X:11434")
QDRANT_URL = os.environ.get("QDRANT_URL", "http://192.168.X.X:6333")

for url in [f"{OLLAMA_URL}/api/version", f"{QDRANT_URL}/collections"]:
    try:
        with urllib.request.urlopen(url, timeout=5) as r:
            print(url, "->", r.read(300).decode())
    except Exception as e:
        print(url, "ERROR:", e)