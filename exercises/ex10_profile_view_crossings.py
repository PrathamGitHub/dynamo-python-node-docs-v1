# Uncomment the imports you need

from Autodesk.AutoCAD.ApplicationServices.Core import Application
from Autodesk.Civil.ApplicationServices import CivilApplication
from Autodesk.AutoCAD.DatabaseServices import (
    OpenMode, ObjectId, Polyline, SymbolUtilityServices, LayerTable, LayerTableRecord)
from Autodesk.AutoCAD.Geometry import Point2d, Point3d
from Autodesk.Civil.DatabaseServices import Alignment, PolylineOptions
from Autodesk.AutoCAD.Colors import Color

# --- imports ----------------------------------------------------------------
import traceback

# --- force-reload the WHOLE package so nested helper edits are picked up ------
import sys
from pathlib import Path
sys.path.append(str(Path(__file__).resolve().parent))

from _helpers import unload_package
unload_package("_helpers")
from _helpers import (
_opt_str, _opt_int, _opt_float, normalize_name_list, _ensure_layer, cleanup, get_style_id_or_first, build_unique_name, _pt_of, station_offset, point_location, endpoint_on_alignment, unload_package)

# --- end imports ------------------------------------------------------------

def run(context):
    """Receives an already-open transaction + doc context. Returns the Data payload.
    Does NOT lock, start a transaction, or commit — the node owns that."""
    doc    = context["doc"]
    db     = context["db"]
    ed     = context["ed"]
    civdoc = context["civdoc"]
    tr     = context["tr"]        # <-- already open; just use it
    IN     = context["IN"]

    data = {"Created": 0, "Skipped": [], "Warnings": []}
    net_name   = _opt_str(IN, 0, "")
    ic_prefix  = _opt_str(IN, 1, "IC-")
    style_name = _opt_str(IN, 2, "")
    test_limit = _opt_int(IN, 3, 0)


    try:
        # ---------------------------------------------------------------
        # Your logic here, using tr.GetObject(...), etc.
        arget = None
        for oid in civdoc.GetPipeNetworkIds():
            n = tr.GetObject(oid, OpenMode.ForRead)
            if getattr(n, "Name", "") == net_name and hasattr(n, "GetStructureIds"):
                target = n; break
        if target is None:
            raise Exception(f'Pipe Network "{net_name}" not found.')     # FATAL -> node catches

        style_id, resolved = get_style_id_or_first(
            civdoc.Styles.AlignmentStyles, style_name, data["Warnings"], "Alignment Style")
        data["StyleUsed"] = [resolved]

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
        if test_limit > 0:
            ic_ids = ic_ids[:test_limit]

        ms = tr.GetObject(SymbolUtilityServices.GetBlockModelSpaceId(db), OpenMode.ForWrite)
        existing = set()

        # --- loop ---
        for sid in ic_ids:
            sname = getattr(tr.GetObject(sid, OpenMode.ForRead), "Name", "IC")
            pipes = conn.get(sid, [])
            if not pipes:
                data["Skipped"].append(f"{sname} (no connected pipe)"); continue
            pid, st_id, en_id = pipes[0]
            sp, ep = _pt_of(tr, st_id), _pt_of(tr, en_id)
            if sp is None or ep is None:
                data["Skipped"].append(f"{sname} (no coordinates)"); continue
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
                coll = civdoc.Styles.LabelSetStyles.AlignmentLabelSetStyles
                labelset_id, _ = get_style_id_or_first(coll, None, [], "Alignment label set style")
                Alignment.Create(
                    civdoc, plops, aln_name,
                    ObjectId.Null,               # SITE_ID = no site
                    db.LayerZero,                # or a resolved layer id
                    style_id,
                    labelset_id)              
                data["Created"] += 1
            except Exception as e:
                data["Skipped"].append(f"{sname} (create alignment failed: {e.__class__.__name__})") 
                data["Warnings"].append(str(e))
                data["Warnings"].append(traceback.format_exc())
                continue
        # ---------------------------------------------------------------

    except Exception as e:
        data["Warnings"].append(str(e))
        data["Warnings"].append(traceback.format_exc())

    return data                    # becomes results["Data"] in the node