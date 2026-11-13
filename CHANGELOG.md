# Changelog

## [Unreleased] — reindex-project parses a tree its rows no longer describe

**The staleness warning now names a repair that clears it.** The vectors-on arm
of `reindex-project` took the cheap path -- embed the stored chunks, swap the
identity-stamped collection in, parse nothing -- whenever
`vectors_policy.rows_current` held. That is a BOOKKEEPING fact: a pass
completed and its recorded `extraction_version` is this build's. It says
nothing about the tree. So on a project whose rows were current but which had
gained and changed files, the command embedded the old chunks, walked nothing
and returned `files_indexed: 0`, and the warning it is named as the repair for
was unchanged: a permanent stale banner on a healthy tree. The cheap arm is now
taken only when the staleness diff is `fresh`. `Core.verdict_against_tree` is
ONE implementation shared by the read-side check and by
`Core.tree_needs_a_pass`, so the banner and the repair diff the same rows
against the same walk; a rows-current project whose tree has moved on takes the
full parse, and `rows_current` alone never decides whether a pass parses.

Two answers keep the rows instead of parsing: `fresh` (nothing to parse) and
every `unknown` state -- a walk budget stop, or a tree this process cannot
enter. An unknown verdict is no evidence of a change, and a parse taken on no
evidence would read every file it could not walk as `deleted` and purge its
rows. The cheap arm's promise to run with the source tree absent or unreadable
is unchanged and pinned by its own test. The vectors-off arm is untouched, and
a failure before the swap still leaves the old collection serving reads.

Measured on the fixture probe (`runs/reindex-tree-repair/probe.py`: two files,
a stub embedder and store at the Core seam, real scanner/manifest/staleness):
after one added and one modified file the verdict reads `stale` with reasons
`alpha.py modified` and `beta.py added`; one `reindex-project` returns
`files_indexed 2`, `chunks_embedded 2`, `files_committed 2`, `files_deleted 0`
and the verdict reads `fresh`; a second pass on the unchanged tree returns
`files_indexed 0` -- the vector rebuild that parses nothing.

**A file that is GONE by the read is no longer an index error.** A tool's own
temp file inside the project could be stat'ed and hashed by the walk, deleted
before the pass opened it, and land as
`<path>#None: cannot read: [Errno 2] No such file or directory` -- leaving
`error_count=1` on a clean index, an error row nothing could clear. A scanned
path that raises `FileNotFoundError` at the read now leaves the scan set: no
row, no `unreadable` skip and no error row. An older pass's row for it is
deleted with the file, so no later staleness read reports it as `deleted`
either, and `walked == indexed + uncommitted + skipped_total` still holds
because the walk's own count loses that entry with it. Permission and I/O
errors keep the `unreadable`/error path unchanged.

Same probe, after the vanished temp file: `error_count 0`, `last_errors []`,
`skipped {}`, `walked 2 == indexed 2`, verdict `fresh`.

## [Unreleased] — the shipped ordering was measured against ranking the unmerged hits and kept

**The merge-before-the-class-key order stays, and the reason is measured, not
assumed.** The repeat-coverage pass is an identity on a shipped read:
`Core.search` folds overlapping/adjacent same-file hits into one union hit
before the class key, so the surviving spans are already disjoint, and the
pass's +0.0204 MRR win belonged to the eval harness's `unmerged` arm, where the
merge is off. This round built the instrument that puts the two orderings on
the SAME retrieval and measured them on the answer a reader gets. `eval --arm
ranked` fixes the order on the unmerged hits (class key, then the
repeat-coverage pass) and runs `ranking.merge_for_display` afterwards, which
folds each same-file run into its union at the best-ranked member's slot and
never re-sorts; `--arm merge-timing` reports it beside the shipped `merged` arm
from ONE retrieval per query. The existing `merged`, `unmerged` and `both` arms
are unchanged.

Measured at index revision 3312 (`last_indexed` 2026-11-09T00:39:37Z), 42
labelled queries, one runner, one warm-up discarded, shipped answer surface,
counters showing 42 store searches and 42 embeddings per 42 queries for both
orderings:

| pipeline | ordering | recall@1 | recall@3 | recall@5 | recall@10 | MRR | nDCG@10 |
|---|---|---|---|---|---|---|---|
| dense | merged (shipped) | 0.5714 | 0.8095 | 0.8333 | 0.8810 | 0.6938 | 0.7401 |
| dense | ranked | 0.5714 | 0.8095 | 0.8333 | 0.8810 | 0.6938 | 0.7401 |
| hybrid | merged (shipped) | 0.6190 | 0.8333 | 0.8571 | 0.8571 | 0.7242 | 0.7583 |
| hybrid | ranked | 0.6190 | 0.8333 | 0.8571 | 0.8571 | 0.7242 | 0.7583 |

Every delta is 0.0000, 0 of 42 queries improved and 0 regressed on each
pipeline, and the top-10 class composition is identical (dense 333 source /
83 tests / 2 docs; hybrid 210 source / 159 tests / 42 docs). The stated bar
was: adopt the alternative only if it is strictly better on MRR AND nDCG@10
and its recall@10 is not lower, on BOTH pipelines. It is equal, not better, so
the shipped ordering is kept and the shipped path is unchanged. The structural
reason the two timings agree: the merge takes a repeated span's slot away
before either ordering reads it, and a display merge that gives each union its
best-ranked member's slot reproduces the shipped order exactly. Evidence:
`runs/ranking-comparison/` (local, untracked); the reader-facing record is the
Repeat coverage section of `skill/references/semantic.md`.

## [Unreleased] — a vectors-off upgrade reparses instead of re-stamping

**On the `--vectors off` arm, a manifest recording OLDER row axes now forces a
full reparse.** That arm builds no fingerprint, so the row-axis comparison the
vectors-on arm runs never happened there: a pass over a manifest recording
`extraction_version` 14 ran incrementally, and then `write_row_side` stamped the
current axes (`14 -> 15`) over rows the older extraction had produced -- every
row-reading surface saw versions that agreed while the rows underneath were not
this build's. The recorded `chunker_version`/`extraction_version` are now
compared directly (the inverse of the newer-direction check), and a LOWER
recorded value reparses every file. A blank or absent recorded version is not a
verdict -- the same rule as `newer_version_gaps` -- so a pre-versioning manifest
is re-stamped rather than reparsed, and the first vectors-on pass writes a
complete fingerprint for it. It stays a vectors-off pass: no chunk is
queued for embedding and no vector surface changes. The NEWER direction still
refuses the whole pass, unchanged. The row-side stamp now waits for the
reparse: if a file the reparse had to rebuild cannot be re-opened between the
scan and the parse, the recorded axes are left where they are for the next pass
instead of being certified current over it.

A manifest whose two axes disagree with this build in BOTH directions (one
older, one newer) takes the newer/refusal branch, never the reparse: the newer
check runs first, and refusal is the safe side. The cost of a reparse that
could not re-read a scanned file is stated too: the axes stay at the older
values, so every later pass reparses the whole project and repeats the warning
-- and the row reads keep refusing -- until that file becomes readable or
leaves the scan set (its row is then deleted). The remedy is to fix the read,
or let the scanner drop the file.

Measured on the guard fixture (2 files, 3 functions each) from the counters the
pass returns: older axes with the tree unchanged -- `files_indexed 2`,
`chunks_embedded 0`, `files_deleted 0`, zero embedder calls, and a symbol row
the older extraction's row set lacked (`handler_2` of `caf\u00e9_na\u00efve.py`) is
back; the same tree already at the current axes -- `files_indexed 0`,
`chunks_embedded 0`. At ef70e6c the older-axes case reported `files_indexed 0`.
The vectors-on arm's numbers are unchanged (its upgrade case still re-embeds,
`chunks_embedded > 0`).

## [Unreleased] — a pass holds one group of vectors, not the project

**A vectors-on pass embeds and writes in bounded groups.** The pass built every
embedding text for the project, embedded them in one call and only then started
its per-file upsert loop, so each chunk's vector was alive before the first
point was written: reindexing vivarium-sim (823 files, 32,629 chunks, 4096-dim
embeddings) was OOM-killed at 4.18 GB RSS under a 4 GiB scope while the box had
28 GB free. Chunks are now embedded and their files committed in whole-file
groups of at most `EMBED_GROUP` (default 512) chunks, a file larger than that
travelling alone. The per-file commit after that file's points reach the store
is unchanged, so a crash still costs at most one group's re-embed and the
manifest never claims a row whose vector did not reach the store; a poisoned
chunk is still recorded in `index_errors` and skipped. The embedded text is the
same `embed_text()` construction as before, byte for byte, so no vector changes.

Measured on a generated fixture of 80 files / 8,000 chunks at dimension 1024,
one runner: the largest embed call 8,000 -> 500 chunks (the HTTP request size
stays bounded by `EMBED_BATCH`, 48, inside it), the peak number of vector
objects alive at once 8,000 -> 500 (16x), and the process's RSS growth 74.6 MB
-> 11.6 MB. On that fixture the wall time went 1.049 -> 1.004 s, 4.29% faster
(`calc eval (1.004-1.049)/1.049*100` = -4.2898, the negative sign meaning
faster). At 16,000 chunks the base
peak doubles to 16,000 while the grouped pass stays at 500, so the bound is a
function of the group, not of the tree. A 512-chunk group at the fleet's 4096
dimensions is about 67.1 MB of vectors in one memory model -- one Python float
object plus a list slot per coordinate, 32 bytes (512 x 4096 x 32 B = 67.1 MB)
-- against about 4.28 GB for the whole 32,629-chunk project in the same model
(32,629 x 4096 x 32 B = 4.277 GB), the size the OOM measured. The 74.6 MB RSS
growth above was measured on vectors sharing one float object per dimension
(`[0.1] * dim`, 8 B per coordinate), a different and flatter model, so it is
not the one this paragraph's MB/GB figures use. Throughput on a SEPARATE
fixture -- 20 files / 2,000 chunks at dimension 1024, with 20 ms per request
and 0.5 ms per text injected to stand in for Ollama -- costs 3.7%: 0.730 ->
0.757 ms/chunk, median of three runs each. The embedded texts hash identically
before and after, so the grouping changed where the vectors live, never what
they are.

## [Unreleased] — a pass older than the recorded fingerprint refuses

**A pass whose code is older than the manifest's fingerprint no longer runs at
all.** The direction-aware guard refused the fingerprint re-stamp and the
direction-blind full rebuild, but let the pass continue: an older watcher still
ran a delete step derived from its own scan set (vivarium-sim lost all 12
multibyte-straddle files, 823 -> 811 rows) and could embed changed files with
its older extraction code. The guard now refuses the whole pass before any
delete or row/version write, raising the rebuild class. (`index-more` records
its scope in the manifest before the pass starts, so a refused `index-more` can
still add a scope entry; the pass itself writes nothing.) That refusal covers the
service-free pass too: the version ordering needs no embedder, so a
`--vectors off` pass -- which deletes from its own scan set and re-stamps the
row axes just the same -- is refused on the same terms. The class now survives
the pass-failure seam, so a caller sees the rebuild exit code (4) from
`add-project`, `index-more` and `reindex-project` rather than a generic
failure (1); the pass
surfaces still report the project's `state=error` carrying the message and the
remedy -- stop the watcher (`code-indexer unwatch`), then restart it on the
current code (`code-indexer watch --all --background`), or run
`code-indexer reindex-project <path>`. A watcher left running across an upgrade
reports that error on every tick instead of corrupting silently. The legitimate
direction is untouched: a manifest built by OLDER code still deletes what is
genuinely gone and re-stamps, on both arms -- on the vectors-on arm by
rebuilding and re-embedding, on the vectors-off arm by the reparse it runs (a
vectors-off pass cannot embed anything).

Measured: the dedicated guard tests reproduce the deletion at the base commit
(the multibyte-named file's row disappears while the recorded newer fingerprint
stands, and on the vectors-off arm a row is deleted and the recorded extraction
version regresses) and hold every row after the change, with the upgrade
direction in its own test per arm: re-embedding, deleting and re-stamping on the
vectors-on arm, and reparsing, deleting and re-stamping on the vectors-off arm.
A refused `reindex-project` exits 4 (`RebuildRequiredError`
on stderr), not 1.

## [Unreleased] — a lexical token's prefix family survives its position

**Every lexical token is prefix-matched, not only the trailing one.**
`build_match_query` put FTS5's `*` on the last token alone, so a token that
occurs nowhere narrowed the answer when it sat last: at revision 3312 a query
for `vector` prefix-matched 668 rows, `vector OR zzqxx*` matched 377, and
`zzqxx OR vector*` matched 668 again -- the same two words answered
differently in the other order, and the real word lost its expansion in the
first. Every token is now quoted and prefix-matched, so each contributes its
prefix family wherever it sits and an absent token contributes no postings.
That order-independence holds among the tokens the builder keeps: it keeps
only the last twelve, so a query longer than twelve still answers differently
depending on which tokens survive the cut. FTS5 syntax still cannot reach the
MATCH string: every token is quoted, so an operator word survives only as a
literal quoted token.

Measured on the forty-two labelled queries at revision 3312 (219 files, rows
current), one runner, one warm-up discarded, the merged arm: lexical MRR
0.7210 -> 0.7440, nDCG@10 0.7431 -> 0.7665 and recall@10 0.8095 -> 0.8333,
with four queries improving (one from a miss) and none regressing; hybrid
changes nothing it measures (MRR 0.7242, nDCG@10 0.7583, every correct-hit
rank identical) although 25 of its 42 answers differ in their rows and 26 in
their scores in the fused tail; dense
answers are untouched, 0 of 42. The cost is reported rather than tuned back:
the wider arm admits slightly more prose to the fusion window (the hybrid
window's docs share 15.50% -> 15.97% at the same revision, over 1271 slots),
and the class ladder still keeps a document out of the answer.

## [Unreleased] — a qualified C++ call, and the repository's own excludes

**A call through a qualified name writes a caller edge.** `geo::place()`
parses as a `call_expression` whose function is a `qualified_identifier`, and
the C++ query had call patterns for a plain identifier, a field access and a
template call but none for a qualified one. The call half of every namespace-
or class-qualified call was therefore invisible: the scope half was captured
and `find-callers place` answered nothing. The query now captures the callee
from the call's function field (plain and template form), so a qualified call
records a `call` row naming the callee. The pattern is anchored on the call's
function field, so a qualified TYPE use (`geo::Point p;`) keeps its `type` row
and never becomes a call; both directions are pinned by test.

**The scanner reads the repository-local exclude file.** `.git/info/exclude`
is part of what git ignores, and the scan read only `.gitignore` and
`.codeindexignore`: on a copy of a tree, twelve `.artifacts/*.log` files that
`git check-ignore` reports ignored were indexed, because only the local
exclude named them. The scan now finds the repository covering the project
root (`$GIT_COMMON_DIR/info/exclude` for a linked worktree, never a user-global
exclude file), anchors its patterns at the repository root even when only a
subdirectory is scanned, and reads it BEFORE every `.gitignore` so a tracked
negation still wins -- git's own precedence. Against real git, on a tree with
nothing untracked and not ignored, the scan set now reconciles: indexed files
== `git ls-files` minus `git check-ignore` hits. Two limits are deliberate: a
user-global exclude file is never read, and the exclude is resolved for the
repository covering the scanned root, not per nested repository.

**A linked worktree's `.git` pointer file is not indexed.** The directory
prune covers the `.git` DIRECTORY only, so a linked worktree (or submodule),
whose `.git` is a FILE holding `gitdir: <host path>`, had that file scanned
and its text indexed. The filename loop now skips it as ignored, so a host
path cannot reach the index through it. Future scans only: a pass does not
purge rows for a path that left the scan set, and the sweep keeps a row whose
file still exists on disk, so an already-stored one leaves on
`reindex-project` for that project.

**`EXTRACTION_VERSION` 14 -> 15** for both, in one bump because both change
what the index holds: rows built by 14 carry no qualified-call rows and a scan
set that includes files git ignores, so every registered project answers
`needs-reindex` and reads refuse (exit 4) until the fleet rebuild runs.
`CHUNKER_VERSION` is unchanged -- the chunker is untouched.

## [Unreleased] — a phrase matches across the chunks of one file

**A multi-word lexical query is evidence, not a requirement.** `--mode
lexical` built its FTS5 MATCH string as quoted tokens joined by the implicit
AND, so every word had to sit inside ONE chunk: "vector database" matched
nothing on this repository while "hashing" and "point_id" matched. The
query is now the OR of its quoted tokens (at that point the trailing token
alone was prefix-matched; every token is prefix-matched as of the entry above),
so a chunk carrying any of the words is a hit and bm25 ranks a chunk carrying
more of them higher. FTS5 syntax stays out of reach: every token is quoted,
so no caller-supplied operator reaches the query string.

Measured on the forty-two labeled queries at one index revision (3138), one
runner, one warm-up discarded, both arms from source: lexical MRR 0.3333 ->
0.7258 and recall@5 0.3333 -> 0.8333; hybrid recall@10 0.8810 -> 0.8571,
MRR 0.6959 -> 0.6919 and nDCG@10 0.7421 -> 0.7336, with one labeled query
(q-concept-01) falling from rank 6 to a miss because the widened lexical arm
fills the fused top ten. Reported as a measured trade, not tuned back.

## [Unreleased] — the graph says what it can see

**A repeated chunk span yields its slot to a distinct region.** The chunker
emits several vectors for one code region -- a declaration and an anonymous
line window over the same lines, a class and its methods -- so two slots of one
answer could show the same source twice while a different region never
appeared. After the class key the ranking moves a hit whose span overlaps one
an earlier hit of the same file already shows behind the distinct spans of its
own rung. The rule is rung-scoped, so it can never put a source duplicate below
a document; it is an order, never a filter, and a hit with no line range keeps
its place. On a shipped read the earlier merge has already collapsed one
file's overlapping and adjacent chunks into a single union hit before the
class key, so the rule is an identity there: it acts on the unmerged arm
(`--arm unmerged`, the default), which is the surface where its measured win
lives.

**A point repair needs no embedding service.** `reindex-project --repair-points`
re-addresses vectors that are already in the collection, so it has no reason to
ask the embedder anything -- yet it dialled the dimension probe, one request
with nothing to embed, and failed outright with the service down. The
dimension now comes from what the index already records: `meta.embed_dim` from
the last vector-writing pass, or, for a manifest that recorded none, the length
of a vector the collection already holds. A repair issues no request at all
and reports the same counts with the embedding host pointed at a dead endpoint.
When neither recorded source exists it refuses with exit 4 naming
`reindex-project`, rather than dialling the service to guess a dimension the
index never recorded.

**A rebuild refusal from the repair exits 4, not 1.** The two refusals
`--repair-points` can make -- the stored rows are not current, the vector
collection is gone -- were raised from inside the pass and folded into the
generic error state, so the CLI re-wrapped them as `InternalError` and exited
1 while the documented code for "the index must be rebuilt" is 4 (ADR-0003
section 5). `RebuildRequiredError` now keeps its class and reaches the caller
unchanged; both refusals exit 4 with empty stdout and one stderr line.

**A repair that writes no point advances no revision.** `index_revision` moves
once per committed pass, and the repair bumped it even when it found every
point already agreeing with its row -- so a command that changed nothing
looked like a change to every later audit. A repair that writes no point now
writes no manifest at all (no revision, no `last_indexed`). A row left
unrepairable is not that case: the vector state is recorded and the command
refuses with exit 4.

**A type reached through its member is a reference.** `NeedBand.Critical`
recorded only the member row, with `NeedBand` as its receiver, and never a row
targeting `NeedBand` — so `find-references NeedBand` reported a type that 27
lines of the tree name as invisible. The extractor now writes the receiver as
a `type_use` row; the index pass's resolver turns it into a `type`
reference when the name binds a definition of a type kind — the file's own
first, then the store's unique definition, then its file-class choice, the
precedence introduced for reference targets — and deletes it when the store
defines the name nowhere or defines it only as something that is not a type,
such as a namespace a qualified name scopes into or a function. A C#
local-binding walk keeps parameters, locals, foreach and catch names out
before the resolver is asked, so a member read through a variable never
becomes a type reference. The documented limit: a value bound under the exact
name of a type in the same file is indistinguishable from the type by name
alone and binds to it.

**`find-references` no longer claims a name absent when it is not.** The empty
answer now has three states: references found; no reference rows but the index
defines the name (new `empty_reason` value `defined-not-referenced`, sentence
naming the definition — the code has no references, it is not a hole in the
index); and no rows and no definition, where only an index that parsed all of
its files may say the name is absent. A partly parsed index says there is no
definition or reference row in THIS index, how many files it parsed, and that
the name may live in a file it never read. The coverage caveat rides the state
it describes.

**An `#include` is an importer, not a caller.** Import edges left the
`find-callers` walk: callers are `call`/`reference` rows, and files that
merely import the file defining the subject are printed in their own counted
section (`importers (N)` in text, `importers` / `importer_count` in JSON),
never in the caller list or its count. `find-references` applies the same
rule to `includes` rows: they are not references, so the unfiltered answer
lists the importing files separately and keeps them out of `total`;
`--relationship includes` still returns the raw directive rows.

**`deps` names its levels and never reads as a leaf.** `in L1` / `out L1`
became `imports of <file> (depth N):` / `importers of <file> (depth N):`, and
the JSON keys became `imports` / `importers`, each keyed by depth (documented
in `docs/json-schema.md`). Every include/import directive that resolved to no
file is counted and named under `unresolved_imports` (`imports of <file>:
none resolved; N include directives unresolved (...)` in text), so a header
made of twenty system includes no longer reads like a leaf. C/C++ system
headers are captured by the reference queries now, the C++ header
spellings (`.hpp`, `.hxx`, `.hh`, `.cc`, `.cxx`) map to cpp, so a header's
directives and references are extracted at all instead of being skipped as an
unknown language, and a C++ template call (`obj.method<T>(...)`,
`free<T>(...)`) is a call row -- the name sits one node deeper, so the plain
identifier pattern never saw it.

**A type named where a TYPE belongs is a reference.** Round 2 recorded a
member access's receiver; the other type positions stayed invisible -- a
parameter type, a field or property type, a return type, a generic argument,
an `is`/`as` pattern and a cast produced no row at all (9 lines and 10 tokens
on the vivarium tree). The C# query now captures those positions and the C++
query captures the qualifier of `Type::Member`, all written as the interim
`type_use` relationship the receiver rows already used. The resolver turns one
into a `type` reference when the name binds a definition OF A TYPE KIND (this
file first, then the store's unique definition), keeps it unattributed and
counted when the name is defined more than once and no file-class choice
breaks the tie, and deletes it when the store defines the name nowhere or
defines it as a namespace or a function -- so a name that resolves to
something which is not a type, an external library's type among them, never
becomes a type row.

*Corrected in this unreleased section:* an earlier draft of the paragraph
above promised that "a local variable, a property name or an external
library's type never becomes a type row", and the same claim was written
beside the capture set in `queries/csharp/references.scm`. That was not true
of the code then shipped: the value-binding walk read parameters, locals,
foreach and catch names only, so a property or a field spelled like a type
still produced a row, and the resolver converted any name that bound a
definition at all -- a project namespace included. The true rule is narrower:
a value binding is excluded only where the extractor can SEE the binding, and
the walk now also covers a property, a field and a generic type parameter. A
value bound in a form the walk does not cover is still indistinguishable from
the type by name alone, and that limit is now stated where the captures live.

`EXTRACTION_VERSION` moves 11 -> 12: the extraction axis moved (new reference
rows); rows built by 11 hold none of them.

**A header is read as the language it is written in, not the one its name
suggests.** `.h` mapped to C, so a C++ project that spells its headers `.h`
had every declaration in them invisible while the file row said `parsed` — no
`parse_unavailable`, no caveat, and `find-symbol` calling the class absent
from an index it had just declared complete. A `.h` header's grammar is now
decided PER FILE from the file itself, and the language that parse used is
what the file row and its symbol rows store — not the one its extension
suggested, and not a fixed map that reads every `.h` one way just to make the
other side work. tree-sitter's cpp grammar parses a C header too, and C is not
a strict subset of cpp (a K&R definition is legal C and an error in cpp), so
both trees are built for a `.h` file and the NAMED DECLARATIONS they produce
are compared, name plus kind: cpp wins when it names a declaration the C tree
does not, and a header both grammars read into the same declarations stays
`c`, which is what the C project that ships it expects. Both row axes moved
with this work: `CHUNKER_VERSION` 2 -> 3 (a `.h` file's chunks came from the
cpp grammar), then 3 -> 4 (the per-file decision, so a C-only header's chunks
come from the C grammar again and a K&R definition is not lost), then 4 -> 5
(below, where the decision stopped counting declarations and started comparing
them), and `EXTRACTION_VERSION` 10 -> 11 (a `.h` file's symbol, reference and
import rows exist at all).

**A header's grammar is chosen by what each tree DECLARES.** The per-file
decision counted declarations, and a count cannot tell a misreading from a
reading: C accepts `class Widget { public: void run(); };` without an error
and reads a FUNCTION named `Widget` from it, so the two counts tied 1-1 and
the tie went to `c` -- the file row said `c`, the symbol said `function`, and
`find-symbol Widget --language cpp` printed the absence sentence over an index
that called itself complete. The two trees' named declarations are compared
now, name plus kind: cpp wins when it names one the C tree does not, so the
class-only header is cpp with a `class` row, and a header that mixes a
namespace with a K&R definition reads as cpp instead of as a C tree that
parses cleanly and hides the class. A `struct Point` header names the same
declaration in both trees and stays `c`. `CHUNKER_VERSION` 4 -> 5 and
`EXTRACTION_VERSION` 13 -> 14: a `.h` header's stored language, symbol kinds
and chunks all moved.

**A namespace in a qualifier is a scope, not a type.** `geo::Point` resolves
`geo` to the namespace the project defines, and the resolver converted every
resolved `type_use` row to `type` -- so the index carried a type reference to
a namespace. The conversion is now guarded by the KIND of the definition it
binds (class, struct, enum, interface, record), so a namespace, a function or
a method that binds the name keeps no type row, while a real type reached
through a qualified name keeps its row. A generic type parameter is read off
the tree and shadows a type of the same name across the whole declaring type,
so `class Box<T>` beside a `class T` records no row for `T`; a property and a
field join the value-binding walk for the member-access rule. Still
unrecorded, and now stated as a limit: a C# type written with its namespace
(`Vivarium.Sim.NeedBand b`) produces no row at all, because only a bare
identifier is captured.

**A hit's language and a directory's dominant language read the stored row.**
The lexical-search hit labelled `lang` and the `--tree` projection's dominant
language both asked the extension map, so a `.h` header the index had read
with the C grammar was reported `cpp` in `search --json`, and a C project
whose headers are all `.h` was reported as dominant cpp. Both read the
language the file's row stores.

**One axis note for whoever splits this set.** The parse-verdict change
landed in `b3275ae` with no version bump of its own: the verdict stopped being
the chunk yield's and became the parser's, so a file whose AST declares
nothing is `parsed`. It is carried here because v13 and v14 rebuild
everything; cherry-picking `b3275ae` alone would leave every import-only file
stored `parse_unavailable` with nothing to trigger the rebuild.

**A file the parser read is `parsed`, even when its AST declares nothing.**
An import-only module parses cleanly and yields no chunk, and the verdict was
taken from the chunk yield: the file was stored `parse_unavailable` beside
`status=ok` and `chunk_count=1`. Coverage then said "1 of 2 files parsed" with
`parse_error 0` and `parse_error_files` empty, so no reader could learn which
file it was or why, and every absence claim in the project carried a caveat
about a reader that exists. The verdict is the parser's own again:
`parse_unavailable` means only that no AST exists at all (no grammar pack, no
grammar for the extension, or a parser exception). An empty AST still stores
one fallback chunk so the text stays searchable. Markdown and the other prose
formats are `parsed` for the same reason, so an empty answer on a prose-only
project names the reader it lacks (`language-without-ast`) instead of a parse
that ran.

`EXTRACTION_VERSION` moves 9 -> 10: the extraction axis moved (new reference
rows, new include rows, a wider scanned language map). An existing index
reports `needs-reindex` and its row reads refuse until the next pass; chunking,
embedded text and vectors are untouched.

## [Unreleased] — a reference credits the definition it resolves to

**Fan-in is now per DEFINITION, not per name.** The name half of
`Manifest.file_fan_in` was `SELECT name, MIN(file) ... GROUP BY name`, so every
reference to a name was credited to the lexicographically first file defining
it -- a test module that sorted before the source owning a name took that
name's whole count. Every index pass now attributes each reference through
`Manifest.resolve_ref_targets`, which writes `refs.target_symbol_id` and
`refs.resolution`: the referencing file's own definition first (`same_file`,
when that file defines the name exactly once); otherwise the store's only
definition of the name, or the one definition the project's file-class ladder
strictly prefers among several (`same_project` -- source above examples,
tests, documents, artifacts, the same ladder `overview` ranks hotspots by);
otherwise nothing (`none`). A tie is never broken by name or path: those rows
stay unattributed, are credited to no file, and are counted in
`overview`'s `hotspots.unattributed` (text: `unattributed: N references
credited to no file`) with the compact tag `hotspot-unattributed`. The
`--path-prefix` scope now filters target files and counts the unattributed
rows that sit inside the prefix; it no longer re-decides ownership.

This is index-time data: the columns existed (schema v7) but nothing wrote
them. `EXTRACTION_VERSION` moves 5 -> 6, so an existing index reports
`needs-reindex` and its row reads refuse until `reindex-project` runs; what is
stored until then is the old per-name aggregate and no resolved targets, and
one pass replaces it. Chunking, the embedded text and the vectors are
untouched, so a vectors-off pass is enough to move the numbers, and a
vectors-on index keeps its cached vectors.

Measured on a fixture where `a_module_test.py` defines `refresh_token` before
`core/refresh.py` does: the old aggregate put all three references on the test
module, the resolved one puts them on `core/refresh.py` and leaves the test
module out of the fan-in list entirely.

The version gate needed one repair to make that refusal real. A vectors-off
pass records only the row keys, so no COMPLETE fingerprint exists and the
full-key comparison never ran: such an index answered silently from rows that
carry no resolved targets (measured: an empty fan-in, exit 0).
`fingerprint_status` now compares the recorded row keys on their own
(`fingerprint.recorded_row_version_gaps`), so the same index refuses with
`extraction_version: 5 -> 6` and exit 4 instead of answering a zero. A
manifest that recorded no row key at all keeps its old reading -- the next
pass backfills it.

## [Unreleased] — an anchored nested pattern anchors where it lives

**A leading `/` in a nested ignore file now anchors to the directory carrying
that file, as in git.** The scanner handed every ignore spec the same
root-relative path, so `src/.gitignore`'s `/secret.txt` matched nothing:
`src/secret.txt` stayed in the scan set while `git check-ignore` called it
ignored. Measured on a probe tree (`/top.txt` at the root, `/secret.txt` in
`src/`): the scan before the change listed 5 paths including `src/secret.txt`;
after it lists 4, equal to git's unignored set, and `src/nested/secret.txt` is
still kept because the anchored pattern does not reach beyond `src/`. This is
the only behaviour change in this batch, and it can only SHRINK a scan set. On
an index built by the old scanner the read reports the dropped file as
`deleted` staleness (`freshness: stale`, 5 files, one `deleted` reason for
`src/secret.txt`); the next pass drops the row (`files_indexed: 4`,
`files_deleted: 1`), after which the read is `fresh` with no stale reasons.
The change is scan-time, so no reindex is required to correct the scanner and
no manifest schema or extraction version moved: one ordinary pass settles the
diff.

**`index-status` names the error total.** When chunk embed errors are recorded,
the line carries `error_count=N` beside the three-example `last_errors=`, and
`--json` carries the same number; neither field appears with no error rows.

## [Unreleased] — a deep-limit answer is the same answer twice

**`semantic-search` returns byte-identical output for the same read against the
same index, at any `--limit`, including the first call after the embedding
service reloads a model — for a read whose query embedding is served from the
embedding cache, which is the default.** The guarantee rests on that cache: with
`EMBED_CACHE=0` and the model reloaded, the vector is recomputed and the cold and
warm reads differ. Two layers were unstable, and neither was the store:
Qdrant returned the same ordered point list on every call and in every process
measured, and the SQL reads underneath are ordered. First, the query embedding
was recomputed whenever the dimension was unknown — which is every read — and
the service's cold-loaded run returns a materially different vector than its
warm one for the same text (components up to 6e-3 apart on the measured model),
so a warm-up call moved scores by ~1e-3 and re-ordered near-ties at depth. The
embedding cache is now canonical for the read path: the dimension is resolved
first, and a miss stores the vector and searches the stored representation, so
the first read and every later one use the same bytes. Second, every score sort
fell back to the order the candidates arrived in, and the same-file merge walked
a group in score order, so a low-scoring chunk seen after a high-scoring one
produced a "union" spanning lines no chunk covers (5-110 from hits at 1-10,
100-110 and 5-50). Ordering now ends in a total identity key — `project`,
`file`, `start_line`, `end_line`, `symbol` — and the merge is the line-ordered
union of the ranges, so the member set is a function of the chunks the store
returned. A deep-limit answer therefore reports the true number of disjoint
ranges instead of the over-merged set — on this repository's current snapshot
(192 files, 3130 chunk rows, a 2926-row pool at `--limit 3000`) 915 ranges
against 425 before. The default depth moves too: an answer can change there
because a merge no longer spans a gap between chunks, so a range that used to
run from the first hit to the last now reports only the lines its chunks
actually cover (in 4 of 4 sampled queries at `--limit 8` the member set
differed, up to 7 of 7 rows). The candidate pool is unchanged at every depth;
what changes is which ranges the answer reports, and the reported ranges are
more precise.
Query-time only: no stored key changed, so no reindex is involved.

## [Unreleased] — four surface questions answered, one scan set corrected,
the help listing grouped, and three fixes recorded

**`code-indexer --help` groups the 33 registered commands into two panels; the
command surface itself is unchanged.** `Core` holds the six an agent needs
first — `overview`, `skeleton`, `find-symbol`, `get-code-context`,
`semantic-search`, `index-status` — and `Advanced` holds every other
registered command, in registration order, which already clusters them by
task family: diagnostic and change reads, graph reads, registry, index
maintenance, freshness, file structure, the plan family, then measurement.
No command was added, removed, renamed or re-flagged, no default, output or
exit code changed, and every command's own `--help` is byte-identical to the
previous release's; only the top-level listing renders differently, and the
entry document now says which panel to start in.

**Nested ignore files compose the way git composes them.** A file was skipped
if ANY per-directory ignore file matched it, which made a nested exclusion
absolute: a deeper `!pattern` could not re-include what a shallower file
excluded, so `git check-ignore` and the scan disagreed about the same tree, and
the scanner's docstring claimed the agreement it did not have. The scan now
reads the specs from the root outwards and lets the last matching rule decide.
A scan set can only grow under that rule, so nothing leaves a manifest: files a
project's nested negations had been losing arrive as `added` staleness reasons
on the first read after the upgrade and gain rows on the next pass. The test
that pinned the old composition is rewritten to the contract it holds now, and
a second one asks `git check-ignore` about the same tree so the two readers
cannot drift apart again.

**Four further rulings are recorded in prose, and behaviour stays where it is.**
Fan-in is a declared aggregate per name, credited to the name's first defining
file: a reference row records the name it observed, never which definition it
meant, and the references now say what a reader may conclude rather than
calling the number an artefact. `--symbol` keeps the bare declaration name on
every name read, with the dotted `qualified_name` named as a field a read
reports and never one it takes back -- and the refusal the reference quotes is
the one the tool prints. A path prefix narrows the files an answer reports, not
the coverage scope, so a prefix answer's counts stay comparable with
`index-status`; a test now pins that. The staleness banner one architectural
record quoted is corrected to the shipped one-line form, under the owner's
ruling, keeping the sentence that says what a stale answer means. The reasoning,
the landed edits and the two follow-ups that want their own card are in
`docs/decisions/index-surface-rulings.md`.

**A search hit with no symbol and an empty snippet now names its own file.**
`identity` used to be the bare word `file` whenever a hit carried neither a
symbol nor a snippet line, so two such hits in different files were
indistinguishable in the compact, JSON and text forms alike. The three forms
now name the hit's file (`pkg/one.py`) and agree with each other; only a hit
that carries no path at all -- a guard a real answer does not reach, because
every stored hit has one -- still falls back to the literal `file`. No key,
shape or default changed.

**A path the staleness check cannot open is its own reported state.** A rowless
path the check could not open (permission, a broken symlink target, an I/O
error between the scan and the diff) was folded into the `added` reasons,
which left a stale banner no reindex could clear. It is now carried beside the
verdict as `unreadable_count` / `unreadable_files` / `unreadable_summary` (the
list capped at five, the count the total), never as a stale reason and never
as withheld; the coverage block gains `files_unreadable`, and `complete` is
false while the count is non-zero. A fresh answer with one such path prints
`1 file could not be read (permission or I/O error), so part of the tree was
not checked` instead of staying silent. The state reports what the diff saw:
the scanner drops a path it cannot open, so a path the scan never returned is
not counted.

**`doctor`'s reachability probes are bounded, and its checks stream.** Each
Ollama and Qdrant reachability probe is bounded by `doctor_probe_timeout`
(default 5 s, from `DOCTOR_PROBE_TIMEOUT` or the new `--probe-timeout` flag
when set) instead of waiting on a blackholed host; a dial that reaches the
bound fails with `timed out after <n>s`. The one-line-per-check output is
flushed as each section returns, so the local checks appear while the probes
are still dialling rather than after them. A host that answers is unaffected.

## [Unreleased] — one verdict source for the fingerprint sentence

**The `index-status` sentence now reads the same vector verdict its line
prints.** The sentence took its vector half from `fingerprint_status`'s
`vectors`, which is the identity comparison alone, while the line printed
`core.vectors_status`, which also knows the vectors mode and the recorded
marker. On a project kept `vectors=off` and read under a different
`EMBED_MODEL`, the line said `vectors=off vectors_state=current` and the
sentence said the vector side was stale and prescribed a rebuild — at exit 0,
beside a verdict that denied it. `core.vectors_status` now carries the
identity comparison (`identity_reason`) inside the same object as the mode and
the marker, and `fingerprint.meaning` is handed that object and nothing else,
so the sentence and the line cannot disagree.

With vectors off the sentence now says there is no vector side at all (no
vectors are stored and no vector query runs), states a recorded identity that
differs from this build's as an inert fact — it changes nothing until a
vectors-on pass, which writes its own collection — and prescribes no rebuild.
A recorded `stale` marker with a matching identity gets its own clause instead
of leaving the reader to reconcile it with a sentence about matched keys. The
verdict values, the field names and the JSON shape are unchanged; only the
`fingerprint_meaning` sentence moved, and only for states it used to
misdescribe.

Two shipped document cells are corrected with it: the sweep-orphans stderr
cell now names the shared `Warning: index is stale: ...` line a successful
sweep writes, and the read contract's graph-notes bullet says a complete
**empty** answer prints its `note:` lines on stdout (with `truncated: false`),
as the coverage bullet already described.

Three more contract sentences were false and are corrected: the read-stderr
enumeration counts SIX cases, the sixth being the package's logging handler --
an unprefixed log note, which `get-code-context` fires on a file edited after
its last index -- and names the `errors.fail()` channel beside it; the
`fingerprint_meaning` shape list is keyed on the values the sentence branches
on (mode, marker, identity) and not on `vectors_state`; and the coverage table
says `complete` is vacuously true on an index that holds no files, so a reader
branches on `files: 0`, not on `complete`.

## [Unreleased] -- an empty answer names its cause, and coverage travels with it

**An empty read answer says why it is empty.** `find-symbol` returning
`no symbols matching 'X'` at exit 0 used to be indistinguishable from the
files never having been parsed, the language having no AST reader, or the
project not being indexed at all. Both forms now state the cause, in one
vocabulary shared by `find-symbol`, `find-definition`, `find-references`,
`find-callers`, `find-callees` and `semantic-search`: `empty_reason` is
`name-absent` (the index parsed the files that could hold the name),
`files-not-parsed` (the sentence names the count and the languages),
`language-without-ast` (the sentence names the language) or `index-empty`,
in that fixed precedence, with `empty_reason_sentence` carrying the sentence
the text form prints after its lead. The VALUES are shared by every surface;
the sentence is worded for the one that answers, so `semantic-search` says
"nothing in an index that parsed N of M files matched the query" rather than
speaking of a name. A `semantic-search --all-projects` fan-out is the one
exception: it has no single index to describe, so a no-hit fan-out prints the
bare `no results` with `coverage.scope: none` and no `empty_reason`. Exit
codes do not change: an empty answer is not an error.

**Coverage travels with every read answer.** Every read command's JSON now
carries one `coverage` object: files in scope, files parsed, files not parsed,
files withheld for privacy, the languages in scope with no AST symbol reader,
the freshness verdict and whether that coverage is complete. It is scoped to
the files the answer actually read where that is knowable (`outline`,
`get-code-context`, `find-symbol --file` say `scope: "answer"`), project-wide
otherwise, and `scope: "none"` for a command that reads no index (`doctor`,
`list-projects`, the eval reports, the two plan reads). The text form prints
one short line ONLY when coverage is incomplete -- on stderr for a non-empty
answer (so a cap such as `overview --max-lines` counts stdout alone) and on
stdout for an empty one, beside its cause -- so a clean answer still says
nothing.

**The fingerprint verdict says what it means, and the sentence follows the
line.** `fingerprint=unverified reason=embedding-dimension-not-compared` used
to be a bare pair of flags. `index-status` now also prints the meaning, in one
stderr line beside its one-line answer and as `fingerprint_meaning` in JSON.
The sentence is a function of the three values the line prints beside it --
the vectors mode, the recorded marker and the identity comparison -- in that
order, and never of `vectors_state` alone: on a project kept `vectors=off`
under a different model the marker reads `current` and the sentence says the
recorded identity differs. With vectors on, a differing identity says a key
did not match and names it (`embed_model: a -> b` or `embed_text_version: a ->
b`) and says `reindex-project` rebuilds it; matched keys with a recorded
`stale` marker say the MANIFEST's record is what is stale; `needs-reindex`
names its differing row keys the same way; with vectors off there is no
vector side at all, so no rebuild is prescribed; and an index that records no
fingerprint gets "the fingerprint comparison did not run" rather than
silence, which reads as a pass. `stale` is a marker no pass in this build
writes -- it is accepted and explained, not produced -- and an `error` marker
is the line's own verdict, which the sentence does not speak to. The branch
ids and the field values that reach them are the table in
`docs/json-schema.md`. The field names are unchanged.

Purely additive for JSON consumers: a caller that ignores `coverage`,
`empty_reason` and `fingerprint_meaning` sees the bytes it saw before. No
manifest schema change, no `EXTRACTION_VERSION` change, no reindex.

## [Unreleased] — one flag, one argument shape across the reads

**The read commands answer to one format flag, and the three symbol reads take
the symbol one way.** `--version` at the top level prints the installed
package version on one line and exits 0, with no command and no project.
`--format text|json` is accepted by every read command that took `--json`,
and `--json` remains an EXACT alias of `--format json`: both spellings render
the same bytes. Passing both is accepted when they agree -- `--json
--format json` is the same answer asked twice and prints the same bytes as
either alone -- and is a usage error (`ArgumentError`, exit 2) naming the
contradiction when they name different formats, rather than a silent
precedence. The one pre-existing combination this changes is `semantic-search
--json --format text`, which used to let `--json` win and now refuses;
`semantic-search --json --format json` keeps its answer. `--format compact` is
a one-line-per-record rendering built only from fields the text form already
prints, with compact's own normalized field names (`loc`, `directory`,
`share`, `hotspot-fan-in`), on `overview`, `index-status`, `skeleton`, `outline`, `find-symbol`,
`find-definition` and `semantic-search`; on every other read it is refused
with exit 2 and a message naming `text|json`. `index-status` prints one line
already, so its compact form is that line. `get-code-context`, `find-symbol`
and `find-definition` all take `--symbol <name>` beside `--project`;
`get-code-context --symbol <name>` with no FILE positional resolves the name
project-wide through the manifest and refuses an ambiguous name with every
candidate listed (`ArgumentError`, exit 2), while the FILE positional keeps
its meaning, and `find-symbol`/`find-definition` keep the positional NAME as
the alias of `--symbol`. No manifest schema change, no `EXTRACTION_VERSION`
change, no reindex, and every invocation that worked keeps its answer except the
one combination named above.

**Round captures leave the publishing surface.** `runs/` is no longer tracked:
the captures, gate output and command logs stay in the working checkout, and
`.gitignore` keeps them out of `git status`. Documents that cite a `runs/...`
path now say in one line that the artifact is local rather than linkable. The
capture sanitiser moved to `scripts/publish_sanitize_capture.py`, because a
test depended on it and it is a tool, not evidence. `eval/queries.jsonl` stays
tracked: it is the corpus the evaluation harness reads.

**The publishing check now governs captures for the profile-path shape.** A
quoted traceback carries the directory it ran under, so a capture under `runs/`
can disclose the household profile path while every authored document is
forbidden to. The lint reports `.hermes/profiles/<name>` in every tracked file,
captures included; the name and card-id rules keep their authored-surface
scope. The failure reference's exit-2 row also stopped listing the agreeing
`--json`/`--format json` pair as a cause, matching the rule the rest of the
surface states.

## [Unreleased] — the banner states its count

**The staleness banner is a stateless one-line count.** Every read of a stale
project printed the same paragraph -- `index is stale (1 file changed since
<timestamp>: 1 added); answers describe the index, not the tree. Refresh: ...`
-- so a reader who saw it a few times stopped treating it as a signal. The
line is now a count that is identical on every read while the tree stays
stale and short enough to stay visible: `Warning: index is stale: 1 added
file -- <path>; answers describe the index, not the tree; refresh with
code-indexer reindex-project <path>`. The kinds, their counts and the
guidance sentence are the whole text answer; the per-file reasons,
`index_revision` and `last_indexed` stay in the `--json` block on every call,
and `index-status` prints them on request. Nothing is stored, remembered or
suppressed: a marker file, a manifest-mtime heuristic or a silent first
occurrence would all make a later process believe it had already been told
something it never saw, so none of them exists and there is no session token
-- every read states the verdict it just computed. A withheld file is still a
skip rather than a stale reason, and on a project that is both stale and
holds skipped files the same line carries both facts distinctly (`1 added
file; 1 file skipped (sensitive content)`), while `index-status` states the
skip in its own line and its warning omits it, so no fact prints twice. Exit
codes are unchanged: a stale read is still exit 0 with one stderr line, a
schema-version refusal is still exit 4, and a failed command still prints no
answer on stdout. `JSON` keys are unchanged -- no schema or
`EXTRACTION_VERSION` change, no reindex.

## [Unreleased] — the reference split

**The query reference is split at its concern boundary, and the budget did
not move.** `skill/references/query.md` had reached its 400-line bound, so the
semantic surface moved out rather than the budget growing: `semantic-search`,
`eval`, `eval-compare` and the ordering policy every ranked answer shares now
live in a sixth reference, `skill/references/semantic.md`; `query.md` keeps
`overview`, `skeleton`, `outline`, `find-*`, `get-code-context`, `deps`,
`changed-symbols`, `map` and `file-outline`, and points at the ordering policy
with one line instead of restating it. The entry document names the sixth
reference with its trigger, and its roster gained a `Reference` column so a
command and the file that carries its flags sit on the same row.

Four worked examples the old budget had cost are restored, each in the file
that now owns its command and each run against the tree: `outline --docstrings`
and `changed-symbols --base/--head --impact` in `query.md`, `eval` /
`eval-compare` in `semantic.md`, and `get-code-context --symbol` there in its
honest spelling -- `--symbol` matches the bare declaration name the file's
rows carry, so the example is `--symbol search`, and the reference says why the
dotted `--symbol Core.search` is refused (exit 1, `InternalError`) rather than
pretending it works. `tests/test_docs.py` reads the roster's new shape; the
index closure, budget, taxonomy and generated-block checks are unchanged and
now cover six references.

## [Unreleased] — the indexed tree

**A withheld file is a skip, not a stale reason.** The index pass drops a
sensitive file on purpose and writes no row for it, so the tree-vs-manifest
diff saw a scanned path with no row and reported it as `added` on every read
-- `index is stale (1 file changed ... 1 added)`, exit 0, forever. The diff
now calls the same decision function the pass calls on the paths it cannot
explain, and a withheld file is its own state: `index-status` prints
`1 file skipped (sensitive content)` and `--json` carries `withheld_count`,
`withheld_files` (path + the layer that fired) and `withheld_summary`, on a
`fresh` verdict, so the banner is quiet and the skip is still visible and
counted. The names are `withheld_*`, not `skipped_*`: `semantic-search --json`
already used `skipped`/`skipped_count` for the projects that could not answer,
and one key never means two things. The summary carries a per-reason numeral
only when both layers fired. A genuinely new non-sensitive file still reports
`added`; a withheld file whose content changes is still withheld, because the
decision is re-run rather than remembered; and no path, row, snippet or field
names the material. On a project registered with `--allow-sensitive` the diff
classifies nothing, so a rowless secret is `added` -- the row the next pass
writes -- instead of a false `fresh` beside a skip. The re-check reads only
scanned paths with no row, at the same 256 KiB prefix, inside an 8 MiB
per-check budget charged in bytes actually read: one huge new file can no
longer spend the whole allowance, and a path the budget did not reach is
counted in `withheld_unexamined_count` beside the `added` reason it keeps.
Nothing was migrated: no manifest schema change, no `EXTRACTION_VERSION` bump,
an existing manifest answers correctly without a reindex.

**`semantic-search` ranks by source kind too.** The last relevance surface
without the ladder now uses it: source before examples before tests before
docs before generated/artifact, decided by the same
`src/code_indexer/ordering.py` classifier every structural answer uses. The
class key runs in every ranking mode (`vector`, `metadata`, `hybrid`) and
every retrieval mode (`dense`, `hybrid`, `lexical`), after the scoring pass
and before the generic output caps, so a near-duplicate test can no longer
win a question about the implementation -- nor a fixture, where it sits under
a tests or spec tree. Two cases the order does NOT close, and where each was
closed instead: a captured transcript is excluded by `.codeindexignore`, and
a captured `.err` or a fixture in a top-level `fixtures/` tree is still
source to the classifier and still wins. It is an order, not a filter: every
hit in the window is still returned, the demoted ones at the tail.

- **The bare dash is gone.** A hit whose chunk carries no symbol rendered as
  `-` in compact output (and as nothing in text), so the top answer often
  said where but not what. Text and compact now print the first non-blank
  code line of the snippet when there is no enclosing symbol, `file` when
  the snippet is empty, and `--json` carries the same string as
  `hit.identity`; no output path can produce `-` or an empty name.
- **One classifier, no second path rule.** The module's own test/vendor path
  marker is removed rather than kept in step with `ordering.file_class`: it
  called `fixtures/`, `vendor/` and `testdata/` non-source while the shared
  classifier calls them source, so one hit could be tests to search and
  source to `find-definition`. The metadata ranking mode now adjusts for
  symbol presence only; paths are judged in one place.
- **Two waivers, written down in both claims.** A test-targeting query
  (`test`, `tests`, `testing`, `spec`, `specs`, `fixture`, `fixtures`,
  `unittest`, `unittests`) sorts the tests rung with source; a data-format
  query (`json`, `yaml`, `yml`, `toml`) waives the pure-data down-weight and
  share cap. The `ranking.py` docstring and `skill/references/query.md`
  (Ordering) state the same key lists per mode and the same two waivers.
- **Nothing was reindexed.** Ordering and identity are decided at query time
  from stored rows: no schema change, no `EXTRACTION_VERSION` bump.

**A structural answer is ordered, not merely counted.** `find-definition`,
`find-symbol`, `find-references`, `find-callers` and the two `overview`
hotspot lists now share one ordering policy: source before samples before
tests before docs before generated/artifact files, then identity quality (a
hit that can name what it matched before one that merely shares a name), then
a deterministic tie-break over the row's identity fields. It is an ordering,
not a filter: every hit is still returned, the demoted ones after the
promoted ones, and each surface keeps its existing cap.

- **Why.** A test fixture and an audit document accumulate identifier
  mentions because they exercise or quote the code beneath them, so a list
  ordered by fan-in or by path alone put `tests/conftest.py` (1,460) above the
  server module (1,006) in an orientation answer, and left fifteen
  `confidence=exact` `Tick` definitions interleaving test files with
  production. The class is a property of the path, so the order no longer
  moves with the index's incidental hit distribution.
- **One classifier, both layers.** `src/code_indexer/ordering.py` owns the
  path classes; a capped SQL read calls it as the `file_class_weight`
  function, so the store and the graph layer cannot disagree about what a
  test file is. That function is registered closed over the project's own
  ladder, so the two layers also agree about a name the project adds.
- **The document every repository has.** Extensionless documents
  (`LICENSE`, `LICENCE`, `COPYING`, `NOTICE`, `AUTHORS`, `README`,
  `CHANGELOG`, `CONTRIBUTING`) are documents, not source. Before this, a
  licence file's prose held identifier mentions that the classifier read as
  source, so the orientation answer was true about fixtures and quiet about
  the one document every repository ships: a fixture or an audit document
  does not win orientation, and neither does the licence.
- **Shipped samples have a rung.** `examples/`, `example/`, `sample/`,
  `samples/`, `demo/` and `demos/` sort after source and before tests: code
  written to be read demonstrates the API, but it is not evidence about this
  tree, and on the path tie-break it used to rank above the shipped entry
  point. A project extends any rung with a `[tool.code-indexer.ordering]`
  table in its `pyproject.toml`; the additions are additive, so a rung member
  cannot be declared away.
- **Each surface states its own keys.** The symbol read orders class, recorded
  kind, attribution, then name and the identity fields; the reference read
  class, whether the reference sits in a named enclosing symbol, attribution,
  then its identity fields; `overview` class, then the count. Two clauses
  that could not vary were removed rather than documented as if they ranked
  rows: the reference read's "target has a definition" check was a constant
  under `to_name = ?`, and the symbol read's exact-name clause duplicated a
  distinction the query split already enforces.
- **The two graph walks say what they do.** `deps` orders each level by class
  before its node cap fills, so a test or sample importer cannot take a
  budget slot from a production one; `find-callees` keeps call-site order,
  which the reference now names as an exemption with its reason.
- **Case, in one place.** Names are compared case-insensitively (ASCII, SQLite
  `NOCASE`) by the symbol read only; no other surface compares names, so a
  non-ASCII identifier compares exactly everywhere.
- **Nothing was reindexed.** Ordering is decided at query time from stored
  rows: no schema change, no `EXTRACTION_VERSION` bump, no change to what is
  stored.

**Captures are no longer part of this repository's index.** A root
`.codeindexignore` now names the two non-source trees the scanner skips:
`runs/` (round records, gate output and captured command logs) and `eval/`
(the retrieval corpus of queries and their labelled answers). Both stay
tracked in git; the exclusion is index-only, so `git log` still carries every
committed record.

- **The measured cost of indexing them.** In the live manifest, `runs/` held
  939 of 1,111 files, 1,614 of 4,312 chunks and 550 of 2,935 symbols, and
  those rows tied with or outranked source in semantic search, `find-callers`
  and hotspot rank. `eval/` went the same way for a different reason: its
  lines are questions shaped like the ones users type, so an indexed corpus
  matches a query before the code that answers it does.
- **Existing rows need a reindex.** The file governs future scans; rows
  already in a manifest are dropped by a reindex of the project. This entry
  ships the exclusion, not the pass that applies it to a live index.
- **`.gitignore` had lost `logs/`.** A commit appended `runs/` to a `logs/`
  entry that had no trailing newline, leaving the single line `logs/runs/`, so
  neither tree was ignored while the commit message claimed otherwise. `logs/`
  is back on its own line. `runs/` is deliberately not ignored: round records
  are committed on purpose, and ignoring the tree would hide new captures from
  `git status` and force `git add -f` for the records that stay.
- **`tests/test_repo_exclusions.py` pins the set.** It copies the
  repository's own `.codeindexignore` into a throwaway tree shaped like this
  one and asserts the captures and the corpus stay out while `src/` is
  scanned; without the file the test fails. The same tree also carries
  `src/runs/`, `src/eval/` and `pkg/runs/`, which the anchored patterns keep.
- **The patterns are anchored to the root.** `runs/` and `eval/` matched a
  directory of either name at any depth, so a module under `src/` would have
  vanished from the index while the prose said root trees. They are now
  `/runs/` and `/eval/`. A second case records the composition rule: the
  per-directory specs are `any()`-unioned, so a root `!keep.txt` cannot
  re-include a `keep.txt` that `src/.codeindexignore` excluded.

## [Unreleased] — the retired read-path TTL

**`STALE_TTL` parses nothing now.** The config field it fed was inert -- reads
report their verdict from the stat scan, so no code consumed the value. The
field, its default entry and the integer parse are gone, and every test
fixture that built a `Config` with it is cleaned up.

- **The variable is still accepted and ignored.** A deployment that exports
  `STALE_TTL` starts and behaves exactly as before; the retirement is a
  removal from the config surface, not a new failure for an inherited
  environment. A value that is not a number no longer raises the way the old
  parse did, and `tests/test_retired_config_vars.py` pins both facts.
- **The documented row stays.** The indexing reference keeps `STALE_TTL` and
  now says it is accepted and ignored; `tests/test_docs.py` checks the retired
  names against that table instead of listing them as live reads.

## [Unreleased] — the agent surface

**The documentation an agent loads before it acts is now 118 lines, and the
detail is read on demand.** `skill/SKILL.md` was one 1090-line page (39,797
bytes) carrying every flag, example and remedy; it is now the entry document
of ADR-0009 -- decision tree, command roster, exit table, staleness rule, the
read-only guarantee, the reference index -- and the detail moved into five
references under `skill/references/`, each with its own budget.

- **The entry document carries the choice, not the detail.** One row per
  question shape, one roster line per command with its read-or-write class,
  the six-code exit table with the `ErrorType` rule, the staleness rule in
  three sentences, and one trigger per reference. Per-command flags,
  examples, the plan workflow, provider behaviour and the MCP parity table
  left it; the parity table lives in `README.md` alone (ADR-0007 section 5).
- **The roster and the command sections are generated from the CLI registry.**
  `scripts/generate_skill_docs.py` renders them from the same typer registry
  the CLI builds its commands from, so a command cannot exist undocumented
  and a flag cannot be documented that does not exist; the prose around a
  generated block is written by hand.
- **`tests/test_docs.py` guards the split.** It replaces the fenced
  command-block parser with six checks: roster equality with the registry
  including classes, index closure (every reference named, every reference
  file named, every command covered by exactly one reference), both budgets
  with the measured size printed, taxonomy coverage of every exit code, the
  generated blocks against the generator, and the installed copy's digest --
  which skips with its reason when the installed copy is not in the new
  shape.
- **Nothing about the CLI changed**: no command added or removed, no flag
  moved, no exit code changed. The entry document's roster is the same 33
  commands, each still in the class ADR-0003 section 1 gives it.

## [Unreleased] — the refactoring provider contract

**A refactoring provider interface exists, and it was proved before any
language server was installed.** Five operations — `capabilities`,
`resolve_symbol`, `find_references`, `prepare_rename` and `materialize` —
sit behind one module; a stub in the test tree answers from fixture files and
`tests/test_provider_contract.py` is the registration test, so the
abstraction is shaped by the contract instead of by one server's quirks. No
language server is installed, spawned or probed.

- **Availability is a property of `(provider, operation)`, and it is
  measured.** `supported`, `unsupported` and `unknown` are three values:
  `unknown` is never reported as a denial, so a provider that did not answer
  a capability question is asked anyway. An operation the provider reports
  `unsupported` is `UnsupportedOperation` (exit 2) with no edit produced,
  never answered by a weaker local approximation. No provider installed is
  `ProviderUnavailable` (exit 3) with one line naming the install route; a
  provider that never becomes ready or times out is also exit 3, and a
  timeout is a cancelled request, never an empty semantic answer.
- **Lifecycle.** Lazy start: constructing a session spawns nothing, so every
  syntax-only command is unaffected by a provider being absent, slow or
  broken. One provider per project per invocation, reused, terminated at
  close — no daemon. Every semantic answer carries
  `provider: {name, version, transport}`, and the index stores no provider
  identifier.
- **Positions convert once.** `provider_positions` holds the only two
  conversion functions between the provider's 0-based lines and UTF-16 code
  units and the plan's 1-based lines and bytes, and applies the one boundary
  test in both directions. A position inside a surrogate pair is
  `PositionConversionFailed`, never rounded. Proved against an
  astral-plane line, a CRLF file, a byte-order mark, an empty last line and a
  surrogate-pair offset.
- **Provider edits become plan edits.** Each edit expands to the whole lines
  it touches, bytes outside the range preserved exactly; two edits on one
  line merge in descending byte order; the region hash covers the whole old
  line; a cross-line edit is `NonLineAlignedProviderEdit` rather than an
  approximation. The plan format still reserves `rename_symbol` and now
  refuses it as `UnsupportedOperation` (exit 2) with no text edit.
- **The four pass commands answer as data.** `add-project`, `index-more`,
  `remove-project` and `reindex-project` gain `--json`: schema 1, the fields
  the text renderer prints and `ErrorType` on failure, with no staleness
  block. Documented in `docs/json-schema.md`.

## [Unreleased] — structural query on a stable identity

**A change can now be prepared as a content-addressed plan, validated and
previewed without writing.** Six plan commands appear: three writers
(`plan-replace-body`, `plan-insert-symbol`, `plan-delete-declaration`),
`plan-validate`, `plan-show`, and `plan-apply`, which applies a plan through
filekeeper, reporting every file and handing back the ordered operations to
undo. A rename is not a command: the format names the operation and refuses
it, because references are textual and nothing would verify the rename.

- **A plan is its file.** The document's canonical bytes (UTF-8, sorted keys,
  no insignificant whitespace, no trailing newline) are hashed to
  `plan_id: sha256:<hex>` with the member absent, then written in; the file is
  `<project>/.code-indexer/plans/<hex>.json`. There is no store: a reference
  is a path or `sha256:<hex>`, and a path whose content hash differs from the
  requested hash is refused.
- **The locator is line-free identity plus evidence.** `path`, `language`,
  `qualified_name`, `symbol_type`, `decl_hash` and the observed `range`; the
  revision-scoped `symbol_id` appears nowhere. A plan carries one whole-file
  hash per touched file, the declared newline convention, one region hash per
  edit, and the old bytes when they fit under 8 KiB.
- **Coordinates are rules, not conventions.** 1-based inclusive lines, byte
  offsets into the stored bytes with offset 0 after any BOM (the BOM is
  preserved and lies inside no range), the terminator not part of its line, no
  columns, and a line/byte cross-check that turns a corrupted offset into
  arithmetic failure instead of a silent wrong edit. A final line without a
  terminator is legal; insertion after it is `position: end`, never a line
  number.
- **Ordering is part of the format.** Within a file descending start line,
  across files ascending path. Adjacent edits are legal; overlaps
  (`OverlappingEdits`, exit 2) and two insertions at one point
  (`AmbiguousOrder`, exit 2) are refused, never ordered.
- **`plan-validate` reports and never claims.** Per-check verdicts for the
  plan hash, schema, paths, file hash and newline, node re-derivation, the
  line/byte cross-check, region hashes, overlap and ordering, plus the
  parser's `parsed` / `parse_error` / `parse_unavailable` per file — read from
  the parser, never from the chunker's fallback. A stale file is `PlanStale`
  (exit 5) naming both hashes; an ambiguous node lists both candidates; a
  `semantic_required` plan is `SemanticProviderUnavailable` (exit 3).
- **`plan-show --diff` draws the change from the plan plus the current
  bytes** and writes nothing; without `--diff` it prints the document as read,
  unknown members preserved, because rewriting a plan would change its hash.
- **`--print` writes nothing at all** on the three writers: the canonical
  document goes to stdout, no plan file, no plan directory.
- New refusals ride ADR-0003's existing exit codes and add no number:
  `PathEscapesProject`, `NotUtf8`, `NewlineMismatch`, `MixedNewlines`,
  `CarriageReturnInPlan`, `SubLineEditUnsupported`, `OverlappingEdits`,
  `AmbiguousOrder`, `AmbiguousNode`, `LocatorMismatch`, `PlanStale`,
  `PlanFormatError`, `PlanHashMismatch`, `SemanticProviderUnavailable`.
- **`plan-apply` applies the plan, one filekeeper mutation per edit.**
  `plan-apply <ref>` runs a read-only pre-pass first — the same code
  `plan-validate` runs, so the two cannot disagree about what stale means —
  and one failure there refuses the whole plan (`PlanStale`, exit 5) with zero
  mutations. Each edit is then one `keeper file replace-lines\|insert-lines\|
  delete-lines` call carrying the hash observed immediately before it and, when
  the plan carries it, the expected region text; the next edit's hash is the
  `after_hash` keeper returned. Files apply in ascending path order and, within
  a file, start lines descending. A failure stops the run: no further file, no
  compensating write.
- **The report names every file** as `applied`, `would_apply`, `failed` or
  `not_attempted`, with per-edit ranges, operation ids and the keeper code and
  message on a failure, plus the plan id, the structural identity and whether
  the run is partial. A mid-loop failure is exit 1 `PartialApply` with the
  report on stdout once anything was written, and `KeeperFailure` when nothing
  was; keeper 5 maps to 5.
- **The way back is a list.** Every operation id is printed in application
  order and undone in reverse with `keeper history undo <id>`; a refused undo
  (exit 12) ends recovery and is reported, never retried. Every call carries
  `--task-id <plan_id>`, so `keeper history list --task-id sha256:<hex>`
  reconstructs the application from keeper's journal, and a JSON receipt lands
  in `.code-indexer/receipts/<plan-hash>-<timestamp>.json` with its path
  printed.
- **`--dry-run` writes nothing** and still runs the pre-pass: keeper's own
  `--dry-run` goes to every call and every file reports `would_apply`.
- **No fallback path.** An absent keeper is `KeeperUnavailable` (exit 3) with
  the install line, and a keeper whose `file replace-lines --help` lacks
  `--expected-hash`, `--expected-content` or `--dry-run` is
  `KeeperCapabilityMissing` (exit 3), naming the flag: the probe is by flag and
  not by version, because a build that keeps the version and drops a guard has
  to be caught.
- **Post-apply is a parse verdict, never a compile verdict.** Every modified
  file is reparsed and reported `parsed`, `parse_error` with the offending
  lines, or `parse_unavailable` where the language has no grammar — read from
  the parser, never from the chunker's fallback. The reparse writes nothing;
  `--refresh` runs one incremental pass afterwards and the report says so.
- `.code-indexer/` (the plan directory) is now ignored: plans are generated
  documents and are never committed.

**The identity model is now a query surface, and an ambiguous name is
reported instead of guessed.** `find-symbol` gains the structural filters
over the rows the identity pass already writes — `--kind` (with `--type` and
`--symbol-type` kept as aliases), `--name-pattern` (a case-sensitive glob
over the name; no index was added for it — a prefix glob is a range seek on
the existing name index, and a leading wildcard is a scan of the table (`SCAN s`), cheap
at this size), `--container`, `--visibility`, `--language`,
`--inherits NAME` and `--has-call NAME` — and they compose as AND in one read.
No schema change, no new index, no reindex: this release is query-only.

- **`--inherits` and `--has-call` are syntax-evidence filters, and say so.**
  Every symbol row now carries `evidence: ast|unknown`, read from
  the file's parse verdict. A symbol in a file with no AST extractor reports
  `unknown`, never a negative: on this repository's own index 91.8% of
  reference rows are textual, so absence of an attributed call is not
  absence of a call. An empty answer from either filter carries
  `evidence_note` (JSON) or one explanatory line (text) naming how many files
  were not attributed.
- **A capped answer says it was capped.** `find-symbol --json` carries
  `match_count` (the uncapped population, computed from the same clause set)
  and `truncated`; text mode prints `... N of M matches shown (--limit N
  reached; ...)`. `--limit` keeps its meaning and its default.
- **Ambiguity has an envelope.** An exact name with more than one definition
  carries `match_count` and `ambiguity.candidates[]` — file, range, kind,
  container, signature, language and `decl_hash` — instead of resolving to
  whichever row sorted first. `find-definition --exactly-one` is the
  single-symbol surface: an ambiguous name refuses with `ArgumentError`
  (exit 2) and lists every candidate, as data (`candidates`) in `--json`.
- **`find-callers` and `find-callees` union over every definition and label
  each direct hit.** Their JSON carries `match_count`, `ambiguity` when the
  name has several identities, and a `targets` list per direct hit naming the
  candidate definition(s) it can belong to — narrowed to one where the store
  attributes the edge (an import resolves to a file), all candidates where a
  name-level reference cannot be attributed.
- **The attributed filters' join is name-level, and documented as such.**
  `--inherits`/`--has-call` match a reference to the symbol's own name in its
  own file (`from_symbol` carries no container), so two same-named symbols in
  one file are both credited with the union of both bodies' attributed
  references. Narrowing that needs the staged reference resolution, not a
  query change; the docs say what a hit means today.
- **`heuristic` is reserved, not reachable.** No extractor produces it — a
  parsed file's rows are `ast`, the regex fallback's are `unknown` — and the
  identity docstring, skill/SKILL.md and docs/json-schema.md now say so.
- **`--exactly-one`'s empty answer is documented.** A name with no definition
  is `NotFoundError`, exit 1, where the same query without the flag answers
  `definitions: []` with exit 0 — the flag asked for exactly one, so it
  refuses rather than hand back an empty set.
- **Documented:** skill/SKILL.md and docs/json-schema.md describe the filters,
  the evidence label, the ambiguity envelope and the truncation signal.

## [Unreleased] — a read can destroy nothing

**A read command no longer opens the manifest read-write.** Every read opens
it read-only (`mode=ro`, no journal-mode write, no DDL, no migration), so a
read can not empty a table. A manifest recorded at an older schema revision
is REPORTED, not upgraded: `index-status` prints `schema=<n>`
(`manifest_schema` in `--json`), and every row surface refuses with the
existing `RebuildRequiredError` shape — one stderr line, exit 4, empty
stdout, the remedy named. `rows=absent` is the separate never-indexed
verdict; a manifest at an older revision whose rows carry this build's
stamp reports `rows=current`. Migration is a write pass's job, and
a pass repopulates the rows it re-created. No revision bump, no DDL change.

- **`semantic-search` refuses a vectors-off project before embedding.** The
  vectors-off and fingerprint gates now run over the resolved entries BEFORE
  the query embedding is computed, so the documented `ConfigError` naming
  `--vectors on` (exit 4) is what a caller sees on a machine with no
  reachable embedder, instead of the embedder's `BackendError` (exit 3).
- **The physical collection name always carries the embedding identity.**
  Every vector-writing pass — full, incremental and vectors-only rebuild —
  takes its collection from one helper (`idx_<slug>__<identity digest>`) and
  binds the `idx_<slug>` alias to it; the `__new` scratch name and the
  plain-slug defaults are gone. A changed identity also forces a re-embed
  into the new collection rather than reusing a hash cache that describes
  the old one.
- **`rows=current` no longer requires chunks.** The row verdict is decided by
  the completed-pass stamp and the extraction version, so a project whose
  indexed files legitimately produce no chunks reports `current` instead of
  `absent` for ever.
- **Dead surface removed:** `vectors.record_mode`,
  `fingerprint.VECTOR_SIDE_KEYS`, `Manifest.chunks_for_file`, and the four
  unreachable migration-hint branches, plus the callerless
  `fingerprint.rows_staleness_warning` (the staleness block renders that
  warning). The last-resort declaration hash is line-free, as the identity
  record promises.
- **`remove-project` reclaims the collection its alias serves.** The deleter
  resolves `idx_<slug>` to the physical collection behind it, drops any
  collection left from an older embedding identity, removes the alias first
  (so it cannot outlive the data), and reports only drops it performed.

## [Unreleased] — a pass that needs no service

**A pass no longer needs either service.** With no embedding or vector service
reachable, `add-project` builds a complete index — files, symbols, references,
imports, the `chunks` table, `chunks_fts` and `index_errors` — records
`vectors=off` and exits 0. A NEW project records `off`; an existing
vectors-on project stays `on` across the upgrade, and `auto` never turns
embedding on by itself.

- **The `chunks` table is real.** `chunks(file, chunk_index, start_line,
  end_line, symbol, symbol_type, chunk_hash, text)` is filled by the same pass
  that reads each file, keyed on `(file, chunk_index)`, and purged with the
  file by the existing per-file purge. `text` is the raw chunk text — the
  contextual header is still composed at embed time.
- **Qdrant is a vector store only.** The payload no longer carries a snippet
  and no read path takes text from it; the search snippet comes from the
  manifest.
- **`--vectors auto|on|off`** on `add-project`, `index-more` and
  `reindex-project`. Turning vectors on for a project whose rows are current
  embeds the stored chunk text and hash: no scan, no reparse, no source read.
- **Three version axes and a policy matrix.** `extraction_version` (rows),
  `embedding_version` (one composed string: model/dimension/embedded-text
  format) and `index_schema_version` (the manifest DDL). An extraction bump
  invalidates rows; an embedding bump builds a NEW collection under the new
  identity, swaps it in and drops the old one, leaving rows intact; a schema
  bump is migrated by the next WRITE pass and leaves the collection intact
  (a read reports the recorded schema and refuses rows written at an older
  revision until that pass runs).
- **Per-side staleness.** `index-status` reports `rows=current|absent`,
  `vectors=on|off` and `vectors_state=current|absent|stale|error`, in text and
  JSON (`schema` stays 1). A mismatched embedding identity no longer refuses
  the row surfaces: they answer and the answer says the vector side is stale.
- **`semantic-search` refuses a vectors-off project.** One `ConfigError` line
  naming `--vectors on`, exit 4, empty stdout; never a silent lexical answer,
  never an empty success. A mismatched embedding identity refuses as before.

## [Unreleased] — symbol identity and parse verdicts

**Breaking, and it is visible on the first query after upgrade.** Symbol rows
are now keyed by `(file, qualified_name, symbol_type, decl_hash)` — no line
number in the key — so a definition shifted by a line keeps its identity, two
overloads that differ only by their signature are two rows, and a row carries
`qualified_name`, `container`, `language`, `decl_hash` and a revision-scoped
`symbol_id`. A declaration hash cannot be derived from rows written by the
previous extractor, so the extraction version moved to 5 and **every project
reports `needs-reindex` until its ordinary pass runs** (`reindex-project`, or
the next pass under the new code). That pass is the only remedy, it is not a
paid operation, and it does not rebuild the vector collection: the embedding
identity is unchanged and unchanged chunk text re-embeds from the cache.

- **Refusals instead of answers on an obsolete index.** Every surface that
  answers from stored rows now exits 4 with one `RebuildRequiredError` line on
  stderr and EMPTY stdout on a `needs-reindex` project: `find-symbol`,
  `find-definition`, `outline`, `skeleton`, `overview`, `get-code-context`,
  `find-references`, `find-callers`, `find-callees`, `deps` and
  `changed-symbols`. Previously only `search` gated, so `find-symbol` and
  `overview` answered "no symbols" from rows the build would not produce.
  `list-projects`, `lookup-project`, `index-status` and `doctor` still answer
  — they need no rows. A machine-readable mode does not soften it: the refusal
  keeps stdout empty in `--json` (and `deps --format json`) too. `--refresh`
  still runs the pass first, so it repairs and then answers.
- **Per-file parse verdicts.** Every indexed file records `parsed`,
  `parse_error` or `parse_unavailable`, taken from the parser's own result
  (`has_error`, `is_missing`) and never inferred from the chunker, which cannot
  fail. A file the regex fallback handled reports `parse_unavailable`, not
  `parsed`. `index-status` counts the three per project in text
  (`parse=parsed:N,parse_error:N,parse_unavailable:N`) and in JSON.
- **Schema revision 7 ships the whole round's DDL.** Besides the identity
  columns, the revision creates the nullable `refs.target_symbol_id` and
  `refs.resolution` columns and the `chunks` table. Both are populated by the
  following leg and are empty here, so the migration and the reparse happen
  once for the round rather than twice. Migration is automatic on open and
  idempotent; a pre-7 symbols table is re-created empty because its rows
  cannot be re-keyed.

## [Unreleased] — dependency boundary and the narrowed MCP surface

- **Core installs and runs with no service.** `ollama` and `qdrant-client`
  moved into the `code-indexer[semantic]` extra and `mcp` into
  `code-indexer[mcp]`; `numpy` is listed nowhere (it arrives with
  `qdrant-client`). A core command constructs neither client, and an absent
  semantic extra fails with one line — `ConfigError: the semantic extension
  is not installed; install code-indexer[semantic]` — and exit 3, instead of
  a traceback. The MCP server is imported only by `code-indexer-mcp`.
- **Eleven MCP tools removed, and `find-definition` with them.** The surface is
  now the eight read-only query shapes: `list_projects`, `overview`,
  `skeleton`, `find_symbol`, `find_references`, `find_callers`,
  `get_code_context`, and `semantic_search` when the semantic extra is
  installed. Removed: `add_project`, `index_status`, `lookup_project`,
  `reindex_project`, `remove_project`, `changed_symbols`, `deps`,
  `find_callees`, `find_symbols`, `find_definition`, `outline` — **use the
  CLI subcommand of the same name**. `code-indexer-mcp --help` prints the
  registered set and the removals; the install line is now
  `uvx --from "code-indexer[mcp]" code-indexer-mcp`.
- **The MCP relay stops reporting failure as success.** A successful call
  returns the CLI's stdout; a failing call returns the CLI's stderr with
  `isError: true` and the exit code and `ErrorType` in the structured
  result. Reads publish `readOnlyHint=true` with `idempotentHint=true`.
- **Every MCP call appends one usage line** to
  `$INDEX_ROOT/mcp-usage.jsonl` (tool, outcome, exit code, response bytes,
  duration, timestamp — never the arguments).
- **Reads no longer probe a service for the fingerprint verdict.**
  `list-projects`, `lookup-project`, `index-status` and `doctor` compare the
  recorded fingerprint with the values configuration alone can state; the
  recorded embedding dimension is reported `unverified`, not guessed.
- **The package description no longer claims a semantic core.**

## [Unreleased] — read contract

- **Reads stop repairing the index; a failed query stops exiting 0.** Every
  command is now either a read or a write. A read (`overview`, `skeleton`,
  `outline`, `find-symbol`, `find-definition`, `find-references`,
  `find-callers`, `find-callees`, `deps`, `get-code-context`,
  `semantic-search`, `changed-symbols`, `index-status`, `list-projects`,
  `lookup-project`, `doctor`, `eval`, `eval-compare`, and `sweep-orphans`'s
  default dry run) opens no write transaction and runs no index pass; the
  implicit `STALE_TTL` refresh is gone. An answer describes the index as of
  `meta.last_indexed` and reports `fresh`, `stale` (with reasons) or
  `unknown` — one stderr line in text mode, an additive JSON block
  (`stale`, `stale_known`, `stale_reasons`, `stale_reason_count`,
  `index_revision`, `last_indexed`; `schema` stays 1) on every read answer.
  Repair is explicit: `--refresh` (one incremental pass under the
  per-project flock) or a pass command. `--skip-stale-check` stays accepted
  everywhere and is inert.
- **Exit codes are a taxonomy**: 0 success (including zero hits), 1 other
  runtime failure, 2 usage error, 3 backend/extension unavailable, 4 index
  requires a rebuild, 5 lock or concurrency conflict. A failed search now
  exits 3 (was 0) with `BackendError:` on stderr and stdout empty, so a
  backend outage is distinguishable from an empty result.
- **Unscoped queries are bounded**: one project by default, `--all-projects`
  to fan out, one embedding attempt with a short timeout (the retry chain
  stays on the write path), and a 30 s wall that reports unreached projects
  as `skipped` with `reason=budget` (`partial: true`).
- **`--json` on `list-projects`, `lookup-project`, `index-status`,
  `find-definition` and `find-references`**, carrying the fields the text
  renderer prints plus `ErrorType` on failure; the five read commands also
  carry the staleness block.

## [Unreleased] — lifecycle selector surface

- **Lifecycle commands share `index-status`'s project selector (issue #29).**
  `reindex-project`, `remove-project`, `lookup-project` and `index-more` now
  accept the project as a positional path **or** as `--project` / `--name`
  (path, slug, or custom name). For `reindex-project`, `remove-project` and
  `lookup-project` the flag wins when both are given; `index-more` accepts
  `<project> <subpath>` or `--project <project> <subpath>` and rejects the
  flag plus two positionals as ambiguous. Every
  selector reaches the one registry resolver, so the four commands behave
  like `index-status` instead of being positional-only. The positional forms
  remain the MCP call shape for `lookup-project`, `remove-project`,
  `index-status` and `reindex-project`; `index-more` has no MCP tool.
  Mutating commands —
  `reindex-project`, `remove-project` and `index-more` — never guess a
  project from the working directory: with neither spelling they exit with a
  usage error that lists the registered projects. `lookup-project`, being a
  read-only check, may still resolve by cwd on an empty call and keeps its
  print-and-exit-0 not-registered behavior. `index-more` supports both
  `index-more <project> <subpath>` and `index-more --project <project>
  <subpath>`, and rejects `--project` plus two positionals as ambiguous.

## [Unreleased] — publishing hygiene

- **The publishing rules are now a mechanical check (issue #27).**
  `scripts/publishing_hygiene_lint.py` (stdlib only) scans every tracked path
  and fails on three classes: absolute host paths — in capture data too, since
  a path from the machine that ran a capture is never publishable — plus
  internal profile names (held as sha256 digests, so the check forbids the
  names without publishing the roster; `--hash-name NAME` extends the set) and
  board card ids, both governed in authored surfaces only. Capture data under
  `runs/` keeps its payload: it is quoted evidence about another repository.
  Files skipped (binary, over 2 MB, tool/cache directories) are counted, so a
  skip is never a silent pass. `tests/test_publishing_hygiene.py` runs the
  check over the tree and proves each detector fires, and
  `.githooks/pre-commit` blocks a violating commit (activate with
  `git config core.hooksPath .githooks`). The records and docstrings the earlier
  scan found were neutralised in the preceding commit; test fixtures and capture
  payloads were left as evidence. CI enforces the rule through that offline
  suite; the hook is the opt-in local early warning, and its plain `python3`
  call is valid only while the lint stays stdlib.

## [Unreleased] — symbol-mode follow-ups

- **Symbol mode's changed-file path matches the exact name only (issue #24).**
  The re-resolve path accepted a live chunk whose symbol was the requested
  name **or** ended with `.` + the name. Extraction never stores a dotted
  name — C# takes the leaf of a `qualified_name`, C++ the last part of a
  `Foo::bar` declarator — so the suffix branch could never fire: a read-only
  count over the eleven manifest tables on the development host found zero
  dotted symbol names. The branch is deleted; the exact-name comparison is
  the whole rule, with no replacement heuristic and no cap. The extractor
  comment in `ts_chunker.py` now states what is stored — the leaf — instead
  of what a caller might wish to match, and a test pins the changed-file
  rule against a live chunk named `pkg.prepare`. No reindex is owed: no
  stored row changes.
- **The eval harness scores every same-name row in a file (issue #25).**
  `_symbol_range` read the label's rows uncapped but scored against only the
  first of them, which was safe only because every measured duplicate is a
  decorator double-row with near-identical ranges — a label whose rows in one
  file diverge (an overload set, a redefinition, a wrapper) would have scored
  a hit inside the second row as a miss. `_hit_correct` now reads every row
  through `_symbol_ranges` and any overlap wins; nothing is capped or
  truncated. A fixture pins a label with two disjoint rows in one file and a
  hit inside the second row scoring correct. The unscoped past-the-cap
  fixture still passes. No reindex is owed: the eval reads rows the store
  already holds.
- **One decorated definition answers as one range (issue #26).** Symbol mode
  returned every matching symbol row, and one decorated definition
  contributes two rows (the decorator line and the declaration line, same end
  line) — `cli.py` `get_code_context` came back as `1435-1452, 1436-1452`, so
  a caller counting segments overcounted definitions. Ranges of the requested
  name that overlap or touch are now merged into one contiguous span per run
  before segments are built: the decorator pair collapses to one span, while
  genuinely disjoint ranges stay separate. The rule is stated in the
  `code_context` docstring and in `skill/SKILL.md`, and pinned by a test using
  a decorated definition (one segment, one honest range). Each symbol-mode
  segment now carries `symbol_rows`, the number of stored rows it merged, so
  the merge is visible in the answer instead of implicit in the range. The
  field is additive — `schema: 1` is unchanged, and line-range mode's segment
  shape is unchanged. The order is unchanged. The eval any-overlap hardening
  reads rows from the manifest, never segment counts, so it does not depend on
  this merge. No reindex is owed: the stored rows are what they were.
- **The merge runs on the rows' own ranges, then `--context-lines` pads each
  merged span (issue #26).** `_merge_symbol_ranges`' adjacency test (`start <=
  previous_end + 1`) fuses any two windows that touch, so merging AFTER
  padding would fuse two genuinely disjoint definitions of the requested name
  that sit a few lines apart: with `--context-lines 2` the padded windows
  touch and the whole file would answer as one segment including the code
  between the definitions. The order is identity first, presentation second —
  the rows' own ranges are merged, then `context_lines` is applied to each
  surviving span — so a display flag no longer changes how many segments
  answer. Line-range mode is unchanged. Pinned by three tests: six
  definitions four lines apart answer as six padded spans, two disjoint
  definitions answer as two padded spans, and the changed-file re-resolve
  path keeps every definition under padding. The rule is stated in the
  `code_context` and `_merge_symbol_ranges` docstrings and in skill/SKILL.md.
  No reindex is owed: no stored row changes.

## [Unreleased] — get-code-context symbol resolution

- **`get-code-context --symbol` finds a definition whose row sorts past the
  25-row exact-name cap (issue #23).** Symbol mode read the exact-name rows
  through `Manifest.find_symbols`' default `limit=25` and only then filtered
  to the requested file in Python, so a file whose row sorted past the cut
  came back empty and the command reported the stored row as absent:
  `InternalError: symbol '__init__' not indexed in
  src/code_indexer/registry.py` (row 26 of 40 on this tool's own index;
  `doctor.py` is row 40). The lookup now filters name and file in one
  uncapped read — `find_symbols(symbol, substring=False, file=rel,
  limit=None)` — so the answer no longer depends on where the row sorts. The
  answer shape (`file` / `stale` / `re_resolved` / `segments`) and the order
  (ast rows first, then `start_line`) are unchanged. No reindex is needed:
  the store already held the row.
- **The eval harness scores the same definitions (issue #23).**
  `evalharness._symbol_range` had the identical capped-then-filtered shape
  and returned `None` for every definition past the cap, so a hit that
  matched a late-sorting definition was scored as a miss. It uses the same
  scoped, uncapped read. One shipped eval query moves with it:
  `eval/queries.jsonl` (`q-concept-04`) labels
  `src/code_indexer/manifest.py` / `__init__`, whose row is 33 of 40 — the
  one label in the shipped set the old read could never resolve — so
  overlap scoring was silently unavailable for it. An `eval-compare` against
  a report recorded before this change is therefore not comparable for that
  query. No eval report is tracked in the repo, so no recorded score needs
  repair.
- **Symbol mode returns every matching symbol row, not the first five.** Both
  `code_context` segment paths carried `[:5]` — the manifest rows and the
  live re-resolve on a changed file — which silently shortened a name with
  more than five matching rows in one file. The cap is gone rather than
  announced: a file's own rows for one name are a small, finite answer, and
  an unannounced cap on an answer was the defect. Rows are not definitions:
  a decorated definition contributes two rows, so it surfaces as two
  overlapping ranges.
- The rest of `find_symbols`' 25-row default is untouched: `find-symbol`'s
  browse and substring fallback and the exact-symbol pin in the fused
  ranking depend on it deliberately.

## [Unreleased] — the definition roots reach the walk, and a prefix scopes its own hotspots

- **The definition-roots read no longer stops at 25 (issue #22).** The read
  behind a graph walk's roots — `Manifest.find_symbols` — defaulted to
  `limit=25` with no trace, so `find-callers __init__` on this tool's own
  index walked 25 of the store's 40 `__init__` subtrees and certified the
  answer `truncated: false, omitted: 0`. That was issue #22's filed symptom,
  live, one layer under the reads the previous entry fixed. It now goes
  through `Manifest.definitions_of(name)` — the exact-name read with no cap —
  as do `find-callees`' roots, a dotted subject's qualifier resolution and
  `_definer_of`. `find-symbol`'s browse mode keeps its own 25-row default;
  exact-name `find-symbol` and `find-definition` answer with every definition
  (and `--limit` is honoured when given, which no surface could do before).
  Live: `find-callers __init__ --max-nodes 400` reports **25 → 40 roots**,
  `truncated: false`.
- **A root set larger than `--max-nodes` is cut, and says so.** Roots still
  consume the node budget; when they alone exceed it the walk shows the first
  `cap`, sets `truncated`, and prints `40 definition roots exist for this
  subject, node cap 10 — 30 roots not shown; raise --max-nodes`. Those
  refusals are not `omitted` (a definition list is not an edge list) — but nor
  is `omitted` `0`: roots that spend the whole budget leave no slot for
  level-0 edges, and those refusals are counted (`find-callers __init__
  --max-nodes 10` on this tool's own index: `roots 10, truncated: true,
  omitted: 124`).
- **`overview --path-prefix` attributes a name to its in-prefix definition
  again.** `file_fan_in` picked the owning definition over the whole store and
  `build_overview` then filtered to the prefix's files, so a name defined both
  inside and outside the prefix lost every reference. Scoping the ownership
  aggregate to the files being reported on recovers, measured on read-only
  live manifests: **532** rows under `docs/` and **6,044** under
  `docs/verification` on one index, **20,295** under `docs/` on another; on
  that second index `docs/SUMMARY.md` moves 7,345 → 8,096 and a second file
  enters the top ten. A full overview is unchanged.
- **The capped reads' order keys are total.** `callers_of` now orders
  `(file, line, relationship, to_name, receiver)` and `import_rows`
  `(file, line, raw_target)` — both tables' primary-key column sets (+ the
  receiver for refs). `ORDER BY file, line` left the order inside a tie group
  to the query plan, and the tie groups are not rare: 73,911 tied `(file,
  line)` groups in one live `refs` table, 107,401 in another, 3 in a 488-row
  `imports` table. A capped read is now a reproducible prefix, not a subset
  that happens to agree today.
- **`changed-symbols --impact` names the bound it hit.** Each file entry
  carries `bounds: {per_name, node_budget, max_nodes}` beside `truncated` /
  `omitted`, and the note advises only what works for the bound that fired:
  the per-name cap of 50 does not answer to `--max-nodes` (use
  `find-callers <symbol>`), the shared node budget does. The text preview
  prints six caller lines and now adds `(+N more)`; the JSON carried all of
  them, so the text was the only place the cut was invisible.
- **`Manifest.fan_in_counts` is deleted.** No caller at this tip or at the
  round's base, no test, and its docstring claimed the overview hotspots — a
  surface it never fed (it counted names; hotspots count files). A store layer
  that hands over everything should not keep a silent 20-row cap in it.
- Query-time only: `EXTRACTION_VERSION` stays 4, no schema or row-key change,
  no reindex owed, exit codes and `server.py` untouched.

## [Unreleased] — changed-symbols reports its own bounds (issue #22)

- **`changed-symbols --impact` says when its caller list is cut (issue #22).**
  The impact list has two bounds and reported neither: a per-name store read
  (`callers_of(name, limit=50)`) and a shared node budget across the whole
  diff (`max_nodes`). Both were applied in silence, so a payload listing five
  callers read exactly like a symbol called five times. Each file entry in
  `impact` now carries `truncated: <bool>` and `omitted: <int>` — the same
  vocabulary `find-callers` / `find-callees` / `deps` use, so a consumer reads
  one signal, not two kinds of truncation — and the text renderer prints
  `impact: N more caller references not shown ...` beside the callers it did
  print. `omitted` is a count of reference rows the bounds refused: the
  per-name read's refusals come from one aggregate (`ref_relationship_counts`)
  and the budget's are counted as they are skipped. It is a count of rows
  withheld, never a total for the symbols the diff did not name.
  The per-name read keeps its cap on purpose: a diff can name many symbols and
  the impact list is a navigation aid, not a graph walk — it is now simply a
  cap that speaks.

## [Unreleased] — the store reads hand over everything (issue #22)

- **No store read under these walks caps silently any more, and `overview`
  hotspots are computed instead of sampled (issue #22).** Issue #21 made every
  *graph* cap announce itself, but the reads one layer beneath them were still
  bare `limit=` caps: `Manifest.callers_of` and `callees_of_file_symbol`
  stopped at 200 rows, `import_rows` and `callers_of_all` at 5,000, with no
  signal anywhere. (The definition-roots read, which still capped at 25 at the
  time of writing, is fixed in the entry above.) The
  graph could not be honest about the population while the layer beneath it
  handed it a short list, and the orientation surface every agent reads first
  was reporting sampled hotspots as if they were complete.
  **The store now hands over everything it holds.** `callers_of`,
  `callees_of_file_symbol` and `import_rows` take `limit: int | None = None`,
  and no limit means no cap; an explicit limit stays usable for a caller that
  wants a bounded read and says so in its own output (`changed-symbols` does).
  `import_rows` also gained an order key, so a capped read is a reproducible
  prefix rather than an arbitrary one — corrected in the entry above to
  `(file, line, raw_target)`, a total key; `(file, line)` alone was not.
  This was live-reachable, not theoretical: on one live index a single target
  name holds 10,258 reference rows and another holds 18,493, so the 200-row
  read answered with 1.9% of the truth and called the answer complete. That
  same walk now reports `truncated: true` with `omitted: 10218` at the default
  node cap — a lower bound on what it refused, where it used to print 200
  nodes and claim nothing was missing.
  **`overview` fan-in hotspots are now one SQL aggregate** (`file_fan_in`)
  over resolved imports into a file plus references to a name that file
  defines, excluding self-references — the same intent the old row scan had,
  minus the sample. The old code counted `callers_of_all(limit=5000)`: 0.8% of
  the `refs` table on a large index, and with no `ORDER BY` not even a stable
  0.8%. `callers_of_all` had no other caller and is gone.
  **This changes visible output, and that is the fix.** On live indexes:
  `overview` on a 608k-reference project reports its top file at
  **184 → 19,183** fan-in (second place **40 → 3,627**); on a 935k-reference
  project the top file goes **253 → 55,503** (second **76 → 16,662**). Any
  hotspot ranking computed before this change was a sample, not a count.
  Measured cost of the complete answer: `overview` 0.885 s → 1.087 s and
  0.891 s → 1.483 s on those two indexes, `find-callers` 0.869 s → 0.887 s and
  0.895 s → 1.025 s (medians of three runs, cold process). No new index was
  needed.
  **Unchanged:** query-time only — no extraction query, schema, row key or
  fingerprint moved, so `EXTRACTION_VERSION` stays 4, no reindex is owed, and
  exit codes are as they were. `omitted` keeps its documented meaning (edges
  examined and refused at the node cap, a lower bound); it is now a truer
  lower bound.

## [Unreleased] — an incomplete graph answer says so (issue #21)

- **`find-callers` no longer loses a second call site in the same function, and
  every graph cap now reports what it refused (issue #21).** Two defects sat
  behind one symptom. The first was node identity: `find-callers` keyed its
  visited set on `(file, symbol)` — the *enclosing* symbol — so two calls to
  the same target on two lines of ONE function were the same node, and the
  second was dropped by the cycle guard before it could be printed. Nothing
  said so: the answer looked complete. A caller node is now the call SITE,
  `(file, line, from_symbol, relationship)`, at every BFS level (a transitive
  call site is not swallowed either), so each site keeps its own line and its
  own receiver; definition roots keep `(file, symbol)` identity. The second
  defect was the caps: `find-callers` / `find-callees` (`count >= max_nodes`)
  and `deps_for_file` (`len(seen) < max_nodes`) applied a reached cap as a
  bare `continue`, so an edge disappeared with no trace, and `graph_to_json`
  emitted only `{"roots": ...}`. A refused edge is now counted. In `--json` the
  payload keeps `roots` exactly as it was and gains two top-level siblings —
  `"truncated": <bool>` and `"omitted": <int>`, the number of edges the walk
  examined and refused at the node cap (`0` when nothing was refused); `deps`
  keeps its per-level `in`/`out` dicts and gains the same two fields. In the
  text tree the result gains one `note:` line naming the cap and the count,
  phrased as a lower bound — what this walk examined, never a claim about the
  whole population:
  `note: node cap 4 reached — 61 more reference edges not shown (a lower
  bound: a refused edge is counted but never expanded, so its own edges are
  not walked, and a depth cap on the same answer can cut more); raise
  --max-nodes to see more`. A depth cap is reported too, because a
  depth-bounded answer is incomplete: when the last level leaves a frontier
  with unvisited edges, the answer says `deeper references exist beyond
  depth N` (and the callee / import-edge variants). Those lines print on
  stdout with the answer; the explanatory notes of a complete, non-empty
  answer stay on stderr as before.
  `find-callees` deliberately keeps TARGET-set identity `(file, to_name)` — its
  question is "what does this symbol reach", so a caller calling one target
  twice is one callee — and that asymmetry with `find-callers` is now written
  down in the module docstring, the command docstrings and `skill/SKILL.md`.
  **Expect more rows, and more truncation.** Every import statement is now its
  own site, so a heavily referenced symbol lists more nodes per answer and
  reaches the default `--max-nodes 40` sooner: expect more nodes per answer,
  and expect the default caps to report truncation more often than before.
  **Unchanged:** this is a query-time change only — no extraction query,
  schema or fingerprint moved, so `EXTRACTION_VERSION` is untouched, no
  reindex is owed, and an existing index answers with the fix immediately;
  exit codes are as they were (success is still 0); the graph is still textual
  and unbound; the MCP server needed no new logic and relays the new fields
  because it relays the CLI's own output.

## [Unreleased] — "cannot verify" is a verdict of its own (issue #18)

- **`index-status` no longer goes silent when the fingerprint verdict cannot
  be reached (issue #18).** Reaching that verdict means probing the running
  configuration — asking the embedder for its dimension — so on a host where
  the embedder is unreachable the probe fails and the status call has no
  answer to give. It used to give none *visibly*: the field was simply
  omitted, leaving an operator unable to tell "the index matches" from "I
  could not tell", which is the misleading-by-omission shape the surrounding
  fix exists to remove. The fingerprint vocabulary now carries a third
  value: `index-status` prints
  `fingerprint=unverified reason=embedder-unreachable` in that case, and a
  missing `fingerprint=` field now means exactly one thing — the index
  matches the running configuration. "Cannot verify" is its own value and is
  never folded into the pass state; `needs-reindex` still means a *verified*
  mismatch. Nothing else moves: a merely unverifiable index is not made a
  refusal (`ok` keeps its meaning of "no known mismatch"), so which surfaces
  refuse is unchanged and no emitted fingerprint state is re-stamped. The
  command's help text and `skill/SKILL.md` state the behaviour.

## [Unreleased] — the fingerprint refusal renders as one line (issue #16)

- **a mismatched index now refuses with one clean error line, not a rich
  traceback (issue #16).** `semantic-search` was the only command whose
  failures escaped to the framework: a fingerprint-mismatched index raised
  out of the command body, so an operator got a wall of traceback with the
  actionable sentence (`run: code-indexer reindex-project <path>`) buried at
  the bottom. Worse, the message sources baked a `ConfigError: ` prefix into
  the *message*, and the shared failure taxonomy renders one line as
  `<ErrorType>: <message>` — so the type was printed twice
  (`ConfigError: ConfigError: index for ...`). The refusal now goes through
  the same failure seam every other subcommand uses (`errors.fail`): exactly
  one `<ErrorType>: <message>` line on stderr and the taxonomy's exit code,
  with the traceback available under `--verbose`. The three message sources
  that carried the baked-in prefix (`fingerprint.mismatch_error`,
  `Core.assert_fingerprint_ok`'s fallback, and the embedder's fatal
  model-missing error) no longer carry it, so the taxonomy is the single
  source of the prefix. **Which surfaces refuse is unchanged**: `search`
  still refuses a mismatched index, and the manifest-only surfaces still warn
  and answer.

## [Unreleased] — fingerprint reporting (issue #13)

- **`index-status` no longer reads as idle on an index that owes a rebuild
  (issue #13).** Its output was built only from the in-process *pass* state, so
  a project whose index no longer matched the current configuration — which
  `doctor` and `list-projects` both call `needs-reindex` — printed a plain
  `state=idle`, two readers giving two answers on the same binary. It now also
  reports the *fingerprint* state: `fingerprint=needs-reindex reason=<diff>` is
  printed whenever the recorded fingerprint does not match the running
  configuration, the same verdict the other lifecycle surfaces give. The
  `index-status` docstring and `skill/SKILL.md` describe both vocabularies (pass
  state vs fingerprint state) so they are no longer confusable.
- **the manifest-only surfaces warn once instead of answering silently
  (issue #13).** `find-references`, `find-callers`, `find-callees`, `deps`,
  `skeleton`/`map` and `outline`/`file-outline` read stored rows and are NOT
  made fatal on a mismatch — refusing them would remove the tool's usefulness
  on exactly the index a user is inspecting. They now print one stderr warning
  line when the stored rows may have been produced by different extraction
  code, comparing only the version-ordered keys (`chunker_version`,
  `extraction_version`) and naming the recorded and current values. The
  embedding-configuration keys (`embed_model` / `embed_dim` /
  `embed_text_version`) are deliberately NOT consulted — they affect the
  vectors, not the stored rows — and that asymmetry is stated in
  `fingerprint.rows_staleness_warning`. `search` remains fatal on a
  fingerprint mismatch, unchanged.

## [Unreleased] — the orphan sweep (issue #11)

- **new `sweep-orphans` subcommand clears deleted-file residue (issue #11).**
  A row in `refs`, `imports` or `symbols` whose `file` has no `files` row and
  whose file is gone from the working tree is residue from a deletion that did
  not clean up. The command is a **dry run by default**: it prints the
  per-table counts and the file list it would clear and changes nothing;
  `--apply` removes them in one transaction and reports the count removed. It
  reads the manifest directly and never triggers an index pass. No implicit
  purge is added to the pass.
- **the delete path now purges `imports` rows too (issue #11).** This is the
  one behaviour change in this leg: `Manifest.delete_files` removed `files`,
  `symbols` and `refs` rows for a deleted path but skipped `imports`, so every
  deletion left that file's import rows behind as orphans. New deletions no
  longer accrue that residue; `sweep-orphans` clears what earlier ones already
  left.

## [Unreleased] — require scoping, pidfile-probe honesty, a vacuous assertion (issues #12, #14, #15)

**This round needs a rebuild**: `EXTRACTION_VERSION` is `4`, so every existing
index reports `needs-reindex` and refuses queries until `reindex-project`
re-extracts. Existing live indexes were still recorded at `2` (never rebuilt to
`3`), so this bump adds no work beyond the rebuild that was already pending.

- **a JS/TS call with a string argument is no longer stored as an import AND
  an include (issue #12).** `queries/typescript`, `queries/tsx` and
  `queries/javascript` captured the require shape twice with no predicate on
  the function name, so `alert("hi")` produced an `imports` row (kind
  `require`, target `hi`) **and** an `includes` reference row. Both patterns
  are now scoped with `(#eq? @_fn "require")`: a `require(...)` call still
  yields its `imports` row (kind `require`) and its `includes` row, and an
  ordinary string-argument call yields neither (only its `call` row). The
  dead, unhandled `@import.require-target` capture was dropped in the same
  edit. The claim in the query comments that the pattern behaved "exactly as
  the C/C++ queries do" was inaccurate — the C/C++ capture matches the quoted
  include node only — and is corrected in place.
- **the pidfile liveness probe no longer opens read-write (issue #14).**
  `watcher.pidfile_is_free` opened the pidfile `O_RDWR` and returned "free" on
  any `OSError`, so a live watcher whose pidfile this user cannot open
  read-write read as free and `doctor` reported `stale pidfile` about a live
  watcher. `flock` works on a read-only descriptor, so the probe now opens
  `O_RDONLY`, and a new `watcher.pidfile_liveness(path) -> "free" | "held" |
  "unknown"` distinguishes "cannot open at all" (unknown) from "free".
  `pidfile_is_free` is kept for existing callers but maps unknown to *not
  proven free*, so no caller turns ignorance into a confident negative.
  `doctor`'s `watch` line renders unknown as a warning that is not the stale
  message; `stop_watcher` gains an `unknown` state and its "no watcher
  running" branch no longer claims absence on an unknown probe; `unwatch`
  exits 1 with a "cannot determine" line for it.
- **a test assertion that could never fail (issue #15).**
  `tests/test_graph.py` compared a reference row's `from_symbol` (index 1)
  against line numbers while meaning the line (index 0), so the
  "names only in comments/strings are never captured" property was untested.
  The assertion now indexes the line field, and the positive direction (the
  same name IS captured from real code, on its line) is pinned alongside it.
  The repaired assertion was then proven to fail under a mutation that lets
  the AST path also match string content, and to pass again once the file was
  restored byte-exact.

## [Unreleased] — watcher and index-state hygiene (issue #10)

Three index-side defects fixed at their source, plus the operational
ordering that keeps the third one from recurring. The code changes
themselves imply no reindex; the procedure below is what a version bump
already needed.

- **`watch --all` survives one bad registration.** A registered project
  whose root is missing or unreadable is now skipped with a single warning
  naming it, and the remaining projects are served; the watcher reports the
  projects it actually covers and exits nonzero only when it covers none.
  The failure that actually occurred — watchdog raises the
  `inotify_add_watch` error from the observer's `start()`, not from
  `schedule()` — no longer aborts the watcher: it degrades to the documented
  quiet-period polling fallback, which the docstring already promised. A
  `--background` parent also re-confirms the daemon is alive and still
  holding the pidfile after the handshake, so it never prints
  `watch daemon started` for a daemon that died on entry (previously it
  exited 0 over a dead daemon).
- **`doctor` no longer calls a live watcher stale.** The `watch` line reads
  liveness from the pidfile **flock**, not from pid existence or the file
  text. The pidfile format is `<pid> <iso-timestamp>`, so the old
  whole-file `int()` parse failed on every live watcher and reported
  `stale pidfile (pid None not running)`. A held flock now reports `running
  (pid <pid>)`, a held flock with an unreadable pid reports `running (pid
  unreadable)`, and only a free flock with a pidfile present is genuinely
  `stale pidfile`. Query-time only; no reindex implication.
- **a process does not overwrite a fingerprint it does not recognise.** A
  pass whose manifest was recorded by NEWER code than the running process (a
  version-ordered key — `CHUNKER_VERSION` / `EXTRACTION_VERSION` — recorded
  higher than this process's own) now logs a loud warning naming the
  project, both fingerprints and the remedy, and defers the re-stamp,
  leaving the newer value alone. Previously the mismatch check was
  direction-blind: an older-code watcher's sweep re-embedded the whole
  project with its older extraction code and then stamped its own older
  version, reverting the flag and replacing good vectors. The legitimate
  direction — an index built by older code, rebuilt by a newer process —
  still rebuilds and re-stamps.
- **operational ordering documented.** After any version bump, stop all
  watchers, rebuild, then restart them on the new code:
  `unwatch --all` -> `reindex-project <path>` -> `watch --all --background`.
  A watcher left running across a bump silently reverts the fingerprint
  every sweep. Recorded in the `watch --help` text and this repo's
  `skill/SKILL.md` watch details; `doctor`'s fingerprint check names the live
  watcher when a mismatch is present.

## [Unreleased] — reference-store coverage and precision (#9)

The two load-bearing coverage gaps the store unification left behind are
closed — a `calls` filter answers on a language with no AST query, and
`includes` is populated for every AST language, not just C/C++ — plus the C#
member-access precision pass and the CLI honesty fix. **This round needs a
rebuild**: `EXTRACTION_VERSION` is `3`, so every existing index reports
`needs-reindex` and refuses queries until `reindex-project` re-extracts the
new rows.

- **textual `call` coverage.** The textual scan (every language with no AST
  query, markdown included) now stores a `call` row for an identifier followed
  immediately by `(`. Measured over the live indexes, that adds 20,157 rows:
  13,338 (≈66%) are `.csx` script call sites (real code) and the other 6,819
  (≈34%) are not — markdown 6,468 (≈32%), then json 244, shell 78, text 9,
  toml 2 and a few stragglers. Allowing whitespace before the paren would add a
  further 89,882 matches that are overwhelmingly prose (79,551 of them
  markdown, e.g. `function (see below)`), so the tight form is the documented
  rule. That removes the spaced-prose flood but does **not** make markdown
  prose call-free: the `word(s)` shape is tight, so `commit(s)`, `actor(s)` and
  `project(s)` in prose are still stored as `call` rows. One occurrence yields
  one row: a tight `name(` is a `call`, any other identifier an `identifier`.
  Rows stay `confidence='heuristic'` — a `name(` in a string or trailing
  comment still matches.
- **AST `includes` coverage.** Every AST language now routes its import
  directive into the reference store as an `includes` row, in parallel with
  its unchanged `imports` row: python module names, TS/JS module specifiers
  and `require` sources, Go import paths, and C# `using` targets (the capture
  is a second `@ref.include` on the SAME node, exactly as the C/C++ queries
  already did). `find-references <module> --relationship includes` therefore
  answers for python/typescript/go/csharp instead of a bare negative.
  `queries/tsx/` and `queries/javascript/` were
  missing entirely, so `.tsx` and `.js` files silently produced NO reference
  rows at all; both now have real query files, and `.jsx` resolves to the
  `javascript` language like every other JS extension. Angled C/C++ system
  headers remain unresolved and produce no row (documented, pinned by test).
- **a filtered-empty report is honest.** `find-references X --relationship
  calls` no longer prints a bare negative that reads as "nothing calls this"
  when references to `X` exist under other relationships: it now prints
  `no calls references to 'X'; 12 references exist unfiltered (identifier,
  type)`. One aggregate query, not a second report; the bare line is kept for
  a genuinely absent name.
- **C# member-access precision.** Only the OUTERMOST access of a chain is a
  reference: `System.Console.WriteLine(x)` yields the `WriteLine` row and no
  row for the intermediate `Console`/`System` segment, which previously
  became a reference target nobody can call or read. Measured on 359 live
  `.cs` files: 3,805 of 61,190 stored reference rows were such intermediate
  segments (63 distinct receiver-shaped names — `Ledger`, `World`, `Producer`,
  `CultureInfo`, `JsonSerializer`). The stated trade-off: a surviving row of a
  chain deeper than one hop now carries `receiver=NULL`, because its receiver
  expression is itself a member access. The same-line receiver collapse
  (one row per `(file, line, to_name, relationship)`) is documented in
  `graph_extract`, `manifest` and skill/SKILL.md rather than fixed: keeping both
  rows would require a `refs` table rebuild to change the primary key, which
  was measured as unclean against the gain for a rare shape. Both routes are
  pinned by test.
- **stale residue cut.** `docs/subcommand-design.md` no longer claims `deps`
  reads `symbol_refs` (it reads the `imports` table) and its supersede note
  names the v6 store; `docs/audit.md` no longer describes
  `ts_chunker.extract_refs` as the live reference producer. The dead
  production-looking pair `Manifest.find_refs` / `RefRow` (tests were their
  only callers) is deleted — `tests/test_csharp_references.py` and
  `tests/test_codeintel.py` now read through the real reader,
  `Manifest.refs_to` (and `Core.find_references`). The
  `replace_file_refs_heuristic` stub is gone from
  `tests/test_indexer_embed_context.py`; the method does not exist.

## [Unreleased] — one reference store, a C# AST query, precision pass (#8)

The two reference tables became one, C# stopped being textual-only, and the
precision debt issue #8 recorded was paid. **This round needs a rebuild**:
`EXTRACTION_VERSION` is bumped, so every existing index reports
`needs-reindex` and refuses queries until `reindex-project` re-extracts the
unified rows.

- **one reference store, one writer (schema v6).** `symbol_refs` is gone;
  `refs` is the single reference table with a single writer (`graph_extract`),
  one row shape and one honest `relationship`/`confidence` per row, plus a
  `receiver` column (the member-access receiver as written, plain identifier
  or NULL). A call is stored once, so the union's duplicate-line symptom
  dissolves. Relationship vocabulary: `call`|`type`|`base_class`|`member`|
  `includes`|`identifier`. Rows are written at the commit phase, after the
  file's vectors, so a crash never claims references that are not there.
  `find-references` reads the one store and labels each row with the
  confidence it carries; `--relationship` still spans
  `calls`|`includes`|`inherits`|`references`.
- **a tree-sitter C# references query** (`queries/csharp/references.scm`).
  C# joins the AST languages, so a property/field READ through a member
  access (`a.OughtRoleBindings`) is a real reference row with
  `confidence=ast` instead of nothing, and a qualified static call
  (`ArmorResolution.Classify`) is a caller row; both record the receiver
  expression as written when it is a plain identifier. Names appearing only
  in comments or strings do not match. C# `using` directives are recorded as
  imports, left unresolved (documented, never guessed).
- **precision pass (issue #8).** (1) `find-callers` no longer asserts a
  receiver it never resolved: the subject is reported once on the `note:`
  lines and `receiver=` appears on a direct hit only, and only when the store
  holds one for that row. (2) `semantic-search --format json` carries
  `skipped`/`skipped_count` (plus a trailing line in text/compact), so a
  consumer can tell the result is partial. (3) skill/SKILL.md names the exact
  `--fresh` command set. (4) `tests/test_flag_contract.py`'s description now
  matches its parametrisation and covers `changed-symbols`. (5) the two-store
  union note is gone from skill/SKILL.md. (6) the textual scan's comment rule is
  stated precisely: lines BEGINNING with `#`/`//`/`*`/`--` are skipped, while
  trailing comments and strings match.
- **`read_fingerprint` dropped `extraction_version` (found while running this
  round's rebuild).** The recorded fingerprint was rebuilt from four of its
  five recorded keys, so `extraction_version` silently fell back to the
  *current* value on every read and an extraction change could never mark an
  index stale. Fixed — the `EXTRACTION_VERSION` bump above now actually flips
  every existing index to `needs-reindex` — and pinned by a read-back
  round-trip test.

## [Unreleased] — agent-report round (issues #5, #6, #7)

Three reported defects, fixed at the query/manifest seam; no index rebuild is
needed (all three are query-time or listing-time changes).

- **whole-project search no longer dies on one unresolvable project**
  (c5341bb, issue #6). One registered project whose collection is gone
  aborted every project-less `semantic-search` with a single trailing error
  line and zero hits. Per-project failures are now isolated: the project is
  skipped with a loud warning naming it, the remaining projects' hits are
  returned, and the run reports the skipped count. If no project answers the
  run still errors. The fingerprint (`needs-reindex`) gate stays fatal and
  deliberately sits outside the skip path — a wrong-model index answers wrong
  silently. `list-projects` fetches ONE collection listing (physical
  collections + aliases) and reports a project that has completed a pass but
  whose collection is gone as `state=error collection=missing` instead of a
  healthy `idle`. The live end-to-end test now deregisters what it registers
  (it leaked one registration per live run).
- **`--skip-stale-check` is accepted everywhere, as documented** (efebc91,
  issue #7 second defect). `find-callers`, `find-callees`, `deps`,
  `overview` and `changed-symbols` did not accept it while skill/SKILL.md
  claimed every command does; all five now take it and route it into the
  Core, and `eval-compare` accepts it as a no-op so the claim is literally
  true. skill/SKILL.md states exactly where the flag changes behavior.
- **`find-references` and `find-callers` stop under-reporting** (issues #5,
  #7 main claim). `find-references` now reads the UNION of both reference
  stores (`symbol_refs`: invocation targets `calls`/`includes`;
  `refs`: AST `call`/`type`/`base_class` plus the textual `identifier`
  fallback for languages without an AST query, C# included), labelling every
  row with the store and confidence it carries, and a bare "no references" is
  printed only when both stores are empty. `find-callers` accepts a dotted
  `Type.Member` subject (qualifier resolved against the symbol rows, fan-in
  matched on the trailing member name, receiver reported per hit), and when
  the subject has no definition row (properties/fields are not extracted as
  symbols) it seeds from the `refs` rows and says so on `note:` lines instead
  of printing a bare `no results`. skill/SKILL.md and the command docstrings
  document which store answers what and that the graph is textual/unbound.
  Follow-up recommended but NOT done here: a tree-sitter C# references query
  and/or unifying the two reference tables (see the round report).

Documentation-only corrections (no behavior changes); docs drift now guarded
by `tests/test_docs.py` (runs in CI via `pytest -m "not live"`).

- **skill/SKILL.md** (agent-facing, 720d39c):
  - decision tree stated the wrong default ranking; the real default is
    `--ranking vector` (pure cosine); added a one-line recommendation to pass
    `--ranking hybrid` for exact-identifier queries; removed the
    "rerank/heuristic" wording that is not flag values.
  - command block de-duplicated (find-callers/find-callees/deps appeared
    twice with a dangling "see below" reference), grouped by lifecycle
    (registry → orientation → lookup → reading → search → edits → lifecycle),
    one entry per command with full options verified against `--help`.
  - previously undocumented flags now listed: `index-more`,
    `--include`/`--priority`, `--fresh` (plus a global-flags line),
    `--head`/`--include-untracked`/`--substring` (changed-symbols),
    `--mode`/`--rerank`/`--context-lines` (semantic-search),
    `--relationship` values, dev-only `eval`/`eval-compare`.
  - `--type` documented as an alias of `--symbol-type` on find-symbol.
  - the find-symbol-only `--name alias` section replaced by a
    project-selection section covering every project-scoped command.
  - canonical index-state list (idle/indexing/never-indexed/needs-reindex/
    error) with meanings and agent actions.
  - environment-variable list completed against the config module
    (EMBED_BATCH, UPSERT_BATCH, MAX_FILE_BYTES, CHUNK_MAX_CHARS,
    PARANOID_HASH, VERBOSE; EMBED_CACHE default ON) with placeholder-default
    warning for OLLAMA_URL/QDRANT_URL.
  - front matter: trigger-oriented description; version aligned with
    pyproject (0.1.0); unsourced "855 B vs 2.2 KB measured" claim removed.
- **README.md** (5690f46): rewritten as a short overview linked to
  skill/SKILL.md; CLI↔MCP parity table (replacing a false 1:1 claim;
  overview/index-more/doctor/watch/unwatch/eval* labeled CLI-only);
  canonical state table; schema history (current schema v5); updated
  staleness/fingerprint/filtering text; new troubleshooting, platform
  support, security/privacy, limitations, and performance sections; MCP
  client config kept with the installed-tool variant and stdout note; stale
  "Python 3.11" claim removed; dev section rewritten with real test
  commands; drift-prone tool-count number removed (0896d0c).
- **pyproject.toml / metadata** (18c21f5): description unified between the
  GitHub About and pyproject; Python-support mismatch of issue #4 settled
  (requires-python `>=3.12,<3.13` since 0fd9604; README no longer claims
  3.11) — issue #4 closed.
- **tests/test_docs.py** (new): offline drift tests — command/option coverage
  against the typer/click registry, duplicate-command detection, env-var
  coverage (code ↔ docs, both directions), MCP parity-table check, Python
  version consistency (pyproject ↔ README ↔ .python-version), and
  link/anchor + dangling-"see below" checks.