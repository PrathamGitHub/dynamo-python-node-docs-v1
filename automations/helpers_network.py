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