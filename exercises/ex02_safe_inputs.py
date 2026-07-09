# Uncomment the imports you need

# from Autodesk.AutoCAD.ApplicationServices.Core import Application
# from Autodesk.Civil.ApplicationServices import CivilApplication
# from Autodesk.AutoCAD.DatabaseServices import (
#     OpenMode, ObjectId, Polyline, SymbolUtilityServices, LayerTableRecord)
# from Autodesk.AutoCAD.Geometry import Point2d, Point3d
# from Autodesk.Civil.DatabaseServices import Alignment, PolylineOptions

import sys
sys.path.append(r"..")
from _helpers import _opt_str, _opt_int, _opt_float, normalize_name_list

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

    # ---
    name = _opt_str(IN, 0, "")
    if not name: data["Warnings"].append('Network name missing; default "".')
    data["Items"] = [
        name,
        _opt_str(IN, 1, "IC-"),
        _opt_float(IN, 2, 0.15),
        normalize_name_list(IN[3] if len(IN) > 3 else None),  # stretch
    ]

    return data                    # becomes results["Data"] in the node