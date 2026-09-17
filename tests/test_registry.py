"""Registry migration, custom-name slugs, and sanitize rules."""

from __future__ import annotations

import pytest

from code_indexer.registry import Registry, sanitize_name, slug_for


@pytest.fixture()
def reg(tmp_path):
    r = Registry(str(tmp_path / "registry.db"))
    yield r
    r.close()


def test_sanitize_name():
    assert sanitize_name("my-project") == "my-project"
    assert sanitize_name("  Vivarium Docs ") == "Vivarium_Docs"
    assert sanitize_name("a/b\\c:d") == "a_b_c_d"
    assert sanitize_name("x" * 100) == "x" * 64  # truncated to 64
    with pytest.raises(ValueError):
        sanitize_name("///")


def test_add_with_custom_name(tmp_path, reg):
    proj = tmp_path / "proj"
    proj.mkdir()
    entry = reg.add(str(proj), name="vivarium")
    assert entry.slug == "vivarium"
    assert entry.name == "vivarium"
    assert reg.get_by_slug("vivarium") is not None
    assert reg.get_by_name("vivarium") is not None


def test_add_default_slug_unchanged(tmp_path, reg):
    proj = tmp_path / "proj2"
    proj.mkdir()
    entry = reg.add(str(proj))
    assert entry.slug == slug_for(str(proj))
    assert entry.name is None


def test_name_collision_rejected(tmp_path, reg):
    p1 = tmp_path / "a"
    p2 = tmp_path / "b"
    for p in (p1, p2):
        p.mkdir()
    reg.add(str(p1), name="dupe")
    with pytest.raises(ValueError, match="collision"):
        reg.add(str(p2), name="dupe")


def test_name_collides_with_existing_hash_slug(tmp_path, reg):
    """A custom name equal to another project's hash slug is rejected."""
    p1 = tmp_path / "a"
    p1.mkdir()
    p2 = tmp_path / "b"
    p2.mkdir()
    e1 = reg.add(str(p1))
    with pytest.raises(ValueError, match="collision"):
        reg.add(str(p2), name=e1.slug)


def test_duplicate_path_still_rejected_with_name(tmp_path, reg):
    proj = tmp_path / "a"
    proj.mkdir()
    reg.add(str(proj), name="one")
    with pytest.raises(ValueError, match="already registered"):
        reg.add(str(proj), name="two")


def test_migration_from_v1_db(tmp_path):
    """A v1 DB (no name column) opens cleanly and gains the column."""
    import sqlite3

    db = str(tmp_path / "v1.db")
    conn = sqlite3.connect(db)
    conn.executescript(
        "CREATE TABLE projects (path TEXT PRIMARY KEY, slug TEXT NOT NULL UNIQUE,"
        " created_at REAL NOT NULL);"
        "INSERT INTO projects VALUES ('/tmp/legacy', 'deadbeef', 1.0);"
    )
    conn.commit()
    conn.close()

    r = Registry(db)
    try:
        cols = [row[1] for row in r._conn.execute("PRAGMA table_info(projects)")]
        assert "name" in cols
        entries = r.list_projects()
        assert len(entries) == 1
        assert entries[0].slug == "deadbeef"
        assert entries[0].name is None
    finally:
        r.close()
