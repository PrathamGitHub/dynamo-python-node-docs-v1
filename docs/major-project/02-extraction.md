# Stage 2 — Extraction: networks → flat rows → DuckDB

!!! abstract "Goal of this stage"
    Build `helpers_network`: read a gravity network (and the other gravity and
    pressure networks that might cross it) inside the open transaction, and flatten
    every pipe and structure into **plain primitive rows**. Then load those rows
    into DuckDB with the fast Arrow path. You end with queryable `pipes` and
    `structures` tables — the foundation every later stage reads from.

    This is the first stage with runnable code. It establishes the **boundary
    contract** that makes the whole project both correct and fast.

---

## Why extract at all? Why not just loop the API?

The reference loops the Civil 3D API directly, inside every IC iteration, every
time it needs to know something. That's the natural first instinct and it's
wrong at scale, for three reasons:

1. **You pay the transaction/marshalling cost repeatedly.** Every `GetObject` +
   attribute read crosses the Python↔.NET boundary. Doing it once and caching
   primitives is far cheaper than re-reading inside nested loops.
2. **You can't ask set-based questions.** "Every inter-network pipe pair that
   crosses" is a join. You can't express a join by walking objects one at a time
   without reinventing a query engine badly.
3. **Live objects are fragile carriers.** A Civil 3D object is only valid inside
   its transaction. The moment you want to persist, compare, or analyse *across*
   objects, you want inert primitives, not live handles.

!!! success "The boundary contract (memorise this)"
    **Only primitives cross the Civil 3D ↔ DuckDB boundary.**

    - Primary key = the AutoCAD **Handle** (a hex string) — stable, serialisable,
      survives across transactions and sessions.
    - Geometry travels as **2D WKT** (`LINESTRING` for pipes, `POINT` for
      structures).
    - Elevation (**z**) travels as separate attribute columns — because DuckDB's
      spatial extension is planar/2D (this becomes important in stage 3).

    Live `Pipe`/`Structure` objects **never** leave the extract step. Everything
    downstream works on the flat rows and hands back **handles**, which the write
    phase re-resolves.

---

## The row shape every object collapses to

One flat schema, so every analysis reads the same tables:

```python
# a pipe row
{
  "handle": "2A7",            # AutoCAD Handle, hex string — the PK
  "name": "Pipe - (1)",
  "network": "SW-Main",       # which network it belongs to
  "role": "main" | "gravity_cross" | "pressure_cross",   # extraction scope tag
  "start_handle": "2A3", "end_handle": "2A5",   # connected structures (may be None)
  "start_x": .., "start_y": .., "start_z": ..,  # invert z (verify — see stage 9)
  "end_x": ..,   "end_y": ..,   "end_z": ..,
  "diameter": 0.300, "slope": 0.004,
  "wkt": "LINESTRING(x1 y1, x2 y2)",
}
# a structure row
{
  "handle": "2A3", "name": "IC-1", "part_type": "Inspection Chamber",
  "network": "SW-Main", "x": .., "y": .., "rim_z": .., "sump_z": ..,
  "wkt": "POINT(x y)",
}
```

The `role` tag is new versus the DuckDB cookbook recipe: this project extracts
**three groups** — the main gravity network we're profiling, other gravity
networks that may cross, and pressure networks that may cross. Tagging the role
at extraction time means the crossing query can say "main vs. everything else"
without re-deriving membership.

---

## `helpers_geometry` — points, WKT, and the verified out-param helpers

Geometry primitives first, because extraction depends on them. Note the
out-parameter helpers use the **verified hybrid convention** from the foundation
section — *not* `clr.Reference`, which does not exist on this build.

```python
# automations/helpers_geometry.py
"""Geometry primitives: robust point reads, WKT emitters, and the out-parameter
helpers for station/offset. Public API — imported across the project."""


def try_get_point3d(obj):
    """Extract a Point3d by probing the attribute names different Civil 3D types
    use for their location. Returns None if none yield a valid 3-D point.
      Structure -> Position | BlockRef -> InsertionPoint | generic -> Location/Point
    """
    for attr in ("Position", "Location", "InsertionPoint", "Point"):
        if hasattr(obj, attr):
            try:
                pt = getattr(obj, attr)
                if hasattr(pt, "X") and hasattr(pt, "Y") and hasattr(pt, "Z"):
                    return pt
            except Exception:
                pass
    return None


def pt_xyz(p):
    """Point3d -> (x, y, z) floats; (None, None, None) on failure."""
    try:
        return float(p.X), float(p.Y), float(p.Z)
    except Exception:
        return (None, None, None)


def wkt_line(x1, y1, x2, y2):
    if None in (x1, y1, x2, y2):
        return None
    return f"LINESTRING({x1} {y1}, {x2} {y2})"


def wkt_point(x, y):
    if None in (x, y):
        return None
    return f"POINT({x} {y})"


def station_offset(aln, x, y):
    """(x, y) -> (station, offset) on `aln`. Hybrid out-param convention:
    pass dummy Doubles AND unpack from the return tuple (leading None = void).
    Raises PointNotOnEntityException if the point cannot be projected."""
    _, st, off = aln.StationOffset(x, y, 0.0, 0.0)
    return float(st), float(off)


def point_location(aln, st, off=0.0):
    """(station, offset) -> (easting, northing) on `aln`. Hybrid convention."""
    _, x, y = aln.PointLocation(st, off, 0.0, 0.0)
    return float(x), float(y)
```

!!! note "Why `try_get_point3d` probes four names"
    Civil 3D is inconsistent about where an object keeps its location: a
    `Structure` uses `Position`, a block reference `InsertionPoint`, other objects
    `Location` or `Point`. Probing in order — and validating the result actually
    has `X/Y/Z` — is more robust than assuming one name. This pattern is Recipe 4
    from the cookbook, kept because it genuinely earns its place here.

---

## `helpers_network` — the extractor

Now the extraction itself. Two details from the reference are worth keeping and
one worth fixing.

```python
# automations/helpers_network.py
"""Extract gravity + pressure networks into flat primitive rows.
Uses the OPEN transaction from context — never opens its own."""
from Autodesk.AutoCAD.DatabaseServices import OpenMode
from automations.helpers_geometry import try_get_point3d, pt_xyz, wkt_line, wkt_point


def get_member(obj, name, cast=None, default=None, missing=None):
    """Defensive attribute read. Records failed names in `missing` (a set) so the
    extractor self-documents which member spellings are wrong on this build."""
    try:
        val = getattr(obj, name)
    except Exception:
        if missing is not None:
            missing.add(name)
        return default
    if cast is not None and val is not None:
        try:
            return cast(val)
        except Exception:
            return default
    return val


def get_pipe_end_structure_handles(tr, pipe_obj, missing=None):
    """(start_handle, end_handle) for a Pipe. Probes BOTH the direct
    *StructureId properties AND the older *Structure.ObjectId form for
    cross-version robustness. Returns (None, None) on failure."""
    search_pairs = (("StartStructureId", "EndStructureId"),
                 ("StartStructure", "EndStructure"))
    for a, b in search_pairs:
        if hasattr(pipe_obj, a) and hasattr(pipe_obj, b):
            try:
                sv = getattr(pipe_obj, a)
                ev = getattr(pipe_obj, b)
                if hasattr(sv, "ObjectId"):
                    sv = sv.ObjectId     # unwrap object-reference form
                if hasattr(ev, "ObjectId"):
                    ev = ev.ObjectId
                sh = _handle_of(tr, sv)
                eh = _handle_of(tr, ev)
                return sh, eh
            except Exception:
                pass
    if missing is not None:
        missing.add(", ".join(f"{a} and/or {b}" for a, b in search_pairs))
    return None, None


def _handle_of(tr, oid):
    try:
        if oid is not None and not oid.IsNull:
            return tr.GetObject(oid, OpenMode.ForRead).Handle.ToString()
    except Exception:
        pass
    return None
```

!!! tip "The dual-property probe is real version defensiveness"
    `get_pipe_end_structure_handles` tries `StartStructureId` *and*
    `StartStructure.ObjectId`. Older API surfaces exposed the connected structure
    as an object with an `.ObjectId` sub-property; newer ones expose the id
    directly. Probing both — and unwrapping — means the extractor doesn't break if
    your build differs. This pattern came straight from the reference and is one of
    the things it got right.

### The extraction driver

```python
def extract_pipes(tr, net, network_name, role, missing, skipped):
    """Flatten every pipe in `net` into rows. `role` tags the scope group."""
    rows, conns = [], []
    for pid in net.GetPipeIds():
        try:
            p = tr.GetObject(pid, OpenMode.ForRead)
            handle = p.Handle.ToString()
            sx, sy, sz = pt_xyz(get_member(p, "StartPoint", missing=missing))
            ex, ey, ez = pt_xyz(get_member(p, "EndPoint", missing=missing))
            sh, eh = get_pipe_end_structure_handles(tr, p, missing)
            dia = get_member(p, "InnerDiameterOrWidth", float, None, missing)
            if dia is None:
                dia = get_member(p, "InnerDiameter", float, None, missing)
            rows.append({
                "handle": handle, "name": get_member(p, "Name", str, None, missing),
                "network": network_name, "role": role,
                "start_handle": sh, "end_handle": eh,
                "start_x": sx, "start_y": sy, "start_z": sz,
                "end_x": ex, "end_y": ey, "end_z": ez,
                "diameter": dia, "slope": get_member(p, "Slope", float, None, missing),
                "wkt": wkt_line(sx, sy, ex, ey),
            })
            for h, et in ((sh, "start"), (eh, "end")):
                if h:
                    conns.append({"pipe_handle": handle, "structure_handle": h, "end_type": et})
        except Exception as e:
            skipped.append({"pipe": str(pid), "reason": str(e)})
    return rows, conns


def extract_structures(tr, net, network_name, missing, skipped):
    rows = []
    for sid in net.GetStructureIds():
        try:
            s = tr.GetObject(sid, OpenMode.ForRead)
            x, y, z = pt_xyz(try_get_point3d(s))
            rows.append({
                "handle": s.Handle.ToString(),
                "name": get_member(s, "Name", str, None, missing),
                "part_type": str(get_member(s, "PartType", default="", missing=missing)),
                "network": network_name, "x": x, "y": y,
                "rim_z": get_member(s, "RimElevation", float, None, missing),
                "sump_z": get_member(s, "SumpElevation", float, None, missing),
                "wkt": wkt_point(x, y),
            })
        except Exception as e:
            skipped.append({"structure": str(sid), "reason": str(e)})
    return rows
```

!!! danger "Reference trap — the silent-skip"
    The reference's crossing scan wraps whole-network loops in one broad
    `try/except` that appends a single warning and moves on. If pipe #37 of 200
    throws, you lose *all* remaining pipes in that network with one vague message.
    Our extractor wraps **each item** — a bad pipe becomes one entry in `skipped`
    with its handle and reason, and the loop continues. Per-item isolation, never
    per-loop transactions (see the transaction-granularity discussion). You find
    out *which* object failed and *why*, and still get the other 199.

---

## Loading into DuckDB — the fast Arrow path

The naïve `executemany` insert runs DuckDB's row-by-row appender and crawls at
~3 kb/s. DuckDB is columnar; it wants **one bulk, columnar scan**. With `pyarrow`
present we build typed Arrow columns and do a single `INSERT ... SELECT`.

```python
# automations/duckdb_engine.py  (load portion)
import duckdb

_PIPE_COLS = [
    ("handle","TEXT"),("name","TEXT"),("network","TEXT"),("role","TEXT"),
    ("start_handle","TEXT"),("end_handle","TEXT"),
    ("start_x","DOUBLE"),("start_y","DOUBLE"),("start_z","DOUBLE"),
    ("end_x","DOUBLE"),("end_y","DOUBLE"),("end_z","DOUBLE"),
    ("diameter","DOUBLE"),("slope","DOUBLE"),("wkt","TEXT"),
]
_STRUCT_COLS = [
    ("handle","TEXT"),("name","TEXT"),("part_type","TEXT"),("network","TEXT"),
    ("x","DOUBLE"),("y","DOUBLE"),("rim_z","DOUBLE"),("sump_z","DOUBLE"),("wkt","TEXT"),
]


def connect(db_path=None):
    con = duckdb.connect(db_path if db_path else ":memory:")   # file=EDA, None=ETL
    con.execute("INSTALL spatial;"); con.execute("LOAD spatial;")
    return con


def _arrow_type_map():
    import pyarrow as pa
    return {"TEXT": pa.string(), "DOUBLE": pa.float64(), "INTEGER": pa.int64()}


def create_and_insert(con, table, cols, rows):
    """Create `table` from `cols` and bulk-insert `rows` via a typed Arrow scan."""
    ddl = ", ".join(f'"{c}" {t}' for c, t in cols)
    con.execute(f'CREATE OR REPLACE TABLE "{table}" ({ddl});')
    if not rows:
        return
    import pyarrow as pa
    tmap = _arrow_type_map()
    names = [c for c, _ in cols]
    # typed per-column build so an all-None column keeps its DOUBLE/TEXT type
    # rather than collapsing to arrow 'null' (which breaks INSERT..SELECT).
    arrays = [pa.array([r.get(n) for r in rows], type=tmap.get(t, pa.string()))
              for n, t in cols]
    con.register("_ins", pa.table(arrays, names=names))
    try:
        con.execute(f'INSERT INTO "{table}" SELECT * FROM _ins')
    finally:
        con.unregister("_ins")


def load_networks(con, pipes, structures):
    create_and_insert(con, "pipes_raw", _PIPE_COLS, pipes)
    create_and_insert(con, "structures_raw", _STRUCT_COLS, structures)
    # build geometry columns once, straight off the raw tables
    con.execute("CREATE OR REPLACE TABLE pipes AS "
                "SELECT *, ST_GeomFromText(wkt) AS geom FROM pipes_raw WHERE wkt IS NOT NULL;")
    con.execute("CREATE OR REPLACE TABLE structures AS "
                "SELECT *, ST_GeomFromText(wkt) AS geom FROM structures_raw WHERE wkt IS NOT NULL;")
```

!!! danger "The all-None column trap (subtle, will bite your first run)"
    Do **not** build the Arrow table with `pa.table({col: values})` — that
    *infers* types, and a column that is entirely `None` (very likely for
    `diameter`/`slope`/`rim_z` on the **first probe run**, before member names are
    confirmed) infers Arrow `null`, which then fails to insert into a declared
    `DOUBLE` column. Build each column with its **declared type** (`_arrow_type_map`)
    so the type is fixed regardless of the values. This was verified against
    pyarrow directly; naive inference gave `null`, typed build gave `double`.

---

## The runnable stage-2 module

Ties it together as a `run(context)` you can load right now. It doesn't create
anything in the drawing yet — it extracts, loads, and reports counts, so you can
confirm the pipeline before building on it.

```python
# automations/project_ic_profiles.py  (stage-2 checkpoint)
import traceback
from Autodesk.AutoCAD.DatabaseServices import OpenMode
from automations import helpers_network as net
from automations import duckdb_engine as duck


def run(context):
    civdoc, tr, IN = context["civdoc"], context["tr"], context["IN"]
    data = {"Warnings": [], "Skipped": [], "Items": []}
    missing = set()
    try:
        main_name = IN[0] if len(IN) > 0 and IN[0] else None
        pipes, structs, conns = [], [], []
        for nid in civdoc.GetPipeNetworkIds():
            n = tr.GetObject(nid, OpenMode.ForRead)
            nname = net.get_member(n, "Name", str, "", missing)
            role = "main" if (main_name and nname == main_name) else "gravity_cross"
            pr, cr = net.extract_pipes(tr, n, nname, role, missing, data["Skipped"])
            pipes += pr; conns += cr
            structs += net.extract_structures(tr, n, nname, missing, data["Skipped"])

        con = duck.connect(None)                 # in-memory for the checkpoint
        duck.load_networks(con, pipes, structs)

        if missing:
            data["Warnings"].append("Unresolved members (pin spellings): "
                                    + ", ".join(sorted(missing)))
        data["Counts"] = {"pipes": len(pipes), "structures": len(structs),
                          "networks": len(list(civdoc.GetPipeNetworkIds()))}
        # prove the tables are queryable
        data["Items"] = con.execute(
            "SELECT network, count(*) n FROM pipes GROUP BY network ORDER BY 2 DESC"
        ).fetchall()
    except Exception as e:
        data["Warnings"].append(str(e)); data["Warnings"].append(traceback.format_exc())
    return data
```

!!! success "First-run checkpoint"
    Load this through the loader node with `IN[0]` = your main gravity network
    name. In the Watch node you should see `Counts` (pipe/structure/network totals)
    and `Items` (per-network pipe counts from a real DuckDB `GROUP BY`). If
    `Warnings` lists unresolved members, that's your cue to pin the real member
    spellings for your build — the extractor told you exactly which. Nothing was
    written to the drawing; this is a safe, read-only checkpoint.

!!! note "Pressure networks — deferred by one stage"
    We've extracted gravity networks here. Pressure networks (`PressurePipe`) use a
    separate API family and a separate `role` tag (`pressure_cross`); we add that
    extractor in stage 3 alongside the crossing query, so both crossing sources
    land in the same `pipes` table with different roles.

Next: **[Crossing detection done right](03-crossing-detection.md)** — the
per-segment, angle-guarded, z-classified crossings **table**, and a head-to-head
contrast with the reference's single-segment test.
