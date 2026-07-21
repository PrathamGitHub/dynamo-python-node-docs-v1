# Stage 9 — Limits & verification

!!! abstract "Goal of this page"
    State plainly what this pipeline does **not** guarantee, give the **first-run
    probe** that catches environment problems before a 200-pipe batch, and show how
    to **read the audit table** to decide whether to trust a run. A tool you can't
    verify is a tool you can't defend in a review — this page is what makes the
    output auditable rather than merely plausible.

---

## Known limitations — stated, not hidden

!!! warning "C1 · Z is provisional (invert vs. centerline)"
    The clash verdict uses each pipe's **invert-derived z** interpolated to the
    crossing point. If a network stores levels as **centerline** (or the extractor
    read the wrong attribute), every gap is off by roughly one diameter — a `TIGHT`
    might really be `CLASH`. The verdict is therefore **provisional** until the
    z-source is confirmed for the specific drawing. The probe below checks this
    first. Treat `dz` as indicative, not final, on an unverified drawing.

!!! warning "C2 · Pressure crossing-LABEL style must be BORROWED — and needs a seed"
    RESOLVED mechanism, with a standing prerequisite. On 2025.2.5 there is **no
    pressure crossing label-style collection** anywhere under `civdoc.Styles.LabelStyles`
    (probe-confirmed: `PipeLabelStyles`, `ProfileLabelStyles`, `ProfileViewLabelStyles`,
    … but no `Pressure*LabelStyles`). Reusing the gravity `CrossProfileLabelStyles`
    id fails at runtime — `CrossingPressurePipeProfileLabel.Create` rejects it in
    `CheckArgLabelStyle` with *Value does not fall within the expected range* (wrong
    style type). And `Create` has **one overload only** — `(pvPart, pv, ratio,
    styleId)` — the style is mandatory (no styleless fallback; a 3-arg call throws
    *No method matches*).

    **Solution:** `available_pressure_label_styles` scans ModelSpace once and builds
    a `{StyleName → StyleId}` dict from placed pressure crossing labels. Callers do
    an **exact lookup** — no silent fallback to the first placed style (that was the
    original bug: all pressure crossings got `'WATER CROSSING'` regardless of
    description).

    **Prerequisite:** at least one placed pressure crossing label must exist in the
    drawing **for each style name** the CSV intends to use. On a fresh drawing with
    none, `resolve_pressure_label_style` returns Null and pressure labels are
    **skipped** with a warning that lists both the missing requested styles and the
    available placed styles. Ship the styles in the template and place one label per
    style once. Verify **visually** (do not read pressure label style names back —
    see C7).

!!! warning "C3 · Detection is 2D-intersection + z-gap, not solid clash"
    Crossings are found by `ST_Intersects` on plan geometry, then judged by a
    vertical gap against summed radii. Two pipes that pass over each other with a
    plan-view intersection are caught; two that are close but whose **plan lines
    don't cross** are not (they never produce a row). This is intentional — it
    matches how a crossing is defined on a profile — but it is not a full 3D
    proximity/solid-interference check.

!!! warning "C4 · Spatial extension needs first-use internet"
    `INSTALL spatial` fetches the DuckDB spatial extension on first use. On an
    air-gapped machine, pre-stage the extension (copy it into the DuckDB extension
    directory) before the first run, or the `LOAD spatial` fails. Once staged, runs
    are offline.

!!! warning "C5 · PARTIALLY RESOLVED — residual unlabelled crossings (Stage 7)"
    The prime suspect — the Stage-6 → Stage-7 pvpart **hand-off** — has been
    **cleared**: a per-PV diagnostic proved `labelable == intended` on every profile
    view. The per-description style-mapping bug (all pressure crossings silently
    getting the same style) has also been **fixed** (see C2 and Stage 7). What
    remains open is a **narrower** set of causes:

    1. **`runs_alongside = TRUE` filtering** — a crossing classified as running
       alongside is excluded from labelling. Verify with the audit table.
    2. **Multiplicity** — a pipe crossing the main twice yields two `crossings` rows
       but one pvpart id; the second `Create` on the same pvpart may no-op or throw.
    3. **Null pressure label style** — a pressure crossing whose description maps to
       a style not yet placed in the drawing is skipped. The warning message lists
       exactly which styles are missing and which are available.
    4. **Structures aren't in `crossings`** — end structures added to the PV have no
       `cross_handle` row; their pvparts are created and never labelled. This is
       correct behaviour (we label pipes, not structures). Confirm "missing" labels
       aren't just structures.

    Plan-view markers (Stage 8) mark **every** crossing regardless, so a run stays
    auditable while this is open — trust the audit + markers as complete; treat
    labels as provisional until all three counts reconcile.

!!! warning "C6 · Pressure PV-part style is COPIED, not selected by name"
    On this build (2025.2.5) pressure styles cannot be resolved by name — there is
    no `PressurePipeStyles` collection under `civdoc.Styles`, the style a pipe uses
    is not present in any `civdoc.Styles.*` collection, and `PressurePipeStyle.Name`
    is **write-only**. So crossing pressure parts are styled by **copying a live
    pressure pipe's `StyleId`** (`pressure_style_from_sample` via `p.get_StyleId()`).
    Consequence: `IN[10]` in the Stage-8 orchestrator means "pressure **source pipe**
    name to copy from" (empty → first pressure pipe). To get a specific look, style
    one pressure pipe in Prospector and point the input at it. Gravity is unaffected
    (`PipeStyles.get_Item` resolves normally).

!!! danger "C7 · Pressure StyleId read-back throws — the write still happened"
    Reading `part.StyleId` on a pressure pipe after assignment throws
    `TypeError: property cannot be read`. The `set_pvpart_styles` function includes
    a read-back check (`if part.StyleId == style_id`) to verify the write on gravity
    parts — this works correctly for gravity. On **pressure parts**, the read-back
    throws, is caught by the `except` branch, and emits
    `"set_pvpart_styles: could not set style on..."`. However, the **write
    (`part.StyleId = style_id`) already executed before the read-back**, so the
    style *was* applied. The warning is misleading but harmless.

    **Expected output for pressure parts:** `applied 0/N part styles.` in warnings.
    This does **not** indicate a failure — it means the read-back threw on every
    pressure part, as expected. Verify pressure part styling **visually** in the
    profile view, or via the Stage-8 count reconciliation — not from the warning
    count.

    **Do not remove the read-back** — it provides genuine verification for gravity
    parts. Do not add a separate pressure-only read-back path — it will throw the
    same way.

!!! note "Collection access is by `get_Item`, not indexing (2025.2.5)"
    Civil 3D style collections (`PipeStyleCollection`, `StructureStyleCollection`)
    are **not** Python-indexable: `coll[0]` / `coll[name]` throw
    `TypeError: unindexable object`. Use `Contains(name)` + `get_Item(name)` (by
    name), `get_Item(0)` (default), `Count`, `ToObjectIds()`, or iteration. Verified
    by probe; do not reintroduce indexer-based access.

!!! warning "C8 · Long-running batches may appear unresponsive"
    On large networks (hundreds of IC pipes, thousands of crossings), the node can
    run for hours without Dynamo showing any progress. This is not a hang — Dynamo
    buffers all output until the node returns. Monitor progress via
    `tail -f <duckdb_dir>/progress.log` from WSL. The log is written and flushed
    after every PV. If the log stops advancing at `PV k/N START`, that specific pipe
    is the bottleneck — check its crossing count in the audit table.

---

## The first-run probe

Run this **once** on a new drawing before any batch. It's read-only — it creates
nothing — and it catches the environment failures that otherwise surface 150 pipes
into a run.

```python
# probe.py — read-only sanity check. IN[0]=main network, IN[1]=surface, IN[2]=duckdb path
def run(context):
    civdoc, IN = context["civdoc"], context["IN"]
    from automations import duckdb_engine as duck
    report = {}
    con = duck.connect(IN[2] if len(IN) > 2 and IN[2] else None)

    # 1) tables exist and are populated
    for t in ("pipes", "structures", "connections", "crossings"):
        try:
            report[t] = con.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
        except Exception as e:
            report[t] = f"MISSING: {e}"

    # 2) main network actually present + role tagged
    report["main_pipes"] = con.execute(
        "SELECT count(*) FROM pipes WHERE role='main'").fetchone()[0]

    # 2b) IC- filter coverage
    report["ic_mains"] = con.execute("""
        SELECT count(*) FROM pipes p
        JOIN structures s ON p.start_handle = s.handle
        WHERE p.role = 'main' AND s.name LIKE 'IC-%'
    """).fetchone()[0]

    # 3) verdict vocabulary — must be the 3-way scheme, not the 2-way draft
    report["verdicts"] = con.execute(
        "SELECT verdict, count(*) FROM crossings GROUP BY verdict").fetchall()

    # 4) z-source smell test (C1): are inverts plausibly below rims?
    report["z_sanity"] = con.execute("""
        SELECT count(*) FROM structures
        WHERE sump_z IS NOT NULL AND rim_z IS NOT NULL AND sump_z > rim_z
    """).fetchone()[0]     # >0 means inverts above rims -> z-source suspect

    # 5) surface presence (Civil 3D side) — EG profiles are empty without it
    tr = context["tr"]
    want = (IN[1] or "").strip().lower() if len(IN) > 1 else ""
    from Autodesk.AutoCAD.DatabaseServices import OpenMode
    report["surface_found"] = bool(want) and any(
        str(getattr(tr.GetObject(sid, OpenMode.ForRead), "Name", "")).strip().lower() == want
        for sid in civdoc.GetSurfaceIds())

    # 6) pressure-style SOURCE (C6/C7): styling copies a live pipe's StyleId
    report["pressure_style_source"] = "n/a (no pressure networks)"
    try:
        from Autodesk.Civil.ApplicationServices import CivilDocumentPressurePipesExtension
        pnet_ids = list(CivilDocumentPressurePipesExtension.GetPressurePipeNetworkIds(civdoc))
        if pnet_ids:
            found = None
            for nid in pnet_ids:
                pnet = tr.GetObject(nid, OpenMode.ForRead)
                for pid in pnet.GetPipeIds():
                    p = tr.GetObject(pid, OpenMode.ForRead)
                    sid = p.get_StyleId()            # bound getter; do NOT read p.StyleId
                    if sid is not None and not sid.IsNull:
                        found = str(getattr(p, "Name", "?"))
                        break
                if found:
                    break
            report["pressure_style_source"] = (
                f"OK (copy from '{found}')" if found
                else "WARN: pressure pipes exist but none yielded a StyleId")
    except Exception as e:
        report["pressure_style_source"] = f"WARN: {e}"

    # 7) pressure label styles available to borrow (C2)
    report["pressure_label_styles"] = "n/a"
    try:
        from automations import helpers_labels as lbl
        db = context["db"]
        styles, _ = lbl.available_pressure_label_styles(db, [])
        report["pressure_label_styles"] = sorted(styles.keys()) if styles else "WARN: none placed"
    except Exception as e:
        report["pressure_label_styles"] = f"WARN: {e}"

    return {"probe": report}
```

!!! tip "Read the probe like a pre-flight checklist"
    - `crossings` = 0 but `main_pipes` > 0 → detection never ran (or wrong main
      name). Fix Stage 3 before batching.
    - `ic_mains` = 0 but `main_pipes` > 0 → no main pipe has a start structure
      named `IC-*`. Check extraction and structure naming.
    - `verdicts` shows `CLEARANCE_OK` → the **two-way foundation SQL** is still
      wired in; the approved three-way (`CLASH`/`TIGHT`/`CLEAR`) isn't. Fix the
      engine.
    - `z_sanity` > 0 → inverts sit above rims → the z-source is wrong (C1). Every
      verdict is suspect until fixed.
    - `surface_found` False → EG profiles will be empty (`ObjectId.Null`); check
      the surface name in `IN`.
    - `pressure_label_styles` = `WARN: none placed` → no pressure crossing labels
      exist in the drawing to borrow from; pressure labels will be skipped. Place
      one label per required style (C2).

---

## Reading the audit table to trust a run

`crossings_audit.csv` (Stage 8) is the artefact you defend the plan set with.
A disciplined review, in order:

| Step | Filter | What you're checking |
|---|---|---|
| 1 | `verdict = 'CLASH'` | Every hard conflict — each must be real and resolved in design |
| 2 | `verdict = 'TIGHT'` | Near-misses within clearance — confirm the clearance target |
| 3 | `angle_class = 'NEAR_PARALLEL'` + `runs_alongside = TRUE` | Correctly excluded from labels? Any real crossing wrongly dropped? |
| 4 | `angle_class = 'OBLIQUE'` | Sanity of the classification band (the reference *filtered* these — we keep them) |
| 5 | count vs. plan markers | Audit rows == plan markers == (once all styles placed) labels |

!!! success "What 'trustworthy' means here"
    A run is trustworthy when: the probe is clean (right verdict vocabulary, sane
    z, IC- mains found, pressure styles placed), the audit `CLASH`/`TIGHT` rows
    survive engineering review, and the three counts reconcile — **audit rows ==
    plan markers == labels**. The first two already hold; the third is gated on the
    open C5 items. Until then, trust the **audit + markers** (complete) and treat
    the **labels** as provisional (possibly incomplete) — and say so in any review.

!!! note "Why this page exists"
    The reference produced drawings that *looked* done and failed audit — crossings
    missed, clashes mislabelled, obliques dropped. The entire value of the DuckDB
    approach is that the evidence is **queryable and reproducible**: same input,
    same `crossings` table, same audit, same markers. Verification isn't a
    postscript to this pipeline; it's the reason for its architecture.
