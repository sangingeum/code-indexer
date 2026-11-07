"""Does the preview path refuse a plan that fails its own hash?

Builds a real plan for a throwaway project, then changes one member so the
document no longer hashes to the id its file name carries, and asks both read
paths the same question. No host path is written down: the checkout is
located from this file's own position, and the fixture is a temp directory.
"""

import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(REPO, "src"))

from code_indexer import plans  # noqa: E402

SOURCE = "def alpha():\n    return 1\n\n\ndef beta():\n    return 2\n"

root = tempfile.mkdtemp(prefix="showhash-")
with open(os.path.join(root, "sample.py"), "w", encoding="utf-8") as fh:
    fh.write(SOURCE)

tf = plans.read_text_file(root, "sample.py")
derived, verdict = plans.declarations("sample.py", tf.text)
decl = plans.find_declaration(derived, "sample.py", "alpha", "function", None)
edit = plans.replace_body_edit(tf, decl, "    return 41")
document = plans.build_document(
    operation="replace_body",
    repository={"project": "scratch", "index_revision": "unknown"},
    files=[plans.file_record(tf, "python")],
    target=plans.locator(tf, decl, "python"),
    edits=[edit])

plan_dir = os.path.join(root, "plans")
plans.write_plan(document, plan_dir)
written = os.path.join(plan_dir, plans.plan_file_name(document["plan_id"]))

# Tamper: one member differs, so the bytes no longer hash to plan_id.
tampered = dict(document)
tampered["created_at"] = "2000-01-01T00:00:00Z"
with open(written, "wb") as fh:
    fh.write(plans.canonical_bytes(tampered))

requested, path = plans.resolve_ref(document["plan_id"], plan_dir, root)
loaded = plans.load_plan(path)
print("ref offered      :", requested)
print("file name behind :", os.path.basename(path))
print("content hashes to:", plans.plan_id_of(loaded))

for name, call in (
        ("plan-validate", lambda: plans.validate_plan(loaded, root,
                                                     requested=requested)),
        ("plan-show --diff", lambda: plans.preview(loaded, root))):
    try:
        out = call()
    except Exception as exc:  # noqa: BLE001 - the refusal is the observation
        print(f"{name:16s}: REFUSED {type(exc).__name__}: {exc}")
    else:
        if name == "plan-validate":
            print(f"{name:16s}: passed={out['passed']}")
        else:
            print(f"{name:16s}: ACCEPTED, diff bytes={len(out[0]['diff'])}")
