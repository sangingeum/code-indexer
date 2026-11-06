"""LIVE re-verify: does `remove-project` reclaim everything the project owns?

Drives the shipped CLI against the real embedding service and the real vector
store, and asks Qdrant itself what it holds before and after each command.
Three observations:

1. a project whose alias serves an identity-stamped collection, plus a
   collection left under an older identity -> both reclaimed, alias gone, and
   the printed line names exactly the collections that disappeared;
2. a project with no collection -> exit 0, no drop claimed;
3. whether Qdrant drops an alias by itself when its collection is deleted
   (i.e. whether the deleter's alias-first order is load-bearing) -- measured
   on a throwaway pair, not on the project under test.

Ends by proving the store's collection list is back to its pre-probe state.

Usage: OLLAMA_URL=<embedding> QDRANT_URL=<vector store> \
         uv run python runs/round2-remove-project-reverify/probe_remove_project_live.py
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
SRC = os.path.join(REPO, "src")
QDRANT = os.environ.get("QDRANT_URL", "http://localhost:6333").rstrip("/")
OLLAMA = os.environ.get("OLLAMA_URL", "http://localhost:11434").rstrip("/")
FAILURES: list[str] = []


class MaskedStream:
    def __init__(self, stream, root):
        self.stream, self.root = stream, root

    def write(self, text):
        return self.stream.write(text.replace(self.root, "<workdir>")
                                 .replace(QDRANT, "<qdrant>")
                                 .replace(OLLAMA, "<embedding>"))

    def flush(self):
        self.stream.flush()


def api(path, method="GET", payload=None):
    data = None if payload is None else json.dumps(payload).encode()
    req = urllib.request.Request(QDRANT + path, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        body = resp.read().decode()
    return json.loads(body) if body else {}


def all_collections() -> list[str]:
    return sorted(c["name"] for c in api("/collections")["result"]["collections"])


def all_aliases() -> dict[str, str]:
    return {a["alias_name"]: a["collection_name"]
            for a in api("/aliases")["result"]["aliases"]}


def with_slug(slug):
    cols = [c for c in all_collections() if slug in c]
    aliases = {a: t for a, t in all_aliases().items() if slug in a or slug in t}
    return cols, aliases


def points(name):
    try:
        return api(f"/collections/{name}/points/count", "POST",
                   {"exact": True})["result"]["count"]
    except Exception as exc:  # noqa: BLE001
        return f"<{type(exc).__name__}>"


def vector_size(name):
    cfg = api(f"/collections/{name}")["result"]["config"]["params"]["vectors"]
    if isinstance(cfg, dict):
        return cfg.get("size")
    return list(cfg.values())[0]["size"]


def run(env, cwd, *argv):
    e = dict(env)
    e["PYTHONPATH"] = SRC + (os.pathsep + e["PYTHONPATH"] if e.get("PYTHONPATH") else "")
    proc = subprocess.run([sys.executable, "-m", "code_indexer.cli", *argv],
                          cwd=cwd, env=e, capture_output=True, text=True,
                          timeout=900)
    print(f"  $ code-indexer {' '.join(argv)}  ->  exit={proc.returncode}")
    for line in (proc.stdout + proc.stderr).splitlines():
        print("    | " + line)
    return proc


def check(label, ok, detail=""):
    print(f"  [{'ok  ' if ok else 'FAIL'}] {label}{(' — ' + detail) if detail else ''}")
    if not ok:
        FAILURES.append(label)


def cleanup(names, aliases):
    for name in names:
        try:
            api(f"/collections/{name}", "DELETE")
        except Exception:  # noqa: BLE001
            pass
    for alias in aliases:
        try:
            api("/collections/aliases", "POST", {"actions": [
                {"delete_alias": {"alias_name": alias}}]})
        except Exception:  # noqa: BLE001
            pass


def main() -> int:
    print("=" * 74)
    print("  LIVE  remove-project vs the collections a project owns")
    print("=" * 74)
    print(f"  embedding service: {OLLAMA}   vector store: {QDRANT}")
    store_before_cols = all_collections()
    store_before_aliases = all_aliases()
    print(f"  store before probe: collections={len(store_before_cols)} "
          f"aliases={len(store_before_aliases)}")

    root = tempfile.mkdtemp(prefix="remove-reverify-", dir=os.environ.get("TMPDIR"))
    sys.stdout = MaskedStream(sys.stdout, root)
    state_dir = os.path.join(root, "state")
    os.makedirs(state_dir)
    env = os.environ.copy()
    env["INDEX_ROOT"] = state_dir
    slugs = []
    try:
        print("\n-- case A: a pass, an older identity's leftover, then remove ")
        project = os.path.join(root, "proj")
        os.makedirs(project)
        with open(os.path.join(project, "a.py"), "w", encoding="utf-8") as fh:
            fh.write("def alpha(x):\n    return x + 1\n\n\ndef beta(y):\n"
                     "    return alpha(y) * 2\n")
        proc = run(env, project, "add-project", project, "--vectors", "on")
        if proc.returncode != 0:
            print("  add-project failed; cannot continue")
            return 2
        slug = None
        for line in proc.stdout.splitlines():
            if "(slug " in line:
                slug = line.split("(slug ")[1].rstrip(")").strip()
        slugs.append(slug)
        cols_before, aliases_before = with_slug(slug)
        print(f"    slug={slug}")
        print(f"    after the pass: collections={cols_before} aliases={aliases_before}")
        physical = aliases_before.get(f"idx_{slug}")
        check("the pass left an alias over a physical collection",
              bool(physical) and physical in cols_before, f"alias -> {physical}")
        live_points = points(physical) if physical else 0
        print(f"    points in {physical}: {live_points}")
        check("the collection carries embedded points",
              isinstance(live_points, int) and live_points > 0, str(live_points))

        stale = f"idx_{slug}__0f0f0f0f"
        size = vector_size(physical)
        api(f"/collections/{stale}", "PUT", {"vectors": {"size": size,
                                                          "distance": "Cosine"}})
        print(f"    seeded a leftover from an older identity: {stale} "
              f"(size={size}, 0 points)")
        cols_before, aliases_before = with_slug(slug)
        print(f"    before remove-project: collections={cols_before} "
              f"aliases={aliases_before}")
        check("two collections carry the slug before the delete",
              len(cols_before) == 2, str(cols_before))

        proc = run(env, project, "remove-project", project)
        cols_after, aliases_after = with_slug(slug)
        print(f"    after remove-project: collections={cols_after} "
              f"aliases={aliases_after}")
        gone = sorted(set(cols_before) - set(cols_after))
        check("every collection the project owned is gone",
              not cols_after and not aliases_after,
              f"remaining={cols_after}/{aliases_after}")
        check("the alias is gone with them", not aliases_after, str(aliases_after))
        message = (proc.stdout or "").strip()
        check("exit 0", proc.returncode == 0, str(proc.returncode))
        check("the printed line names every dropped collection",
              all(name in message for name in gone), f"claimed vs gone={gone}")
        print(f"    claimed drops == collections that disappeared: "
              f"{all(n in message for n in gone)} ({len(gone)} collection(s))")

        projects = run(env, project, "list-projects", "--json")
        check("the throwaway project is unregistered",
              slug not in (projects.stdout or ""), slug)

        print("\n-- case B: a project with no collection ")
        plain = os.path.join(root, "plain")
        os.makedirs(plain)
        with open(os.path.join(plain, "b.py"), "w", encoding="utf-8") as fh:
            fh.write("def gamma():\n    return 3\n")
        proc = run(env, plain, "add-project", plain, "--vectors", "off")
        slug_b = None
        for line in proc.stdout.splitlines():
            if "(slug " in line:
                slug_b = line.split("(slug ")[1].rstrip(")").strip()
        slugs.append(slug_b)
        cols_b, aliases_b = with_slug(slug_b)
        print(f"    slug={slug_b} collections={cols_b} aliases={aliases_b}")
        check("a vectors-off project left no collection",
              not cols_b and not aliases_b, f"{cols_b}/{aliases_b}")
        proc = run(env, plain, "remove-project", plain)
        message_b = proc.stdout or ""
        check("case B exit 0", proc.returncode == 0, str(proc.returncode))
        check("case B claims no drop", "dropped" not in message_b, message_b.strip())
        check("case B says there was nothing to drop",
              "no vector collection to drop" in message_b, message_b.strip())

        print("\n-- case C: a collection left under the pre-rename plain name ")
        old_style = os.path.join(root, "oldstyle")
        os.makedirs(old_style)
        with open(os.path.join(old_style, "c.py"), "w", encoding="utf-8") as fh:
            fh.write("def delta():\n    return 4\n")
        proc = run(env, old_style, "add-project", old_style, "--vectors", "on")
        slug_c = None
        for line in proc.stdout.splitlines():
            if "(slug " in line:
                slug_c = line.split("(slug ")[1].rstrip(")").strip()
        slugs.append(slug_c)
        cols_c, aliases_c = with_slug(slug_c)
        # Put the store back into the shape a pre-rename index left: one plain
        # collection named idx_<slug>, no alias at all.
        cleanup(cols_c, list(aliases_c))
        api(f"/collections/idx_{slug_c}", "PUT",
            {"vectors": {"size": 4, "distance": "Cosine"}})
        cols_c, aliases_c = with_slug(slug_c)
        print(f"    slug={slug_c} collections={cols_c} aliases={aliases_c}")
        check("the pre-rename shape is one plain collection, no alias",
              cols_c == [f"idx_{slug_c}"] and not aliases_c,
              f"{cols_c}/{aliases_c}")
        proc = run(env, old_style, "remove-project", old_style)
        cols_c, aliases_c = with_slug(slug_c)
        print(f"    after remove-project: collections={cols_c} aliases={aliases_c}")
        check("the pre-rename collection is reclaimed",
              not cols_c and not aliases_c, f"{cols_c}/{aliases_c}")
        check("the printed line names the pre-rename collection",
              f"collection idx_{slug_c} dropped" in (proc.stdout or ""),
              (proc.stdout or "").strip())

        print("\n-- does Qdrant drop an alias when its collection is deleted? ")
        col = "zzorderaliasprobe_live"
        alias = "zzorderaliasprobe_alias"
        api(f"/collections/{col}", "PUT", {"vectors": {"size": 4,
                                                        "distance": "Cosine"}})
        api("/collections/aliases", "POST", {"actions": [
            {"create_alias": {"collection_name": col, "alias_name": alias}}]})
        before_alias = all_aliases().get(alias)
        api(f"/collections/{col}", "DELETE")
        after_alias = all_aliases().get(alias)
        print(f"    alias {alias} -> {before_alias} before the collection delete")
        print(f"    alias {alias} -> {after_alias} after the collection delete")
        check("the measurement is conclusive either way",
              before_alias == col, str(before_alias))
        conclusion = ("Qdrant removes a pointing alias by itself, so alias-first is "
                      "not load-bearing for the end state" if after_alias is None else
                      "Qdrant leaves the alias dangling, so alias-first IS load-bearing")
        print("    conclusion: " + conclusion)
        if after_alias is not None:
            cleanup([], [alias])
    finally:
        leftovers_cols = [c for c in all_collections()
                          if any(s and s in c for s in slugs)]
        leftovers_aliases = {a: t for a, t in all_aliases().items()
                             if any(s and (s in a or s in t) for s in slugs)}
        if leftovers_cols or leftovers_aliases:
            print(f"\n  cleaning residue: collections={leftovers_cols} "
                  f"aliases={list(leftovers_aliases)}")
            cleanup(leftovers_cols, list(leftovers_aliases))
        shutil.rmtree(root, ignore_errors=True)

    store_after_cols = all_collections()
    store_after_aliases = all_aliases()
    print(f"\n  store after probe:  collections={len(store_after_cols)} "
          f"aliases={len(store_after_aliases)}")
    check("the store's collection list is back to its pre-probe state",
          store_after_cols == store_before_cols,
          f"before={store_before_cols} after={store_after_cols}")
    check("the store's alias list is back to its pre-probe state",
          store_after_aliases == store_before_aliases,
          f"before={store_before_aliases} after={store_after_aliases}")

    verdict = "PASS" if not FAILURES else "FAIL -> " + "; ".join(FAILURES)
    print("\n  VERDICT: " + verdict)
    return 0 if not FAILURES else 1


if __name__ == "__main__":
    sys.exit(main())
