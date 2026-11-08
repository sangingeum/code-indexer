"""A value binding that shadows a type name is not a type reference.

The C# query captures names written where a type belongs, and the index pass
resolves them by name. A name the file binds as a VALUE shadows the type it is
spelled like -- a generic type parameter, a property, a field, a local -- so a
capture that fires on the shadowed spelling resolves to the type and invents a
`type` row for something that is not a type use.

The extractor already keeps parameters, locals, foreach and catch names out of
the member-access receiver rule, and this file pins the two widenings: a
property name and a field name join that set, and a generic type parameter
suppresses the type-position captures as well (it shadows the type across the
whole declaring type, not only where it is read). The documented limit stays:
a value the walk cannot see is still indistinguishable from the type by name
alone.

Offline: real chunker + queries, stub embedder and store, no network.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


from code_indexer.config import Config  # noqa: E402
from code_indexer.core import Core  # noqa: E402
from code_indexer.registry import Registry  # noqa: E402

# `T` is a type here AND a generic parameter in Box<T>: the parameter wins
# inside Box, so no name written in Box is a type use.
SHADOWED_TYPE_PARAMETER = {
    "T.cs": "namespace App { public class T { } }\n",
    "Box.cs": """\
namespace App
{
    public class Box<T>
    {
        private T _held;
        public T Value { get; set; }
        public T Get(T other) { return other; }
    }
}
""",
}

# `NeedBand` is a type, and a property, a field and a local are each spelled
# with its name. One file per binding kind, so one binding cannot hide
# another: the walk is file-wide, and a local named `NeedBand` in one file
# would keep a property of the same name in that file from being seen at all.
# In each file line 5 (or line 7, for the local) names the TYPE and the line
# after it reads the VALUE.
SHADOWED_BY_PROPERTY = """\
namespace App
{
    public class PropertyUser
    {
        public NeedBand NeedBand { get; set; }
        public double Read() { return NeedBand.Critical; }
    }
}
"""

SHADOWED_BY_FIELD = """\
namespace App
{
    public class FieldUser
    {
        private NeedBand NeedBand;
        public double Read() { return NeedBand.Critical; }
    }
}
"""

SHADOWED_BY_LOCAL = """\
namespace App
{
    public class LocalUser
    {
        public double Read()
        {
            NeedBand NeedBand = null;
            return NeedBand.Critical;
        }
    }
}
"""

NEED_BAND_TYPE = """\
namespace App
{
    public class NeedBand { public static double Critical; }
}
"""


def _rig(tmp_path, files: dict[str, str], name: str):
    cfg = Config(ollama_url="", qdrant_url="", embed_model="stub",
                 index_root=str(tmp_path / "state"),
                 embed_batch=48, upsert_batch=256, max_file_bytes=1048576,
                 watch_debounce=3, watch_sweep_interval=300)
    project = tmp_path / "proj"
    project.mkdir(parents=True, exist_ok=True)
    for rel, text in files.items():
        (project / rel).parent.mkdir(parents=True, exist_ok=True)
        (project / rel).write_text(text, encoding="utf-8")
    core = Core(cfg, skip_stale_check=True)

    class StubEmbedder:
        def dimension(self):
            return 4

        def embed(self, texts):
            return [[0.1] * 4 for _ in texts]

        def query_instruction_text(self, q):
            return q

        def embed_with_errors(self, texts):
            return [[0.1] * 4 for _ in texts], []

        def close(self):
            pass

    class StubStore:
        def collection_exists(self, name):
            return True

        def create_collection(self, name, dim):
            pass

        def upsert_points(self, name, points):
            pass

        def purge_file_points(self, *a, **kw):
            return 0

        def swap_collection(self, tmp, target):
            pass

        def count_points(self, name):
            return 0

        def search(self, *a, **kw):
            return []

    core.embedder = StubEmbedder()  # type: ignore[assignment]
    core.store = StubStore()  # type: ignore[assignment]
    core.indexer.embedder = core.embedder  # type: ignore[assignment]
    core.indexer.store = core.store  # type: ignore[assignment]
    reg = Registry(os.path.join(cfg.index_root, "registry.db"))
    entry = reg.add(str(project), name=name)
    core.registry = reg
    core.run_index(entry.slug, str(project))
    return core, entry, reg


def test_a_generic_parameter_that_shadows_a_type_is_no_type_row(tmp_path):
    """`class Box<T>` in a namespace that also declares `class T`: T inside
    Box is the parameter, so nothing written there is a type use."""
    core, entry, reg = _rig(tmp_path, SHADOWED_TYPE_PARAMETER, name="shadowT")
    try:
        m = core.read_manifest(entry.slug)
        try:
            rows = m._conn.execute(
                "SELECT file, line, relationship FROM refs"
                " WHERE to_name = 'T'").fetchall()
        finally:
            m.close()
    finally:
        reg.close()
    assert rows == [], rows


def test_a_property_a_field_and_a_local_that_shadow_a_type_are_no_type_rows(
        tmp_path):
    """Three value bindings spelled `NeedBand`, one per file: the receiver rule
    keeps their READS out, and the type each one is declared with stays a type
    use."""
    core, entry, reg = _rig(tmp_path, {
        "App/NeedBand.cs": NEED_BAND_TYPE,
        "App/PropertyUser.cs": SHADOWED_BY_PROPERTY,
        "App/FieldUser.cs": SHADOWED_BY_FIELD,
        "App/LocalUser.cs": SHADOWED_BY_LOCAL,
    }, name="shadowv")
    try:
        m = core.read_manifest(entry.slug)
        try:
            rows = m._conn.execute(
                "SELECT file, line, relationship FROM refs"
                " WHERE to_name = 'NeedBand' ORDER BY file, line").fetchall()
        finally:
            m.close()
    finally:
        reg.close()
    assert rows == [("App/FieldUser.cs", 5, "type"),
                    ("App/LocalUser.cs", 7, "type"),
                    ("App/PropertyUser.cs", 5, "type")], rows
