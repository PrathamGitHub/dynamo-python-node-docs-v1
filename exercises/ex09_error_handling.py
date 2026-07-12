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
# import sys
# from pathlib import Path
# sys.path.append(str(Path(__file__).resolve().parent))

# from _helpers import unload_package
# unload_package("_helpers")
# from _helpers import (
# _opt_str, _opt_int, _opt_float, normalize_name_list, _ensure_layer, cleanup, get_style_id_or_first, station_offset, point_location, endpoint_on_alignment, unload_package)

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

    data = {"Warnings": [], "Skipped": [], "Items": []}

    try:
        # ---------------------------------------------------------------
        net_ids = list(civdoc.GetPipeNetworkIds())
        if not net_ids:
            raise Exception("No pipe network in drawing.")   # FATAL -> raise
        net = tr.GetObject(net_ids[1], OpenMode.ForRead)
        processed = 0
        for sid in net.GetStructureIds():
            name = "<unknown>"
            try:
                s = tr.GetObject(sid, OpenMode.ForRead)
                name = getattr(s, "Name", "<unnamed>")
                pos = s.Position                  # per-item fallible call
                _ = (pos.X, pos.Y, pos.Z)
                processed += 1
            except Exception as e:                # NARROW: per item, per step
                data["Skipped"].append(
                    f"{name}: read position failed: {e.__class__.__name__}")
                continue
        total = processed + len(data["Skipped"])
        data["Items"].append({"net_name": net.Name, "Processed": processed, "Skipped": data["Skipped"], "Total": total})
        # ---------------------------------------------------------------

    except Exception as e:
        data["Warnings"].append(str(e))
        data["Warnings"].append(traceback.format_exc())

    return data                    # becomes results["Data"] in the node