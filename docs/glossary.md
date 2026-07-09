# Glossary

!!! abstract "How to use this page"
    Every term used across these docs, each with a one-line plain-language
    definition and (where useful) the code you'll see it as. Skim it once; return
    whenever a word trips you up.

---

## Core API concepts

**Document** — the drawing currently open on screen.
`doc = Application.DocumentManager.MdiActiveDocument`

**Civil Document (`civdoc`)** — the "Civil 3D brain" of the same drawing; knows
about alignments, surfaces, pipe networks.
`civdoc = CivilApplication.ActiveDocument`

**Database (`db`)** — the filing cabinet inside a document where every object lives.
`db = doc.Database`

**ObjectId** — a lightweight *ticket* (like a coat-check tag) that refers to a
database object. You carry the ticket; you fetch the real object only inside a
transaction. `pipe_id`, `aln_id`.

**`ObjectId.Null`** — the "empty ticket": means *"no object."* Used as a polite
"nothing here" instead of crashing.

**Transaction** — the safe workbench where you fetch (`GetObject`) and edit objects.
All-or-nothing: `Commit()` to keep changes, dispose to discard.

**Commit** — inking in your pencilled changes so they become permanent.
`tr.Commit()`

**Document Lock** — a "do not disturb" sign that lets your Dynamo-thread code write
to the drawing safely. `doc.LockDocument()`

**Model Space** — the main drawing area where entities (polylines, profile views)
are placed. `SymbolUtilityServices.GetBlockModelSpaceId(db)`

**`OpenMode.ForRead` / `ForWrite`** — how you fetch an object: to *look* (read) or to
*change* (write).

**`UpgradeOpen()`** — promote an object opened for read to write mode when you decide
you need to change it.

**`AddNewlyCreatedDBObject`** — registers an object you created in code with the
transaction; mandatory or the object is orphaned.

---

## Civil 3D objects

**Pipe Network** — a collection of pipes and structures (a sewer, a water main).
Gravity networks and pressure networks have different APIs.

**Structure** — a node in a gravity network: a manhole, inspection chamber, catch
basin. Has a `Position`.

**Inspection Chamber (IC) / Manhole (MH)** — access structures on a sewer; in code,
identified by a name **prefix** like `"IC-"`.

**Pipe** — a segment connecting two structures (gravity) or between fittings
(pressure). Has `StartPoint`/`EndPoint`.

**Alignment** — a path (usually 2-D) through the drawing; the *ruler* along which
profiles and profile views are built.

**Station / Offset** — the alignment's coordinate system: **station** = distance
*along* the alignment; **offset** = distance *sideways* from its centreline.

**Profile** — an elevation line along an alignment (e.g. existing ground draped over
the path). `Profile.CreateFromSurface(...)`

**Surface** — a 3-D terrain model (TIN); the source for a ground profile.

**Profile View** — the side-on *drawing frame* that displays profiles and pipes
along an alignment. `ProfileView.Create(...)`

**Profile View Part** — the entity created when a pipe/structure is added to a
profile view; its ObjectId is needed to attach crossing labels. Civil 3D 2025+.

**Band / Band Set** — the data table(s) drawn above/below a profile view (invert
levels, sizes, chainages). Connected to data sources via `set_band_inputs`.

**Crossing** — another network's pipe that *crosses* the subject alignment in plan
(as opposed to running alongside it).

**Pressure Network / Extension** — the optional pressure-pipes module; its styles
and networks live on `CivilDocumentPressurePipesExtension`, not on `civdoc`.

---

## Styles

**Style** — a "how it looks" recipe (line weight, color, symbols). Picked by name
from a collection; created by the template, not by code.

**Label Style** — a style specifically for annotation text; lives in a deeply-nested,
version-varying collection tree.

**Label Set** — a named bundle of label styles applied together (e.g. an alignment's
station/geometry labels).

**Style Collection** — the "wardrobe" holding styles of one kind, e.g.
`civdoc.Styles.AlignmentStyles`. Enumerated via `ToObjectIds()`.

---

## Dynamo & Python bridge

**Dynamo** — Autodesk's visual programming environment; Python nodes run scripts
inside it.

**Python Node** — a Dynamo node containing a Python script, with inputs `IN` and
output `OUT`.

**`IN`** — the list of values wired into the node's input ports (`IN[0]`, `IN[1]`…).

**`OUT`** — the value the node returns; set on the last line, usually the `results`
dict.

**`clr`** — the Common Language Runtime bridge that lets Python use .NET libraries.
`import clr`

**`clr.AddReference("...")`** — makes a .NET assembly (DLL) importable, like a `pip
install` for .NET.

**`out double` / dummy doubles** — C# methods such as `StationOffset` write answers
into `out` parameters. Under **pythonnet / CPython 3** (no `clr.Reference`), pass
dummy `0.0` doubles for each `out` slot and unpack the return tuple, e.g.
`_, st, off = aln.StationOffset(x, y, st, off)`.

**IronPython 2 / CPython 3** — the two Python engines Dynamo can use. They differ in
syntax and some behaviours; don't mix code between them.

**Assembly** — a compiled .NET library (`.dll`): `AcMgd`, `AcDbMgd`, `AeccDbMgd`,
`AeccPressurePipesMgd`.

---

## Programming patterns (as used here)

**Feature flag (`HAS_*`)** — a boolean recording whether an optional capability
loaded, so code can guard version/module-specific paths. `HAS_PRESSURE`.

**Capability detection** — trying to load something and remembering success/failure
rather than assuming it's present.

**Safe input reader** — a helper (`_opt_str/_int/_float`) that reads a Dynamo wire or
returns a default. Never crashes on empty/missing/wrong-type input.

**Graceful degradation** — continuing with a sensible default + warning instead of
crashing when something non-fatal is missing.

**Adjacency list / connectivity map** — a dict mapping each structure to its
connected pipes, built once for O(1) lookups.

**Find-or-first** — resolve a named style, or fall back to the first available with a
warning.

**Path-list resolution** — trying a priority-ordered list of candidate API paths and
using the first that works.

**Duck typing** — checking whether an object *has* a method (`hasattr`) rather than
its exact type, for version resilience.

**Duplicate-name retry** — creating an object, catching the "duplicate" error, and
retrying with a suffix.

**Diagnostics dict (`results`)** — a single structured output holding counts,
warnings, skipped items, and available names, for inspection in a Watch node.

---

## Geometry

**Point2d / Point3d** — 2-D / 3-D point objects with `.X`, `.Y` (and `.Z`).

**Polyline** — a multi-vertex line; here, a 2-vertex "seed" that Civil 3D converts
into an alignment.

**Segment intersection (2-D)** — pure-math test for whether two line segments cross
in plan; parameters `t` (along segment A) and `u` (along segment B).

**Crossing angle** — the acute angle between the alignment and a candidate pipe;
small = parallel, large = true crossing.

**Tolerance (`ON_ALIGN_TOL`)** — the sideways distance within which a pipe is
considered to run *on/alongside* the alignment rather than crossing it.

---

!!! tip "Missing a term?"
    If you hit a word that isn't here, it's probably defined in the chapter that uses
    it — check the [Walkthrough](walkthrough/a-imports.md). Suggest additions so this
    glossary grows with the team.
