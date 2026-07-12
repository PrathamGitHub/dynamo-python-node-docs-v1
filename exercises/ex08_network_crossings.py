# =============================================================================
# automations/network_crossings.py
# CAPSTONE: inter-network pipe crossing detection via DuckDB (spatial + attrs).
# Loaded by the standard Recipe 7 loader node; follows the Recipe 8 contract:
#   * receives context (already-open tr) â€” does NOT lock/start-tr/commit
#   * returns a Data payload: {"Warnings": [], "Skipped": [], "Items": []}
#
# Pipeline:  extract (C3D read, uses context["tr"])  ->  DuckDB load+analyze
#            ->  angle/endpoint guards (Solution 8 rigor)  ->  Items
#
# INPUTS (context["IN"]):
#   IN[0] : list[str] | str | None  network names (None/[] -> all)
#   IN[1] : float                   clearance threshold (m); default 0.3
#   IN[2] : str | None              .duckdb path (EDA) or None (in-memory ETL)
# =============================================================================
# --- imports ----------------------------------------------------------------
import traceback

# --- force-reload the WHOLE package so nested helper edits are picked up ------
import sys
from pathlib import Path
sys.path.append(str(Path(__file__).resolve().parent))

from _helpers import unload_package
unload_package("_helpers")
from _helpers import (
_opt_str, _opt_int, _opt_float, normalize_name_list, _ensure_layer, cleanup, get_style_id_or_first, station_offset, point_location, endpoint_on_alignment, unload_package)
unload_package("_helpers_netframework")
from _helpers_netframework import (
    get_member, pt_xyz, wkt_line, wkt_point, resolve_handle,
    crossing_angle_deg, MIN_CROSSING_ANGLE_DEG,
)
unload_package("_duckdb_engine")
unload_package("duckdb")
import _duckdb_engine as duck

import pyarrow as pa
# --- end imports ------------------------------------------------------------

from Autodesk.AutoCAD.DatabaseServices import OpenMode

# # NOTE: in your repo, merge _helpers_netframework into automations/_helpers and
# # import from there. Kept explicit here for clarity.
# from exercises._helpers_netframework import (
#     get_member, pt_xyz, wkt_line, wkt_point, resolve_handle,
#     crossing_angle_deg, MIN_CROSSING_ANGLE_DEG,
# )
# from exercises import _duckdb_engine as duck

# from exercises._helpers import _opt_float, normalize_name_list


def _extract(civdoc, tr, want, missing, skipped):
    """Read pipes + structures into flat primitive rows using the OPEN tr."""
    pipes, structures, connections, used = [], [], [], []
    want_lc = set(n.lower() for n in want)

    for nid in civdoc.GetPipeNetworkIds():
        net = tr.GetObject(nid, OpenMode.ForRead)
        nname = get_member(net, "Name", str, "", missing)
        if want_lc and nname.strip().lower() not in want_lc:
            continue
        used.append(nname)

        for sid in net.GetStructureIds():
            try:
                s = tr.GetObject(sid, OpenMode.ForRead)
                x, y, z = pt_xyz(get_member(s, "Position", missing=missing))
                structures.append({
                    "handle": s.Handle.ToString(),
                    "name": get_member(s, "Name", str, None, missing),
                    "part_type": str(get_member(s, "PartType", default="", missing=missing)),
                    "x": x, "y": y,
                    "rim_z": get_member(s, "RimElevation", float, None, missing),
                    "sump_z": get_member(s, "SumpElevation", float, None, missing),
                    "network": nname, "wkt": wkt_point(x, y),
                })
            except Exception as e:
                skipped.append({"structure": str(sid), "reason": str(e)})
                
        for pid in net.GetPipeIds():
            try:
                p = tr.GetObject(pid, OpenMode.ForRead)
                sx, sy, sz = pt_xyz(get_member(p, "StartPoint", missing=missing))
                ex, ey, ez = pt_xyz(get_member(p, "EndPoint", missing=missing))
                start_h = resolve_handle(tr, get_member(p, "StartStructureId", missing=missing))
                end_h = resolve_handle(tr, get_member(p, "EndStructureId", missing=missing))
                dia = get_member(p, "InnerDiameterOrWidth", float, None, missing)
                if dia is None:
                    dia = get_member(p, "InnerDiameter", float, None, missing)
                l2d = get_member(p, "Length2DCenterToCenter", float, None, missing)
                if l2d is None:
                    l2d = get_member(p, "Length2DToInsideEdge", float, None, missing)
                handle = p.Handle.ToString()
                pipes.append({
                    "handle": handle, "name": get_member(p, "Name", str, None, missing),
                    "start_handle": start_h, "end_handle": end_h,
                    "start_x": sx, "start_y": sy, "start_z": sz,
                    "end_x": ex, "end_y": ey, "end_z": ez,
                    "diameter": dia, "slope": get_member(p, "Slope", float, None, missing),
                    "length2d": l2d, "network": nname, "wkt": wkt_line(sx, sy, ex, ey),
                })
                if start_h:
                    connections.append({"pipe_handle": handle, "structure_handle": start_h, "end_type": "start"})
                if end_h:
                    connections.append({"pipe_handle": handle, "structure_handle": end_h, "end_type": "end"})
            except Exception as e:
                skipped.append({"pipe": str(pid), "reason": str(e)})

    return {"pipes": pipes, "structures": structures,
            "connections": connections, "_networks": used}


def run(context):
    """Detect inter-network pipe crossings and classify clash vs clearance.
    Receives an already-open transaction; returns the Data payload."""
    civdoc = context["civdoc"]
    tr = context["tr"]
    IN = context["IN"]

    data = {"Warnings": [], "Skipped": [], "Items": []}
    missing = set()

    try:
        want = normalize_name_list(IN[0] if len(IN) > 0 else None)
        clearance = _opt_float(IN, 1, 0.3)
        duck_path = IN[2] if len(IN) > 2 and IN[2] else None

        # PHASE 1: extract (read, uses the open tr)
        extract = _extract(civdoc, tr, want, missing, data["Skipped"])

        if missing:
            data["Warnings"].append("Unresolved member names (fix spellings): "
                                    + ", ".join(sorted(missing)))
        if not extract["pipes"]:
            data["Warnings"].append("No pipes found in selected networks.")
            return data

        # # PHASE 2: DuckDB analyze
        con = duck.connect(duck_path)
        duck.load(con, extract)
        rows = duck.crossings(con, clearance=clearance)

        # # PHASE 3: geometric guards (reject near-parallel/glancing crossings)
        for c in rows:
            ang = crossing_angle_deg(c["ax1"], c["ay1"], c["ax2"], c["ay2"],
                                     c["bx1"], c["by1"], c["bx2"], c["by2"])
            if ang < MIN_CROSSING_ANGLE_DEG:
                data["Skipped"].append({"pair": (c["pipe_a"], c["pipe_b"]),
                                        "reason": "crossing angle %.1f deg < %.1f"
                                        % (ang, MIN_CROSSING_ANGLE_DEG)})
                continue
            data["Items"].append({
                "pipe_a": c["pipe_a"], "net_a": c["net_a"],
                "pipe_b": c["pipe_b"], "net_b": c["net_b"],
                "cross_x": round(c["cross_x"], 4), "cross_y": round(c["cross_y"], 4),
                "z_a": round(c["z_a"], 4), "z_b": round(c["z_b"], 4),
                "dz": round(c["dz"], 4), "angle_deg": round(ang, 2),
                "verdict": c["verdict"],
            })

        data["Networks"] = extract["_networks"]
        data["Counts"] = {"pipes": len(extract["pipes"]),
                          "structures": len(extract["structures"]),
                          "crossings": len(data["Items"]),
                          "clashes": sum(1 for i in data["Items"] if i["verdict"] == "CLASH")}
    except Exception as e:
        data["Warnings"].append(str(e))
        data["Warnings"].append(traceback.format_exc())

    return data