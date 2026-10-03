"""Doctor: installation health-check (robustness work item).

One line per check — ``ok|warn|fail <name>: <detail>`` — aggregated by
``run_doctor``; the CLI exits 1 when any check FAILS (warnings do not fail).
Deliberately a dev/ops surface: CLI-only, no MCP tool (agents have
index-status for per-project state; doctor is for the operator).

Network discipline: the only network calls go to the CONFIGURED URLs
(``cfg.ollama_url``, ``cfg.qdrant_url``) — never anywhere else. Every check
function takes its backends as parameters so tests can pass fakes; each
failure mode is individually testable offline.
"""

from __future__ import annotations

import fcntl
import os
import shutil
import sqlite3
import sys
from dataclasses import dataclass
from typing import Any

import ollama

from .config import Config
from .fingerprint import (current_fingerprint, fingerprint_mismatches,
                          read_fingerprint)

OK = "ok"
WARN = "warn"
FAIL = "fail"

LOW_DISK_BYTES = 1 << 30  # 1 GiB
_MIN_PYTHON = (3, 11)


@dataclass
class Check:
    status: str   # OK | WARN | FAIL
    name: str
    detail: str

    def line(self) -> str:
        return f"{self.status} {self.name}: {self.detail}"

    def to_dict(self) -> dict[str, str]:
        return {"status": self.status, "name": self.name,
                "detail": self.detail}


def any_fail(checks: list[Check]) -> bool:
    return any(c.status == FAIL for c in checks)


def qdrant_client_version() -> str:
    from importlib.metadata import version as _pkg_version
    try:
        return _pkg_version("qdrant-client")
    except Exception:  # noqa: BLE001 — unknown install
        return "unknown"


# ---------------------------------------------------------------------------
# runtime (python / uv)
# ---------------------------------------------------------------------------

def check_runtime(python_version: str | None = None,
                  uv_on_path: bool | None = None) -> list[Check]:
    version = python_version or ".".join(str(p) for p in sys.version_info[:3])
    try:
        major_minor = tuple(int(p) for p in version.split(".")[:2])
    except ValueError:
        major_minor = sys.version_info[:2]
    checks = [Check(
        OK if major_minor >= _MIN_PYTHON else FAIL,
        "python",
        version + ("" if major_minor >= _MIN_PYTHON
                   else f" (>= {_MIN_PYTHON[0]}.{_MIN_PYTHON[1]} required)"))]
    has_uv = shutil.which("uv") if uv_on_path is None else uv_on_path
    checks.append(Check(
        OK if has_uv else WARN, "uv",
        "on PATH" if has_uv else "not on PATH (house toolchain expects uv)"))
    return checks


# ---------------------------------------------------------------------------
# ollama: reachable / model present / dimension probe
# ---------------------------------------------------------------------------

def _ollama_model_names(client: Any) -> list[str]:
    resp = client.list()
    models = (resp.get("models", []) if isinstance(resp, dict)
              else getattr(resp, "models", []))
    names: list[str] = []
    for m in models:
        name = getattr(m, "model", None)
        if name is None and isinstance(m, dict):
            name = m.get("model")
        if name:
            names.append(str(name))
    return names


def check_ollama(cfg: Config, embedder: Any,
                 ollama_client: Any = None) -> tuple[list[Check], int | None]:
    """Returns (checks, probe_dim) — probe_dim is None when the probe failed.

    The caller threads probe_dim into the Qdrant dim comparison.
    """
    checks: list[Check] = []
    created = False
    client = ollama_client
    if client is None:
        client = ollama.Client(host=cfg.ollama_url, timeout=cfg.ollama_timeout)
        created = True
    try:
        names = _ollama_model_names(client)
    except Exception as exc:  # noqa: BLE001 — unreachable backend
        checks.append(Check(FAIL, "ollama-reachable",
                            f"{cfg.ollama_url}: {exc}"))
        return checks, None
    finally:
        if created:
            close = getattr(client, "close", None)
            if close is not None:
                close()
    checks.append(Check(OK, "ollama-reachable",
                        f"{cfg.ollama_url} ({len(names)} models)"))
    if cfg.embed_model in names:
        checks.append(Check(OK, "ollama-model", cfg.embed_model))
    else:
        checks.append(Check(
            FAIL, "ollama-model",
            f"'{cfg.embed_model}' not in Ollama tags; "
            f"run: ollama pull {cfg.embed_model}"))
    try:
        probe_dim = embedder.dimension()
    except Exception as exc:  # noqa: BLE001
        checks.append(Check(FAIL, "ollama-dim", f"probe failed: {exc}"))
        return checks, None
    checks.append(Check(OK, "ollama-dim", f"probe={probe_dim}"))
    return checks, probe_dim


# ---------------------------------------------------------------------------
# qdrant: reachable / version / compat / collections vs registry / dims
# ---------------------------------------------------------------------------

def _parse_version(v: str) -> tuple[int, ...] | None:
    try:
        return tuple(int(p) for p in v.split(".")[:3])
    except (ValueError, AttributeError):
        return None


def _versions_compatible(client_v: str, server_v: str) -> bool:
    c, s = _parse_version(client_v), _parse_version(server_v)
    if c is None or s is None:
        return True  # cannot verify — do not invent a failure
    if c[0] != s[0]:
        return False
    return abs(c[1] - s[1]) <= 1


def _collection_dim(client: Any, name: str) -> int | None:
    try:
        info = client.get_collection(name)
        vectors = info.config.params.vectors
    except Exception:  # noqa: BLE001
        return None
    if hasattr(vectors, "size"):
        return vectors.size
    if isinstance(vectors, dict) and vectors:
        first = next(iter(vectors.values()))
        return getattr(first, "size", None)
    return None


def check_qdrant(cfg: Config, store: Any, registry: Any,
                 probe_dim: int | None = None) -> list[Check]:
    checks: list[Check] = []
    client = store.client
    try:
        server_version = client.info().version
    except Exception as exc:  # noqa: BLE001 — unreachable backend
        checks.append(Check(FAIL, "qdrant-reachable",
                            f"{cfg.qdrant_url}: {exc}"))
        return checks
    checks.append(Check(OK, "qdrant-reachable",
                        f"{cfg.qdrant_url} (server {server_version})"))

    client_version = qdrant_client_version()
    if _versions_compatible(client_version, server_version):
        checks.append(Check(OK, "qdrant-compat",
                            f"client {client_version} vs server {server_version}"))
    else:
        checks.append(Check(
            WARN, "qdrant-compat",
            f"client {client_version} vs server {server_version} — major "
            f"versions should match and minor delta must not exceed 1"))

    slugs = {e.slug for e in registry.list_projects()}
    expected = {f"idx_{s}" for s in slugs}
    try:
        physical = {c.name for c in client.get_collections().collections}
    except Exception as exc:  # noqa: BLE001
        checks.append(Check(FAIL, "qdrant-collections", str(exc)))
        return checks
    alias_map: dict[str, str] = {}
    try:
        alias_map = {a.alias_name: a.collection_name
                     for a in client.get_aliases().aliases}
    except Exception:  # noqa: BLE001 — alias API unavailable
        pass
    # A live index name is either a physical collection or an alias
    # (post-swap-rebuild the alias carries the live name).
    present = physical | set(alias_map)
    missing = sorted(expected - present)
    if missing:
        checks.append(Check(
            FAIL, "qdrant-missing",
            f"registered project(s) without a collection: {', '.join(missing)}; "
            f"run: code-indexer reindex-project <path>"))
    else:
        checks.append(Check(OK, "qdrant-missing",
                            f"all {len(expected)} registered collection(s) present"))
    # Orphans: idx_* names that belong to no registered project. Alias
    # targets of expected aliases (swap rebuilds) are protected.
    protected = {target for alias_name, target in alias_map.items()
                 if alias_name in expected}
    idx_present = {n for n in present if n.startswith("idx_")}
    orphans = sorted(idx_present - expected - protected)
    if orphans:
        checks.append(Check(
            WARN, "qdrant-orphans",
            f"collection(s) with no registry entry: {', '.join(orphans)} "
            f"(leftover rebuild temp or stale registration)"))
    else:
        checks.append(Check(OK, "qdrant-orphans", "none"))

    mismatches = []
    for name in sorted(expected & present):
        dim = _collection_dim(client, name)
        if dim is None:
            continue
        if probe_dim is not None and dim != probe_dim:
            mismatches.append(f"{name}: collection dim {dim} != probe {probe_dim}")
    if mismatches:
        checks.append(Check(FAIL, "qdrant-dims", "; ".join(mismatches)))
    else:
        checks.append(Check(
            OK, "qdrant-dims",
            f"match probe {probe_dim}" if probe_dim is not None and expected
            else "nothing to compare (no collections or probe unavailable)"))
    return checks


# ---------------------------------------------------------------------------
# index root: writable / free disk
# ---------------------------------------------------------------------------

def check_index_root(cfg: Config) -> list[Check]:
    checks: list[Check] = []
    root = cfg.index_root
    probe = os.path.join(root, ".doctor-write-probe")
    try:
        os.makedirs(root, exist_ok=True)
        with open(probe, "w", encoding="utf-8") as f:
            f.write("probe")
        os.remove(probe)
        checks.append(Check(OK, "index-root", f"{root} writable"))
    except OSError as exc:
        checks.append(Check(FAIL, "index-root", f"{root} not writable: {exc}"))
        return checks
    try:
        free = shutil.disk_usage(root).free
    except OSError:
        return checks
    free_gib = free / (1 << 30)
    if free < LOW_DISK_BYTES:
        checks.append(Check(WARN, "disk-free",
                            f"{free_gib:.1f} GiB free (< 1 GiB)"))
    else:
        checks.append(Check(OK, "disk-free", f"{free_gib:.1f} GiB free"))
    return checks


# ---------------------------------------------------------------------------
# sqlite integrity (registry + each manifest)
# ---------------------------------------------------------------------------

def _sqlite_integrity(path: str) -> tuple[str, str]:
    try:
        conn = sqlite3.connect(path)
        try:
            rows = conn.execute("PRAGMA integrity_check").fetchall()
        finally:
            conn.close()
    except sqlite3.DatabaseError as exc:
        return FAIL, f"{os.path.basename(path)}: {exc}"
    if rows and all(r[0] == "ok" for r in rows):
        return OK, os.path.basename(path)
    return FAIL, f"{os.path.basename(path)}: {rows!r}"


def check_sqlite(cfg: Config) -> list[Check]:
    checks: list[Check] = []
    candidates = [os.path.join(cfg.index_root, "registry.db")]
    if os.path.isdir(cfg.index_root):
        for name in sorted(os.listdir(cfg.index_root)):
            candidate = os.path.join(cfg.index_root, name, "manifest.db")
            if os.path.isfile(candidate):
                candidates.append(candidate)
    for path in candidates:
        if not os.path.isfile(path):
            continue  # created lazily; nothing to check yet
        status, detail = _sqlite_integrity(path)
        label = ("sqlite-registry" if path.endswith("registry.db")
                 else f"sqlite-{os.path.basename(os.path.dirname(path))}")
        checks.append(Check(status, label, detail))
    return checks


# ---------------------------------------------------------------------------
# locks / watch pid liveness
# ---------------------------------------------------------------------------

def check_locks(cfg: Config) -> list[Check]:
    checks: list[Check] = []
    root = cfg.index_root
    pid_path = os.path.join(root, "watch.pid")
    if os.path.isfile(pid_path):
        pid: int | None = None
        try:
            with open(pid_path, encoding="utf-8") as f:
                pid = int(f.read().strip())
        except (OSError, ValueError):
            pid = None
        alive = False
        if pid is not None:
            try:
                os.kill(pid, 0)
                alive = True
            except ProcessLookupError:
                alive = False
            except PermissionError:
                alive = True  # exists, owned by another user
        if alive:
            checks.append(Check(OK, "watch", f"running (pid {pid})"))
        else:
            checks.append(Check(
                WARN, "watch",
                f"stale pidfile (pid {pid} not running); run: code-indexer unwatch"))
    else:
        checks.append(Check(OK, "watch", "not running (pidfile absent)"))

    held: list[str] = []
    if os.path.isdir(root):
        for name in sorted(os.listdir(root)):
            if not name.endswith(".lock"):
                continue
            path = os.path.join(root, name)
            try:
                fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o644)
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    fcntl.flock(fd, fcntl.LOCK_UN)
                except OSError:
                    held.append(name)
                finally:
                    os.close(fd)
            except OSError:
                pass  # unreadable lock file — not a health failure
    if held:
        checks.append(Check(
            OK, "locks",
            f"held by an active index pass: {', '.join(held)}"))
    else:
        checks.append(Check(OK, "locks", "free"))
    return checks


# ---------------------------------------------------------------------------
# per-project fingerprint status
# ---------------------------------------------------------------------------

class _MetaView:
    """Minimal get_meta adapter over a raw meta mapping (no migration writes
    — doctor must not mutate manifests it only inspects)."""

    def __init__(self, mapping: dict[str, Any]):
        self._mapping = mapping

    def get_meta(self, key: str) -> str | None:
        value = self._mapping.get(key)
        return str(value) if value is not None else None


def _read_meta_mapping(db_path: str) -> dict[str, Any] | None:
    try:
        conn = sqlite3.connect(db_path)
        try:
            return dict(conn.execute("SELECT key, value FROM meta"))
        finally:
            conn.close()
    except sqlite3.DatabaseError:
        return None


def check_fingerprints(cfg: Config, registry: Any, embedder: Any) -> list[Check]:
    try:
        current = current_fingerprint(cfg.embed_model, embedder.dimension())
    except Exception as exc:  # noqa: BLE001 — backend down
        return [Check(WARN, "fingerprint",
                      f"skipped: embedding dimension unavailable ({exc})")]
    entries = registry.list_projects()
    if not entries:
        return [Check(OK, "fingerprint", "no projects registered")]
    issues: list[str] = []
    for entry in entries:
        mdir = os.path.join(cfg.index_root, entry.slug, "manifest.db")
        if not os.path.isfile(mdir):
            continue  # never-indexed: index-status covers that state
        mapping = _read_meta_mapping(mdir)
        if mapping is None:
            continue  # unreadable/corrupt: the sqlite check reports it
        recorded = read_fingerprint(_MetaView(mapping))
        if recorded is None:
            continue  # legacy manifest: backfills on the next pass
        diffs = fingerprint_mismatches(recorded, current)
        if diffs:
            issues.append(f"{entry.slug} ({entry.path}): {'; '.join(diffs)}")
    if issues:
        return [Check(
            WARN, "fingerprint",
            "needs-reindex — " + "; ".join(issues)
            + "; run: code-indexer reindex-project <path>")]
    return [Check(OK, "fingerprint",
                  f"{len(entries)} project(s) match the current config")]


# ---------------------------------------------------------------------------
# aggregation
# ---------------------------------------------------------------------------

def run_doctor(cfg: Config, registry: Any, embedder: Any, store: Any,
               ollama_client: Any = None) -> list[Check]:
    checks: list[Check] = []
    checks += check_runtime()
    ollama_checks, probe_dim = check_ollama(cfg, embedder, ollama_client)
    checks += ollama_checks
    checks += check_qdrant(cfg, store, registry, probe_dim)
    checks += check_index_root(cfg)
    checks += check_sqlite(cfg)
    checks += check_locks(cfg)
    checks += check_fingerprints(cfg, registry, embedder)
    return checks