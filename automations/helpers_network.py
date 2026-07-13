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


