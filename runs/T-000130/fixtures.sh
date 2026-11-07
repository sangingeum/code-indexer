#!/usr/bin/env bash
# Fixtures for the gate: a genuinely stale project, a stale+withheld project,
# a fresh project, an unknown-verdict project and a three-kind stale project.
# Built once with the TIP binary; read by BOTH binaries from the same index.
# Run from a scratch directory: S, TIP and BASE are $PWD-relative.
set -u
S=${S:-$PWD/sandbox}
TIP=${TIP:-$PWD/tip/.venv/bin/code-indexer}
BASE=${BASE:-$PWD/base/.venv/bin/code-indexer}
rm -rf "$S"
mkdir -p "$S/home" "$S/index" "$S/proj" "$S/proj2" "$S/proj3" "$S/proj4"

printf 'def alpha():\n    return 1\n' > "$S/proj/alpha.py"
printf 'def beta():\n    return 2\n' > "$S/proj/beta.py"

printf 'def one():\n    return 1\n' > "$S/proj2/one.py"
# synthetic, publicly documented AWS example key shaped to trip the content layer
printf 'aws_access_key_id = AKIAIOSFODNN7EXAMPLE\n' > "$S/proj2/notes.md"

printf 'def solo():\n    return 0\n' > "$S/proj3/solo.py"
printf 'def four():\n    return 4\n' > "$S/proj4/four.py"

export HOME="$S/home"
export INDEX_ROOT="$S/index"

run() { "$@"; echo "exit=$?"; }

echo "## add-project (tip binary, vectors off)"
run "$TIP" add-project "$S/proj"  --vectors off
echo "## add-project proj2 (holds a sensitive-content file)"
run "$TIP" add-project "$S/proj2" --vectors off
echo "## add-project proj3 (fresh)"
run "$TIP" add-project "$S/proj3" --vectors off
echo "## add-project proj4 (will be made unknown)"
run "$TIP" add-project "$S/proj4" --vectors off

echo "## now make proj stale (gamma.py added) and proj2 double-faulted (extra.py added)"
printf 'def gamma():\n    return 3\n' > "$S/proj/gamma.py"
printf 'def extra():\n    return 9\n' > "$S/proj2/extra.py"

echo "## list-projects (slugs)"
"$TIP" list-projects

echo "## proj5: three kinds at once (modified, added, deleted)"
mkdir -p "$S/proj5"
printf 'def a():\n    return 1\n' > "$S/proj5/a.py"
printf 'def b():\n    return 2\n' > "$S/proj5/b.py"
printf 'def c():\n    return 3\n' > "$S/proj5/c.py"
"$TIP" add-project "$S/proj5" --vectors off >/dev/null 2>&1
printf 'def a():\n    return 11\n' > "$S/proj5/a.py"
printf 'def d():\n    return 4\n' > "$S/proj5/d.py"
rm "$S/proj5/b.py"
