# Uncomment the imports you need

# from Autodesk.AutoCAD.ApplicationServices.Core import Application
# from Autodesk.Civil.ApplicationServices import CivilApplication
# from Autodesk.AutoCAD.DatabaseServices import (
#     OpenMode, ObjectId, Polyline, SymbolUtilityServices, LayerTable, LayerTableRecord)
# from Autodesk.AutoCAD.Geometry import Point2d, Point3d
# from Autodesk.Civil.DatabaseServices import Alignment, PolylineOptions
# from Autodesk.AutoCAD.Colors import Color

# --- imports ----------------------------------------------------------------
import traceback
from Autodesk.AutoCAD.DatabaseServices import (OpenMode, ObjectId, Polyline, SymbolUtilityServices)
from Autodesk.AutoCAD.Geometry import Point2d
# --- force-reload the WHOLE package so nested helper edits are picked up ------
# import sys
# from pathlib import Path
# sys.path.append(str(Path(__file__).resolve().parent))

# def unload_package(package_name):
#     for name in list(sys.modules.keys()):
#         if name == package_name or name.startswith(package_name + "."):
#             del sys.modules[name]
# unload_package("_helpers")
# from _helpers import _ensure_layer

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
        ms = tr.GetObject(SymbolUtilityServices.GetBlockModelSpaceId(db), OpenMode.ForRead)
        target = None
        for oid in ms:
            ent = tr.GetObject(oid, OpenMode.ForRead)
            if getattr(ent, "Layer", "") == "DEV-SCRATCH" and isinstance(ent, Polyline):
                target = ent; break
        if target is None:
            data["Warnings"].append("No DEV-SCRATCH polyline; run Ex 3 first.")
        else:
            old = (target.GetPoint2dAt(1).X, target.GetPoint2dAt(1).Y)
            target.UpgradeOpen()                     # ForRead -> ForWrite
            target.SetPointAt(1, Point2d(80.0, 40.0))
            new = (target.GetPoint2dAt(1).X, target.GetPoint2dAt(1).Y)
            data["Items"].append({"Old": old, "New": new})
        # ---------------------------------------------------------------

    except Exception as e:
        data["Warnings"].append(str(e))
        data["Warnings"].append(traceback.format_exc())

    return data                    # becomes results["Data"] in the node