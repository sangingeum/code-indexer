"""Audit probe: check live services + sample tool outputs (plan §2, §3, §11)."""
import json
import urllib.request

for url in ("http://192.168.1.105:6333/collections",
            "http://192.168.1.103:11434/api/tags"):
    try:
        print(url, urllib.request.urlopen(url, timeout=5).read()[:400])
    except Exception as e:
        print(url, "ERR", e)
