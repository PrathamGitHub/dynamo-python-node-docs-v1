from Autodesk.Civil.DatabaseServices import CrossingPipeProfileLabel, CrossingPressurePipeProfileLabel
from Autodesk.AutoCAD.DatabaseServices import ObjectId

def station_to_ratio(station, pv_start, pv_end):
    """Fraction [0,1] of `station` within the PV window [pv_s, pv_e].
    Subtractive form so it stays correct when a PV starts at a nonzero station."""
    span = pv_end - pv_start
    if span <= 0:
        return None                      # caller must skip; degenerate PV
    r = (station - pv_start) / span
    return min(1.0, max(0.0, r))         # clamp: a crossing just outside the window
                                         # anchors at the edge instead of floating


def station_of_point(aln, x, y, warnings):
    """Station of world point (x, y) on alignment `aln`, or None if it can't be
    projected. Uses the out-param convention: dummy 0.0 doubles drive overload
    resolution; real (station, offset) come back in the return tuple after a
    leading None for the void return."""
    try:
        _, st, off = aln.StationOffset(float(x), float(y), 0.0, 0.0)
        return st
    except Exception as e:
        warnings.append(f"station_of_point failed ({x:.3f},{y:.3f}): {e}")
        return None


def get_profile_view_station_range(pv_obj):
    """PV station bounds (start, end). Confirmed on 2025: ProfileView.StationStart /
    StationEnd. Do NOT wrap in a bare except that returns (None, None) — a wrong
    read must fail loudly, not silently produce null ratios."""
    return pv_obj.StationStart, pv_obj.StationEnd


def create_gravity_label(pvpart_oid, pv_oid, style_id, warnings):
    """Create a gravity crossing label. Returns the label ObjectId on success, else None.
    Source is the ProfileViewPart id from Stage 6 (NOT the raw pipe id)."""
    if pvpart_oid is None or pvpart_oid.IsNull:
        warnings.append("gravity label skipped: no ProfileViewPart id")
        return None
    if style_id is not None and not style_id.IsNull:
        try:
            res = CrossingPipeProfileLabel.Create(pvpart_oid, pv_oid, style_id)
            return res[0] if (res is not None and res.Count > 0) else None
        except Exception as e:
            warnings.append(f"gravity label (styled) failed, retrying default: {e}")
    try:
        res = CrossingPipeProfileLabel.Create(pvpart_oid, pv_oid)      # 2-arg default style
        return res[0] if (res is not None and res.Count > 0) else None
    except Exception as e:
        warnings.append(f"gravity label failed: {e}")
        return None


def create_pressure_label(pvpart_oid, pv_oid, ratio, style_id,
                          has_pressure_label, warnings):
    """Create a pressure crossing label at normalized ratio in [0,1].
    Returns the label ObjectId on success, else None. No-op (warns) if the
    pressure class is unavailable."""
    if not has_pressure_label or CrossingPressurePipeProfileLabel is None:
        warnings.append("pressure label skipped: pressure label class unavailable")
        return None
    if pvpart_oid is None or pvpart_oid.IsNull:
        warnings.append("pressure label skipped: no ProfileViewPressurePart id")
        return None
    r = 0.5 if ratio is None else max(0.0, min(1.0, float(ratio)))
    if style_id is not None and not style_id.IsNull:
        try:
            res = CrossingPressurePipeProfileLabel.Create(pvpart_oid, pv_oid, r, style_id)
            return res[0] if (res is not None and res.Count > 0) else None
        except Exception as e:
            warnings.append(f"pressure label (styled) failed, retrying default: {e}")
    try:
        res = CrossingPressurePipeProfileLabel.Create(pvpart_oid, pv_oid, r)
        return res[0] if (res is not None and res.Count > 0) else None
    except Exception as e:
        warnings.append(f"pressure label failed: {e}")
        return None


    
from automations import helpers_core as core   # reuse get_style_id + unwrap_oid


def resolve_gravity_label_style(civdoc, desired_name, warnings):
    """ObjectId of the gravity crossing label style (documented path first)."""
    coll = civdoc.Styles.LabelStyles.PipeLabelStyles.CrossProfileLabelStyles
    sid, _ = core.get_style_id(coll, desired_name, warnings, "Crossing Pipe Label Style")
    return sid


def available_pressure_label_styles(db, warnings):
    """Return ({StyleName -> StyleId}, first StyleId) for placed pressure crossing
    labels in ModelSpace.

    Civil 3D 2025 does not expose pressure crossing-label styles through a normal
    enumerable style collection on this build. The only confirmed reliable path is
    borrowing the StyleId from an already placed pressure crossing label.

    Therefore, per-description pressure mapping requires one placed label for each
    pressure label style that the CSV intends to use.
    """
    from Autodesk.AutoCAD.DatabaseServices import (
        OpenMode, ObjectId, SymbolUtilityServices)
    from Autodesk.AutoCAD.Runtime import RXClass
    import Autodesk.Civil.DatabaseServices as cds

    target = None
    for cand in ("CrossingPressurePipeProfileLabel", "PressurePartProfileLabel"):
        try:
            target = RXClass.GetClass(getattr(cds, cand))
            break
        except Exception:
            continue
    if target is None:
        warnings.append("available_pressure_label_styles: pressure label RXClass unavailable.")
        return {}, ObjectId.Null

    tr = db.TransactionManager.TopTransaction
    try:
        ms = tr.GetObject(SymbolUtilityServices.GetBlockModelSpaceId(db), OpenMode.ForRead)
    except Exception as e:
        warnings.append(f"available_pressure_label_styles: ModelSpace open failed: {e}")
        return {}, ObjectId.Null

    styles = {}
    first = ObjectId.Null
    for oid in ms:
        try:
            if not oid.ObjectClass.IsDerivedFrom(target):
                continue
            lab = tr.GetObject(oid, OpenMode.ForRead)
            sid = lab.StyleId
            if sid is None or sid.IsNull:
                continue

            try:
                name = getattr(lab, "StyleName", None)
            except Exception:
                name = None
            name = str(name).strip() if name else None

            if name and name not in styles:
                styles[name] = sid
            if first.IsNull:
                first = sid
        except Exception:
            continue

    return styles, first


def resolve_pressure_label_style(db, style_name, warnings):
    """Pressure crossing label styles are NOT reachable via civdoc.Styles on this
    build — no Pressure*LabelStyles collection exists, and the gravity
    CrossProfileLabelStyles id is the WRONG TYPE (CheckArgLabelStyle rejects it with
    'Value does not fall within the expected range'). So we BORROW a valid StyleId
    from an existing pressure crossing label already in the drawing (same tactic as
    pressure_style_from_sample for pipe styles). `style_name` (optional) matches a
    specific label's StyleName (e.g. 'WATER CROSSING'); else take the first.
    Returns a valid ObjectId, or ObjectId.Null if none exists (caller must SKIP —
    there is no styleless Create overload)."""
    from Autodesk.AutoCAD.DatabaseServices import ObjectId

    styles, first = available_pressure_label_styles(db, warnings)

    if style_name:
        key = str(style_name).strip()
        sid = styles.get(key)
        if sid is not None and not sid.IsNull:
            return sid
        warnings.append(
            "resolve_pressure_label_style: requested pressure crossing label style "
            f"{key!r} is not borrowable because no placed pressure crossing label "
            f"uses it. Available placed styles: {sorted(styles.keys())}"
        )
        return ObjectId.Null

    if first.IsNull:
        warnings.append("resolve_pressure_label_style: no existing pressure crossing "
                        "label to borrow a style from; pressure labels will be skipped. "
                        "Place one pressure crossing label manually (any style) once, "
                        "or add the style to the template.")
    return first


def consume_handoff(context, handoff, con, net, pvpart_class, pvpressurepart_class, warnings):
    """Fast path: reconstruct per-PV records from Stage-6's serialised hand-off
    (IN[5] = Stage-6 OUT[0]). Every handle -> ObjectId via
    db.GetObjectId(False, Handle(int(h,16)), 0) — no build_handle_index needed.
    Gravity and pressure crossing pipes draw as DIFFERENT profile-view part classes
    (ProfileViewPart vs ProfileViewPressurePart), so each role is scanned SEPARATELY
    with its own class — a single blended scan silently drops pressure. Returns None
    if handoff is empty/None so the caller falls back to rederive_pv_records.

    handoff: list of {main_handle, pv_handle, grav_handles, pres_handles} dicts.
    Returns the same [{main_handle, pv_id, alignment_id, pvpart_gravity,
    pvpart_pressure}] shape as rederive_pv_records."""
    if not handoff:
        return None                          # caller falls back to rederive

    civdoc, tr, db = context["civdoc"], context["tr"], context["db"]
    from Autodesk.AutoCAD.DatabaseServices import Handle

    def oid_from_handle(h):
        """Resolve ANY database object's ObjectId from its handle HEX string.
        Handle has NO string constructor — it takes an integer. Probe-confirmed on
        2025.2.5: Handle(int(h,16)) works (pythonnet coerces the Python int to the
        Int64 the constructor wants); there is NO long-based GetObjectId overload, so
        going through a Handle object is mandatory. Returns an ObjectId or None."""
        if not h:
            return None
        try:
            n = int(str(h).strip(), 16)          # hex handle -> int; raises on garbage
        except ValueError:
            warnings.append(f"consume_handoff: {h!r} is not a hex handle; skipping.")
            return None
        try:
            oid = db.GetObjectId(False, Handle(n), 0)   # Handle(int) — no Int64 wrapper
            return None if (oid is None or oid.IsNull) else oid
        except Exception as e:
            warnings.append(f"consume_handoff: handle {h!r} resolve failed: {e}")
            return None

    def scan_role(handles, part_class, label):
        """Resolve this role's handles -> ObjectIds, then scan ModelSpace for their
        pvparts using the role's OWN part class. Returns {handle_str: pvpart_oid}."""
        oid2h = {}
        for h in handles:
            oid = oid_from_handle(h)
            if oid is not None:
                oid2h[oid] = h
        if not oid2h:
            return {}
        if part_class is None:
            warnings.append(f"consume_handoff: {label} handles present but part class "
                            f"unavailable; {label} pvparts not resolved.")
            return {}
        found = net.scan_pvparts_from_modelspace(tr, db, list(oid2h.keys()), part_class, warnings)
        return {oid2h[mid]: pv for mid, pv in found.items() if mid in oid2h}

    records = []
    for rec in handoff:
        main_handle = rec.get("main_handle")

        # ProfileView straight from its handle — no name scan, no handle index.
        pv_id = oid_from_handle(rec.get("pv_handle"))
        if pv_id is None:
            warnings.append(f"consume_handoff: PV handle {rec.get('pv_handle')!r} not found; skipping.")
            continue

        # Alignment id (needed for station_of_point in labelling)
        aln_name = None
        try:
            row = con.execute("SELECT name FROM pipes WHERE handle=?",
                              [main_handle]).fetchone()
            if row:
                aln_name = f"ALN - {row[0] or main_handle}"
        except Exception:
            pass
        aln_id = net.find_alignment_id_by_name(tr, civdoc, aln_name) if aln_name else None

        # Scan each role with its OWN part class (this is the fix for pvpart_pressure
        # coming back empty — pressure parts are ProfileViewPressurePart, not ProfileViewPart).
        grav_map = scan_role(rec.get("grav_handles", []), pvpart_class, "gravity")
        pres_map = scan_role(rec.get("pres_handles", []), pvpressurepart_class, "pressure")

        records.append({"main_handle": main_handle, "pv_id": pv_id,
                        "alignment_id": aln_id,
                        "pvpart_gravity": grav_map, "pvpart_pressure": pres_map})
    return records


def rederive_pv_records(context, con, net, pvpart_class, has_pressure, warnings):
    """Standalone-mode reconstruction of Stage-6's per-PV records by scanning the
    already-drawn ModelSpace. Returns [{main_handle, pv_id, pvpart_gravity,
    pvpart_pressure}, ...]. Slower than the fused hand-off (re-scans), but lets
    Stage 7 run with no in-run state."""
    civdoc, tr, db = context["civdoc"], context["tr"], context["db"]
    from automations import helpers_profileview as pvh

    # handle index + inverse (id -> handle), same as Stage 6
    h2id = net.build_handle_index(db, tr, civdoc, has_pressure, None, warnings)
    id2h = {v: k for k, v in h2id.items()}

    records = []
    mains = con.execute("SELECT handle, name FROM pipes WHERE role='main' ORDER BY name").fetchall()
    for main_handle, pname in mains:
        aln_name = f"ALN - {pname or main_handle}"
        aln_id = net.find_alignment_id_by_name(tr, civdoc, aln_name)
        pv_id = pvh.find_profile_view_id_by_name(tr, db, f"PV - {aln_name}")
        if pv_id is None or aln_id is None:
            continue
        rows = con.execute("""SELECT cross_handle, cross_kind FROM crossings
                              WHERE main_handle=? AND runs_alongside=FALSE""",
                           [main_handle]).fetchall()
        want = {h for h, _ in rows if h in h2id}
        want_ids = [h2id[h] for h in want]
        # scan ModelSpace once for the ProfileViewParts of these model ids
        found = net.scan_pvparts_from_modelspace(tr, db, want_ids, pvpart_class, warnings)
        grav = {id2h[mid]: pv for mid, pv in found.items() if id2h.get(mid)
                and any(h == id2h[mid] and k == 'gravity_cross' for h, k in rows)}
        pres = {id2h[mid]: pv for mid, pv in found.items() if id2h.get(mid)
                and any(h == id2h[mid] and k == 'pressure_cross' for h, k in rows)}
        records.append({"main_handle": main_handle, "pv_id": pv_id,
                        "alignment_id": aln_id,
                        "pvpart_gravity": grav, "pvpart_pressure": pres})
    return records


from Autodesk.AutoCAD.Geometry import Point3d
from Autodesk.AutoCAD.DatabaseServices import OpenMode

# spread tuning
_SPREAD_OFFSET_LEFT  = 10.0    # units left of the grid's left frame edge
_SPREAD_OFFSET_RIGHT = 5.0     # units right of the grid's right frame edge
_SPREAD_ROW_GAP      = 8.0     # desired vertical gap between stacked labels (clamped)


def spread_crossing_labels(tr, pv, recs, warnings):
    live = [(oid, s, z) for (oid, s, z) in recs
            if oid is not None and not oid.IsNull and s is not None and z is not None]

    if not live:
        return 0

    e = pv.GeometricExtents
    gx0, gx1 = e.MinPoint.X, e.MaxPoint.X
    gy0, gy1 = e.MinPoint.Y, e.MaxPoint.Y
    grid_h = gy1 - gy0
    s0, s1 = pv.StationStart, pv.StationEnd
    mid_s = (s0 + s1) / 2.0
    avg_z = sum(z for _, _, z in live) / len(live)
    try:
        ok, _, center_y = pv.FindXYAtStationAndElevation(mid_s, avg_z, 0.0, 0.0)
        if not ok:
            raise ValueError("ok=False")
    except Exception as ex:
        z0, z1 = pv.ElevationMin, pv.ElevationMax
        frac = 0.5 if z1 == z0 else (avg_z - z0) / (z1 - z0)
        center_y = gy0 + max(0.0, min(1.0, frac)) * grid_h
        warnings.append(f"spread: FindXYAtStationAndElevation failed: {ex}; using fallback center_y={center_y:.1f}")

    left  = sorted([r for r in live if r[1] <  mid_s], key=lambda r: r[2])
    right = sorted([r for r in live if r[1] >= mid_s], key=lambda r: r[2])

    def place(side, x):
        n = len(side)
        if n == 0:
            return 0
        gap = min(_SPREAD_ROW_GAP, grid_h / n)
        placed = 0
        for i, (oid, _s, _z) in enumerate(side):
            y = max(gy0, min(gy1, center_y + (i - (n - 1) / 2.0) * gap))
            try:
                lab = tr.GetObject(oid, OpenMode.ForWrite)
                lab.LabelLocation = Point3d(x, y, 0.0)

                placed += 1
            except Exception as ex:
                warnings.append(f"spread: set LabelLocation failed: {ex}")
        return placed

    return place(left, gx0 - _SPREAD_OFFSET_LEFT) + place(right, gx1 + _SPREAD_OFFSET_RIGHT)

