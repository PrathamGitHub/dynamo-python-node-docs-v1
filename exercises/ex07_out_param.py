# Uncomment the imports you need

# from Autodesk.AutoCAD.ApplicationServices.Core import Application
# from Autodesk.Civil.ApplicationServices import CivilApplication
# from Autodesk.AutoCAD.DatabaseServices import (
#     OpenMode, ObjectId, Polyline, SymbolUtilityServices, LayerTable, LayerTableRecord)
# from Autodesk.AutoCAD.Geometry import Point2d, Point3d
# from Autodesk.Civil.DatabaseServices import Alignment, PolylineOptions
# from Autodesk.AutoCAD.Colors import Color

# --- imports ----------------------------------------------------------------
from Autodesk.AutoCAD.DatabaseServices import (OpenMode)
import traceback

# --- force-reload the WHOLE package so nested helper edits are picked up ------
import sys
from pathlib import Path
sys.path.append(str(Path(__file__).resolve().parent))

def unload_package(package_name):
    for name in list(sys.modules.keys()):
        if name == package_name or name.startswith(package_name + "."):
            del sys.modules[name]
unload_package("_helpers")
from _helpers import endpoint_on_alignment, _opt_float, point_location

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
        aln_ids = list(civdoc.GetAlignmentIds())
        if not aln_ids:
            data["Warnings"].append("No alignment in drawing.")
        else:
            aln = tr.GetObject(aln_ids[0], OpenMode.ForRead)
            st = _opt_float(IN, 0, 0.0)
            off = _opt_float(IN, 1, 0.0)
            x, y = point_location(aln, st, off)

            is_on_align = endpoint_on_alignment(aln, x, y, 0.15)
            data["Items"].append({"IsOnAlign": is_on_align})
        # ---------------------------------------------------------------

    except Exception as e:
        data["Warnings"].append(traceback.format_exc())
        data["Warnings"].append(str(e))

    return data                    # becomes results["Data"] in the node