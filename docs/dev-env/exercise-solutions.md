# Exercise Solutions

!!! warning "Try first — peek only when stuck"
    Every solution below is inside a **collapsed** box (`???`). Attempt the exercise,
    compare after. These are *reference* solutions — not the only correct answer.
    Coordinates and names assume a scratch drawing; adjust to yours.

!!! note "All solutions are `run(IN)` module bodies"
    Each drops into its own `.py` and runs via the
    [thin loader node](dynamo-node-workflow.md#step-2--the-thin-loader-node-paste-this-once-rarely-change-it).
    The standard imports (shown once here) are assumed at the top of each file.

```python
# standard header used by all solutions
import clr, System
clr.AddReference("AcCoreMgd"); clr.AddReference("AcDbMgd"); clr.AddReference("AcMgd")
clr.AddReference("AeccDbMgd")
from Autodesk.AutoCAD.ApplicationServices.Core import Application
from Autodesk.AutoCAD.DatabaseServices import (
    OpenMode, ObjectId, Polyline, SymbolUtilityServices, LayerTableRecord, LayerTable)
from Autodesk.AutoCAD.Geometry import Point2d, Point3d
from Autodesk.Civil.ApplicationServices import CivilApplication
from Autodesk.Civil.DatabaseServices import Alignment, PolylineOptions
```

---

## Solution 0 — Hello loop

??? success "Show solution"
    ```python
    def run(IN):
        return {"message": "hello from Cursor v2", "inputs_seen": len(IN)}
    ```
    Editing `"v2"` → `"v3"`, saving, and re-running should change the Watch output —
    proving `importlib.reload` picks up disk edits.

---

## Solution 1 — Read one object

??? success "Show solution"
    ```python
    def run(IN):
        doc    = Application.DocumentManager.MdiActiveDocument
        db     = doc.Database
        civdoc = CivilApplication.ActiveDocument
        results = {"Warnings": []}

        lock = doc.LockDocument()
        try:
            tr = db.TransactionManager.StartTransaction()
            try:
                net_ids  = list(civdoc.GetPipeNetworkIds())
                aln_ids  = list(civdoc.GetAlignmentIds())
                names = []
                for oid in net_ids:
                    net = tr.GetObject(oid, OpenMode.ForRead)
                    names.append(getattr(net, "Name", "<unnamed>"))
                results.update({
                    "Drawing": db.Filename,
                    "NetworkCount": len(net_ids),
                    "AlignmentCount": len(aln_ids),
                    "NetworkNames": sorted(names),   # stretch goal
                })
                tr.Commit()
            finally:
                tr.Dispose()
        finally:
            lock.Dispose()
        return results
    ```

---

## Solution 2 — Safe inputs

??? success "Show solution"
    ```python
    def _opt_str(IN, i, default=""):
        try:
            if len(IN) > i and IN[i] is not None:
                s = str(IN[i]).strip()
                return s if s else default
        except Exception: pass
        return default

    def _opt_int(IN, i, default):
        try:
            if len(IN) > i and IN[i] is not None:
                return int(IN[i])
        except Exception: pass
        return default

    def _opt_float(IN, i, default):
        try:
            if len(IN) > i and IN[i] is not None:
                return float(IN[i])
        except Exception: pass
        return default

    def normalize_name_list(x):
        if x is None: return []
        if isinstance(x, str): x = [x]
        seen, out = set(), []
        for item in x:
            if item is None: continue
            s = str(item).strip()
            if s and s.lower() not in seen:
                seen.add(s.lower()); out.append(s)
        return out

    def run(IN):
        w = []
        name = _opt_str(IN, 0, "")
        if not name: w.append('Network name missing; default "".')
        prefix = _opt_str(IN, 1, "IC-")
        tol = _opt_float(IN, 2, 0.15)
        cross = normalize_name_list(IN[3] if len(IN) > 3 else None)  # stretch
        return {"Network": name, "Prefix": prefix, "Tol": tol,
                "CrossNets": cross, "Warnings": w}
    ```

---

## Solution 3 — Write a layer + polyline

??? success "Show solution"
    ```python
    def _ensure_layer(tr, db, name):
        lt = tr.GetObject(db.LayerTableId, OpenMode.ForRead)
        if lt.Has(name):
            return lt[name]
        lt = tr.GetObject(db.LayerTableId, OpenMode.ForWrite)
        rec = LayerTableRecord(); rec.Name = name
        lid = lt.Add(rec); tr.AddNewlyCreatedDBObject(rec, True)
        return lid

    def run(IN):
        doc = Application.DocumentManager.MdiActiveDocument
        db  = doc.Database
        results = {"Warnings": []}
        lock = doc.LockDocument()
        try:
            tr = db.TransactionManager.StartTransaction()
            try:
                _ensure_layer(tr, db, "DEV-SCRATCH")
                ms_id = SymbolUtilityServices.GetBlockModelSpaceId(db)
                ms = tr.GetObject(ms_id, OpenMode.ForWrite)

                pl = Polyline()
                pl.AddVertexAt(0, Point2d(0.0, 0.0), 0.0, 0.0, 0.0)
                pl.AddVertexAt(1, Point2d(50.0, 20.0), 0.0, 0.0, 0.0)
                pl.Layer = "DEV-SCRATCH"

                pid = ms.AppendEntity(pl)
                tr.AddNewlyCreatedDBObject(pl, True)   # <-- the mandatory pairing
                tr.Commit()
                results["Created"] = str(pid)
            finally:
                tr.Dispose()
        finally:
            lock.Dispose()
        return results
    ```
    Omitting `AddNewlyCreatedDBObject` (the deliberate-failure step) leaves the
    polyline unregistered — the run errors or the object is orphaned.

---

## Solution 4 — Update (get → modify → set)

??? success "Show solution"
    ```python
    def run(IN):
        doc = Application.DocumentManager.MdiActiveDocument
        db  = doc.Database
        results = {"Warnings": []}
        lock = doc.LockDocument()
        try:
            tr = db.TransactionManager.StartTransaction()
            try:
                ms = tr.GetObject(SymbolUtilityServices.GetBlockModelSpaceId(db),
                                  OpenMode.ForRead)
                target = None
                for oid in ms:
                    ent = tr.GetObject(oid, OpenMode.ForRead)
                    if getattr(ent, "Layer", "") == "DEV-SCRATCH" and isinstance(ent, Polyline):
                        target = ent; break
                if target is None:
                    results["Warnings"].append("No DEV-SCRATCH polyline found; run Ex 3 first.")
                    tr.Commit(); return results

                old = (target.GetPoint2dAt(1).X, target.GetPoint2dAt(1).Y)
                target.UpgradeOpen()                    # ForRead -> ForWrite
                target.SetPointAt(1, Point2d(80.0, 40.0))
                target.Layer = "DEV-SCRATCH-2"          # (create the layer or reuse)
                new = (target.GetPoint2dAt(1).X, target.GetPoint2dAt(1).Y)
                tr.Commit()
                results.update({"Old": old, "New": new})
            finally:
                tr.Dispose()
        finally:
            lock.Dispose()
        return results
    ```

---

## Solution 5 — Delete / clean up (idempotent)

??? success "Show solution"
    ```python
    def cleanup(tr, db, layer_name):
        ms = tr.GetObject(SymbolUtilityServices.GetBlockModelSpaceId(db), OpenMode.ForRead)
        ids = [oid for oid in ms
               if getattr(tr.GetObject(oid, OpenMode.ForRead), "Layer", "") == layer_name]
        n = 0
        for oid in ids:                                 # second pass: safe to erase
            ent = tr.GetObject(oid, OpenMode.ForWrite)
            ent.Erase(); n += 1
        return n

    def run(IN):
        doc = Application.DocumentManager.MdiActiveDocument
        db  = doc.Database
        lock = doc.LockDocument()
        try:
            tr = db.TransactionManager.StartTransaction()
            try:
                n = cleanup(tr, db, "DEV-SCRATCH")
                tr.Commit()
                return {"Erased": n, "Warnings": []}
            finally:
                tr.Dispose()
        finally:
            lock.Dispose()
    ```
    Second run returns `Erased: 0` — idempotent, no crash.

---

## Solution 6 — Resolve style (fallback + path-list stretch)

??? success "Show solution"
    ```python
    def get_style_id_or_first(coll, desired, warnings, kind):
        try: ids = list(coll.ToObjectIds())
        except Exception: ids = []
        if not ids:
            raise Exception(f"No {kind} in drawing. Import styles from template.")
        if desired:
            try:
                if coll.Contains(desired):
                    return coll.get_Item(desired), desired
            except Exception: pass
            warnings.append(f'{kind} "{desired}" not found; using first available.')
        return ids[0], "<FirstAvailable>"

    def run(IN):
        civdoc = CivilApplication.ActiveDocument
        w = []
        _, a = get_style_id_or_first(civdoc.Styles.AlignmentStyles,
                                     _opt_str(IN, 0, ""), w, "Alignment Style")
        _, b = get_style_id_or_first(civdoc.Styles.ProfileViewStyles,
                                     _opt_str(IN, 1, "___bogus___"), w, "Profile View Style")
        return {"AlignmentStyle": a, "ProfileViewStyle": b, "Warnings": w}
    ```
    (`_opt_str` from Solution 2.) Path-list stretch: see
    [Chunk D](../walkthrough/d-styles.md#the-improved-pattern-path-list-resolution).

---

## Solution 7 — Out-parameters

??? success "Show solution"
    ```python
    def station_offset(aln, x, y):
        st  = clr.Reference[System.Double](0.0)
        off = clr.Reference[System.Double](0.0)
        aln.StationOffset(x, y, st, off)            # fills the boxes
        return float(st.Value), float(off.Value)

    def endpoint_on_alignment(aln, x, y, tol):      # stretch
        try:
            _, off = station_offset(aln, x, y)
            return abs(off) <= tol
        except Exception:
            return False

    def run(IN):
        civdoc = CivilApplication.ActiveDocument
        doc = Application.DocumentManager.MdiActiveDocument
        db  = doc.Database
        results = {"Warnings": []}
        lock = doc.LockDocument()
        try:
            tr = db.TransactionManager.StartTransaction()
            try:
                aln_ids = list(civdoc.GetAlignmentIds())
                if not aln_ids:
                    results["Warnings"].append("No alignment in drawing.")
                    tr.Commit(); return results
                aln = tr.GetObject(aln_ids[0], OpenMode.ForRead)

                x = _opt_float(IN, 0, aln.StartingStation)   # or a known point
                y = _opt_float(IN, 1, 0.0)
                st, off = station_offset(aln, x, y)
                results.update({"Station": round(st, 3), "Offset": round(off, 3),
                                "OnAlign": endpoint_on_alignment(aln, x, y, 0.15)})
                tr.Commit()
            finally:
                tr.Dispose()
        finally:
            lock.Dispose()
        return results
    ```
    The trap to feel first: calling `aln.StationOffset(x, y)` (no boxes) returns
    nothing and raises nothing — the answers had nowhere to go.

---

## Solution 8 — Crossing detection (buggy → fixed)

??? success "Show solution"
    ```python
    import math
    MIN_CROSSING_ANGLE_DEG = 20.0
    ENDPOINT_PARAM_GUARD   = 0.02

    def _segment_cross_params(x1,y1,x2,y2,x3,y3,x4,y4):
        dx12,dy12 = x2-x1, y2-y1
        dx34,dy34 = x4-x3, y4-y3
        denom = dx12*dy34 - dy12*dx34
        if abs(denom) < 1e-10: return None            # parallel
        dx13,dy13 = x3-x1, y3-y1
        t = (dx13*dy34 - dy13*dx34)/denom
        u = (dx13*dy12 - dy13*dx12)/denom
        if 0.0 <= t <= 1.0 and 0.0 <= u <= 1.0:
            return t, u, x1+t*dx12, y1+t*dy12
        return None

    def _angle_deg(ax,ay,bx,by,cx,cy,dx,dy):
        v1=(bx-ax,by-ay); v2=(dx-cx,dy-cy)
        n1=math.hypot(*v1); n2=math.hypot(*v2)
        if n1<1e-9 or n2<1e-9: return 0.0
        c=(v1[0]*v2[0]+v1[1]*v2[1])/(n1*n2)
        return math.degrees(math.acos(max(-1.0,min(1.0,abs(c)))))

    # BUGGY version (single condition) — for comparison
    def is_crossing_buggy(ax,ay,bx,by, sx,sy,ex,ey):
        return _segment_cross_params(ax,ay,bx,by, sx,sy,ex,ey) is not None

    # FIXED version (three questions)
    def is_crossing(ax,ay,bx,by, sx,sy,ex,ey):
        hit = _segment_cross_params(ax,ay,bx,by, sx,sy,ex,ey)
        if hit is None: return False
        t,u,ix,iy = hit
        if u < ENDPOINT_PARAM_GUARD or u > 1.0-ENDPOINT_PARAM_GUARD:
            return False                              # touches only at its own end
        if _angle_deg(ax,ay,bx,by, sx,sy,ex,ey) < MIN_CROSSING_ANGLE_DEG:
            return False                              # shallow graze = alongside
        return True

    def run(IN):
        # alignment along X axis from (0,0) to (100,0)
        A = (0.0,0.0,100.0,0.0)
        cases = {
            "cross_90": (50,-10, 50,10),              # perpendicular  -> True
            "cross_30": (40,-10, 60,10),              # skew           -> True
            "parallel": (10,0.05, 90,0.05),           # alongside      -> False
            "endpoint": (50,0, 50,10),                # touches at u\~0 -> False
        }
        out = {"buggy": {}, "fixed": {}, "Warnings": []}
        for name,(sx,sy,ex,ey) in cases.items():
            out["buggy"][name] = is_crossing_buggy(*A, sx,sy,ex,ey)
            out["fixed"][name] = is_crossing(*A, sx,sy,ex,ey)
        return out
    ```
    Expected: `buggy` mislabels `parallel`/`endpoint` as `True`; `fixed` gives
    `cross_90=True, cross_30=True, parallel=False, endpoint=False`.

---

## Solution 9 — Defensive per-item error handling

??? success "Show solution"
    ```python
    def run(IN):
        doc = Application.DocumentManager.MdiActiveDocument
        db  = doc.Database
        civdoc = CivilApplication.ActiveDocument
        results = {"Processed": 0, "Skipped": [], "Warnings": []}

        lock = doc.LockDocument()
        try:
            tr = db.TransactionManager.StartTransaction()
            try:
                net_ids = list(civdoc.GetPipeNetworkIds())
                if not net_ids:
                    raise Exception("No pipe network in drawing.")   # FATAL -> raise
                net = tr.GetObject(net_ids[0], OpenMode.ForRead)

                for sid in net.GetStructureIds():
                    name = "<unknown>"
                    try:
                        s = tr.GetObject(sid, OpenMode.ForRead)
                        name = getattr(s, "Name", "<unnamed>")
                        pos = s.Position                    # per-item fallible call
                        _ = (pos.X, pos.Y, pos.Z)
                        results["Processed"] += 1
                    except Exception as e:                  # NARROW: per item, per step
                        results["Skipped"].append(
                            f"{name}: read position failed: {e.__class__.__name__}")
                        continue
                tr.Commit()
            finally:
                tr.Dispose()
        finally:
            lock.Dispose()
        total = results["Processed"] + len(results["Skipped"])
        results["Total"] = total
        return results
    ```
    Fatal (no network) → the run stops. Per-item failures → recorded and skipped;
    `Processed + Skipped == Total`.

---

## Solution 10 — Capstone (alignment per IC)

??? success "Show solution"
    ```python
    def _pt_of(tr, sid):
        s = tr.GetObject(sid, OpenMode.ForRead)
        try:
            p = s.Position; return (p.X, p.Y)
        except Exception:
            return None

    def build_unique_name(existing, base):
        if base not in existing:
            existing.add(base); return base
        i = 1
        while f"{base} {i}" in existing: i += 1
        existing.add(f"{base} {i}"); return f"{base} {i}"

    def run(IN):
        doc = Application.DocumentManager.MdiActiveDocument
        db  = doc.Database
        civdoc = CivilApplication.ActiveDocument
        w = []
        net_name = _opt_str(IN, 0, "")
        ic_prefix = _opt_str(IN, 1, "IC-")
        style_name = _opt_str(IN, 2, "")
        test_limit = _opt_int(IN, 3, 0)
        results = {"Created": 0, "Skipped": [], "Warnings": w}

        lock = doc.LockDocument()
        try:
            tr = db.TransactionManager.StartTransaction()
            try:
                # --- setup once ---
                target = None
                for oid in civdoc.GetPipeNetworkIds():
                    n = tr.GetObject(oid, OpenMode.ForRead)
                    if getattr(n, "Name", "") == net_name and hasattr(n, "GetStructureIds"):
                        target = n; break
                if target is None:
                    raise Exception(f'Pipe Network "{net_name}" not found.')   # FATAL

                style_id, resolved = get_style_id_or_first(
                    civdoc.Styles.AlignmentStyles, style_name, w, "Alignment Style")
                results["StyleUsed"] = resolved

                # connectivity map: structure -> [(pipe, start, end)]
                conn = {}
                for pid in target.GetPipeIds():
                    p = tr.GetObject(pid, OpenMode.ForRead)
                    st_id = getattr(p, "StartStructureId", ObjectId.Null)
                    en_id = getattr(p, "EndStructureId", ObjectId.Null)
                    if st_id.IsNull or en_id.IsNull: continue
                    conn.setdefault(st_id, []).append((pid, st_id, en_id))
                    conn.setdefault(en_id, []).append((pid, st_id, en_id))

                ic_ids = [s for s in target.GetStructureIds()
                          if getattr(tr.GetObject(s, OpenMode.ForRead), "Name", "").startswith(ic_prefix)]
                if test_limit > 0: ic_ids = ic_ids[:test_limit]

                ms = tr.GetObject(SymbolUtilityServices.GetBlockModelSpaceId(db), OpenMode.ForWrite)
                existing = set()

                # --- loop ---
                for sid in ic_ids:
                    sname = getattr(tr.GetObject(sid, OpenMode.ForRead), "Name", "IC")
                    pipes = conn.get(sid, [])
                    if not pipes:
                        results["Skipped"].append(f"{sname} (no connected pipe)"); continue
                    pid, st_id, en_id = pipes[0]
                    sp, ep = _pt_of(tr, st_id), _pt_of(tr, en_id)
                    if sp is None or ep is None:
                        results["Skipped"].append(f"{sname} (no coordinates)"); continue
                    try:
                        pl = Polyline()
                        pl.AddVertexAt(0, Point2d(sp[0], sp[1]), 0.0, 0.0, 0.0)
                        pl.AddVertexAt(1, Point2d(ep[0], ep[1]), 0.0, 0.0, 0.0)
                        pl_id = ms.AppendEntity(pl); tr.AddNewlyCreatedDBObject(pl, True)

                        plops = PolylineOptions()
                        plops.PlineId = pl_id
                        plops.AddCurvesBetweenTangents = False
                        plops.EraseExistingEntities = True

                                                aln_name = build_unique_name(existing, f"ALN - {sname}")
                        Alignment.Create(
                            civdoc, plops, aln_name,
                            ObjectId.Null,           # SITE_ID = no site
                            db.LayerZero,            # or a resolved layer id
                            style_id,
                            ObjectId.Null)           # label set (none)
                        results["Created"] += 1
                    except Exception as e:
                        # duplicate-name safety net + any per-item failure
                        results["Skipped"].append(
                            f"{sname}: create failed: {e.__class__.__name__}: {e}")
                        continue

                tr.Commit()
            finally:
                tr.Dispose()
        finally:
            lock.Dispose()

        results["Total"] = results["Created"] + len(results["Skipped"])
        return results
    ```
    **Expected behaviour**
    - New alignments appear, one per processed IC.
    - Re-running produces `ALN - ... (1)`, `(2)` suffixes rather than crashing.
    - `TEST_LIMIT = 3` processes exactly three ICs.
    - A fatal condition (network missing) stops the run; per-item issues are recorded
      in `Skipped` and the run continues.

    **Stretch A** (profile view per alignment): capture the `aln_id` returned by
    `Alignment.Create`, then call `ProfileView.Create(...)` with duplicate-name retry
    — see [Chunk F, step 3](../walkthrough/f-profile-views.md#step-3--the-profile-view-with-duplicate-name-retry).

    **Stretch B** (real crossings only): fold in `is_crossing` from Solution 8, run it
    against the other networks' pipes, and add only genuine crossings to each view.
    At that point you have rebuilt the core of the Profile View Generator from scratch.

---

## Self-assessment: are you ready for real automations?

You can consider the onboarding complete when you can, **without looking anything up**:

- [ ] Write the lock → transaction → commit → dispose skeleton from memory.
- [ ] Read any input safely and explain why bare `IN[i]` is dangerous.
- [ ] Create, update, and erase a database object (and register created objects).
- [ ] Resolve a style with graceful fallback, and know when to raise vs. warn.
- [ ] Call an `out`-parameter method with `clr.Reference` and explain the silent trap.
- [ ] Classify a crossing with the three-question test and explain the buggy version.
- [ ] Structure a batch loop that skips-and-records bad items and fails only on fatal.
- [ ] Run the full Cursor → Dynamo → Watch loop and read failures from `results`.

!!! success "Where to go next"
    Return to the [Cookbook](../cookbook.md) and [Gotchas](../gotchas.md) as daily
    references, keep `.cursorrules` loaded so Cursor writes to standard, and read the
    full [walkthrough](../walkthrough/a-imports.md) to see every pattern combined in a
    production-scale script. From here, most Civil 3D business automations are these
    patterns rewired to a new goal.
