"""Extract gravity + pressure networks into flat primitive rows.
Uses the OPEN transaction from context — never opens its own."""
from Autodesk.AutoCAD.DatabaseServices import SymbolUtilityServices, Handle, OpenMode
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


def find_alignment_id_by_name(tr, civdoc, name):
    """ObjectId of the alignment called `name`, or None. Walks GetAlignmentIds()."""
    for aid in civdoc.GetAlignmentIds():
        if getattr(tr.GetObject(aid, OpenMode.ForRead), "Name", "") == name:
            return aid
    return None


# -----------------------------------------------------------------------------
# Get Pipe End Structure Handles/Ids

search_pairs = (("StartStructureId", "EndStructureId"),
                 ("StartStructure", "EndStructure"))
def get_pipe_end_structure_handles(tr, pipe_obj, missing=None):
    """(start_handle, end_handle) for a Pipe. Probes BOTH the direct
    *StructureId properties AND the older *Structure.ObjectId form for
    cross-version robustness. Returns (None, None) on failure."""
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


def get_pipe_end_structure_ids(pipe_obj):
    """(start_structure_id, end_structure_id) for a Pipe OBJECT (not an id).
    Verbatim from v2: tries the direct *Id properties, then the older object-
    reference properties that expose an .ObjectId. Returns (None, None) on failure.
    NOTE: takes the opened pipe object, so callers pass tr.GetObject(pid, ...)."""
    for a, b in search_pairs:
        if hasattr(pipe_obj, a) and hasattr(pipe_obj, b):
            try:
                sv, ev = getattr(pipe_obj, a), getattr(pipe_obj, b)
                if hasattr(sv, "ObjectId"): sv = sv.ObjectId
                if hasattr(ev, "ObjectId"): ev = ev.ObjectId
                return sv, ev
            except Exception:
                pass
    return None, None


#------------------------------------------------------------------------------
# Extractor drivers for gravity networks

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

#------------------------------------------------------------------------------
# Extractor drivers for pressure networks

def get_pressure_network_ids(civdoc, HAS_PRESSURE, CivilDocumentPressurePipesExtension, warnings):
    """All pressure-network ObjectIds via the extension static method.
    Returns [] (with a warning) if the pressure module isn't installed."""
    if not HAS_PRESSURE:
        warnings.append("AeccPressurePipesMgd not available; skipping pressure networks.")
        return []
    try:
        return list(CivilDocumentPressurePipesExtension.GetPressurePipeNetworkIds(civdoc))
    except Exception as e:
        warnings.append(f"Could not enumerate pressure networks: {e}")
        return []


def extract_pressure_pipes(tr, pnet, network_name, missing, skipped):
    """Flatten a PressureNetwork's pipes into the SAME row shape as gravity
    pipes, tagged role='pressure_cross'. Pressure pipes carry no start/end
    structures in our model, so those handles stay None."""
    rows = []
    for pid in pnet.GetPipeIds():
        try:
            p = tr.GetObject(pid, OpenMode.ForRead)
            sx, sy, sz = pt_xyz(get_member(p, "StartPoint", missing=missing))
            ex, ey, ez = pt_xyz(get_member(p, "EndPoint", missing=missing))
            dia = get_member(p, "InnerDiameter", float, None, missing)
            if dia is None:
                dia = get_member(p, "OuterDiameter", float, None, missing)
            rows.append({
                "handle": p.Handle.ToString(),
                "name": get_member(p, "Name", str, None, missing),
                "network": network_name, "role": "pressure_cross",
                "start_handle": None, "end_handle": None,
                "start_x": sx, "start_y": sy, "start_z": sz,
                "end_x": ex, "end_y": ey, "end_z": ez,
                "diameter": dia, "slope": None,
                "wkt": wkt_line(sx, sy, ex, ey),
            })
        except Exception as e:
            skipped.append({"pressure_pipe": str(pid), "reason": str(e)})
    return rows

# -----------------------------------------------------------------------------
# Add Parts

def scan_pvparts_from_modelspace(tr, db, missing_oids, pvpart_class, warnings):
    """Recover ProfileViewPart ids by scanning ModelSpace for entities of
    pvpart_class whose ModelPartId matches one of missing_oids.
    Returns {model_part_oid: pvpart_oid}. Used only when AddToProfileView
    returned void/None for those parts."""
    found = {}
    if not missing_oids or pvpart_class is None:
        return found
    target = {str(o): o for o in missing_oids}      # compare by string form
    try:
        ms = tr.GetObject(SymbolUtilityServices.GetBlockModelSpaceId(db), OpenMode.ForRead)
        for eid in ms:
            try:
                obj = tr.GetObject(eid, OpenMode.ForRead)
                if isinstance(obj, pvpart_class):
                    key = str(obj.ModelPartId)
                    if key in target:
                        found[target[key]] = eid
            except Exception:
                pass
    except Exception as e:
        warnings.append(f"ProfileViewPart fallback scan error: {e}")
    return found


def add_parts_to_profile_view(tr, db, ids_to_add, pv_id,
                              pvpart_class, has_pvpart, warnings):
    """Add gravity parts (Pipe/Structure) to a PV. Returns {model_oid: pvpart_oid}.
    Fast path: trust AddToProfileView's return. Fallback: ModelSpace scan for
    any part that returned void."""
    pvpart_map = {}
    for oid in ids_to_add:
        try:
            part = tr.GetObject(oid, OpenMode.ForWrite)
            if hasattr(part, "AddToProfileView"):
                result = part.AddToProfileView(pv_id)
                try:
                    if result is not None and not result.IsNull:
                        pvpart_map[oid] = result
                except Exception:
                    pass                              # void return -> fallback later
        except Exception as e:
            warnings.append(f"AddToProfileView failed for {oid}: {e}")

    missing = [o for o in ids_to_add if o not in pvpart_map]
    if missing and has_pvpart:
        recovered = scan_pvparts_from_modelspace(tr, db, missing, pvpart_class, warnings)
        if recovered:
            pvpart_map.update(recovered)
            warnings.append(f"{len(recovered)} gravity ProfileViewPart id(s) "
                            f"recovered via ModelSpace scan.")
    return pvpart_map


def add_pressure_pipes_to_profile_view(tr, db, pressure_pipe_ids, pv_id,
                                       pvpressurepart_class, has_pvpressurepart, warnings):
    """Same contract for pressure pipes -> ProfileViewPressurePart. The returned
    pvpart id is required by CrossingPressurePipeProfileLabel.Create (Stage 7)."""
    pvpart_map = {}
    for oid in pressure_pipe_ids:
        try:
            ppart = tr.GetObject(oid, OpenMode.ForWrite)
            if hasattr(ppart, "AddToProfileView"):
                result = ppart.AddToProfileView(pv_id)
                try:
                    if result is not None and not result.IsNull:
                        pvpart_map[oid] = result
                except Exception:
                    pass
        except Exception as e:
            warnings.append(f"Pressure AddToProfileView failed for {oid}: {e}")

    missing = [o for o in pressure_pipe_ids if o not in pvpart_map]
    if missing and has_pvpressurepart:
        recovered = scan_pvparts_from_modelspace(tr, db, missing, pvpressurepart_class, warnings)
        if recovered:
            pvpart_map.update(recovered)
            warnings.append(f"{len(recovered)} pressure ProfileViewPart id(s) "
                            f"recovered via ModelSpace scan.")
    return pvpart_map


def probe_styles_root(civdoc, warnings):
    """One-time diagnostic (kept for NEW builds, not the resolution strategy).
    Dumps (a) StylesRoot collection names, and (b) the public members/type of the
    PipeStyles collection, so on an unfamiliar build you can see how it exposes
    styles. On THIS build (2025.2.5) the answers are already known — PipeStyles uses
    get_Item, and pressure styles are absent from civdoc.Styles entirely (see the
    'PV-part styles' section). Everything goes to `warnings` for Dynamo output."""
    names = [n for n in dir(civdoc.Styles) if not n.startswith("_")]
    style_cols = [n for n in names if "Style" in n]
    warnings.append(f"StylesRoot members with 'Style': {style_cols}")
    try:
        coll = civdoc.Styles.PipeStyles
        members = [n for n in dir(coll) if not n.startswith("_")]
        warnings.append(f"PipeStyles type: {type(coll).__name__}")
        warnings.append(f"PipeStyles members: {members}")
        # count is the one property the Developer Guide guarantees; probe it
        for cn in ("Count", "count"):
            if hasattr(coll, cn):
                warnings.append(f"PipeStyles.{cn} = {getattr(coll, cn)}")
    except Exception as e:
        warnings.append(f"probe_styles_root: PipeStyles introspection failed: {e}")
    return style_cols


def resolve_part_styles(civdoc, grav_name, warnings):
    """Resolve the GRAVITY part style that governs profile-view display.
    The pipe/structure network style IS the profile-view display style; there is
    no separate ProfileViewPart style collection. PRESSURE is intentionally NOT
    handled here — pressure styles are unreachable by name on this build (no
    collection, not in civdoc.Styles, write-only .Name); use
    pressure_style_from_sample instead. Returns a gravity style ObjectId or None
    (-> set_pvpart_styles no-ops).

    Access pattern is VERIFIED for Civil 3D 2025.2.5: PipeStyleCollection is not
    Python-indexable (coll[0]/coll[name] -> 'unindexable object'); use Contains +
    get_Item(name) for by-name and get_Item(0) for the default."""
    coll = getattr(civdoc.Styles, "PipeStyles", None)
    if coll is None:
        warnings.append("resolve_part_styles: PipeStyles collection absent; gravity style left unset.")
        return None
    try:
        if grav_name and coll.Contains(grav_name):
            return coll.get_Item(grav_name)     # by-name (NOT coll[grav_name])
        return coll.get_Item(0)                  # first/default (NOT coll[0])
    except Exception as e:
        warnings.append(f"resolve_part_styles: gravity style resolution failed: {e}")
        return None


def pressure_style_from_sample(tr, civdoc, get_pressure_ids, warnings, pipe_name=None):
    """Return a pressure-pipe StyleId to apply to crossing pressure PV parts.

    On Civil 3D 2025 (this build) pressure styles are NOT reachable by name:
      - civdoc.Styles has no PressurePipeStyles collection;
      - the target style isn't in any civdoc.Styles.* collection;
      - PressurePipeStyle.Name is WRITE-ONLY (cannot match by name).
    So we borrow a live StyleId via PressurePipe.get_StyleId() — which works even
    though `pipe.StyleId` is write-only for assignment. Optionally match a specific
    pipe by name to copy that pipe's style; otherwise use the first pressure pipe.
    Returns an ObjectId (never None if any pressure pipe exists) or None."""
    try:
        for nid in get_pressure_ids(civdoc):
            pnet = tr.GetObject(nid, OpenMode.ForRead)
            for pid in pnet.GetPipeIds():
                p = tr.GetObject(pid, OpenMode.ForRead)
                if pipe_name is not None:
                    try:
                        if getattr(p, "Name", None) != pipe_name:
                            continue
                    except Exception:
                        continue
                sid = p.get_StyleId()           # readable; direct '=' assignment is not
                if sid is not None and not sid.IsNull:
                    return sid
    except Exception as e:
        warnings.append(f"pressure_style_from_sample failed: {e}")
    return None


def pvpart_addition_stats(ids_to_add, pvpart_map):
    """Diagnostic — quantify the add hand-off for ONE add_* call.
    Returns {requested, returned, missing, missing_ids} where:
      requested   = ids we asked AddToProfileView to draw
      returned    = pvparts we ended up with (fast-path + ModelSpace fallback)
      missing     = requested that produced NO pvpart at all -> unlabelable
      missing_ids = the actual model oids that fell through (for drill-down)
    A non-empty `missing` is the Stage-7 label bug's fingerprint, surfaced here.
    Note: this counts final coverage; it does NOT distinguish fast-path from
    fallback (the add_* functions already warn on fallback recovery)."""
    req = list(ids_to_add)
    got = set(pvpart_map.keys())
    missing_ids = [o for o in req if o not in got]
    return {"requested": len(req), "returned": len(pvpart_map),
            "missing": len(missing_ids), "missing_ids": missing_ids}


def set_pvpart_styles(tr, pvpart_map, style_id, warnings):
    """Apply the display style for parts drawn in a profile view.
    pvpart_map: {model_oid: pvpart_oid} as returned by add_*_to_profile_view.
    style_id: ObjectId of the target network part style; None/Null -> no-op.
    The profile-view display of a pipe/structure follows its NETWORK part style,
    so we set StyleId on the MODEL part (model_oid), not the pvpart. Called once
    for gravity parts, once for pressure parts, after add."""
    if style_id is None:
        return
    try:
        if style_id.IsNull:
            return
    except Exception:
        return
    for model_oid, pvpart_oid in pvpart_map.items():
        try:
            part = tr.GetObject(model_oid, OpenMode.ForWrite)   # the pipe/structure
            part.StyleId = style_id
        except Exception as e:
            warnings.append(f"set_pvpart_styles: could not set style on {model_oid}: {e}")


def build_handle_index(db, tr, civdoc, has_pressure, pressure_ext, warnings):
    """Map every gravity (and, if available, pressure) pipe/structure HANDLE to
    its live ObjectId for this session. DuckDB stores handles (portable, stable);
    the drawing needs ObjectIds (session-bound). Returns {handle_str: ObjectId}.

    Uses Database.GetObjectId(add=False, Handle, reserved=0) — the direct, correct
    inverse of `oid.Handle.ToString()` used by extraction. A handle that no longer
    resolves (part deleted since extraction) is simply skipped, not fatal."""
    index = {}

    def add_net(net_id):
        net = tr.GetObject(net_id, OpenMode.ForRead)
        for oid in list(net.GetPipeIds()) + list(net.GetStructureIds()):
            try:
                index[tr.GetObject(oid, OpenMode.ForRead).Handle.ToString()] = oid
            except Exception:
                pass

    for gid in civdoc.GetPipeNetworkIds():
        try:
            add_net(gid)
        except Exception as e:
            warnings.append(f"handle-index gravity net skipped: {e}")

    if has_pressure and pressure_ext is not None:
        try:
            for pid in pressure_ext.GetPressurePipeNetworkIds(civdoc):
                pnet = tr.GetObject(pid, OpenMode.ForRead)
                for oid in pnet.GetPipeIds():
                    try:
                        index[tr.GetObject(oid, OpenMode.ForRead).Handle.ToString()] = oid
                    except Exception:
                        pass
        except Exception as e:
            warnings.append(f"handle-index pressure nets skipped: {e}")

    return index

