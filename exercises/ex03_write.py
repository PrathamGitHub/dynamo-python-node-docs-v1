# Uncomment the imports you need

# from Autodesk.AutoCAD.ApplicationServices.Core import Application
# from Autodesk.Civil.ApplicationServices import CivilApplication
# from Autodesk.AutoCAD.DatabaseServices import (
#     OpenMode, ObjectId, Polyline, SymbolUtilityServices, LayerTable, LayerTableRecord)
# from Autodesk.AutoCAD.Geometry import Point2d, Point3d
# from Autodesk.Civil.DatabaseServices import Alignment, PolylineOptions
# from Autodesk.AutoCAD.Colors import Color

# --- imports ----------------------------------------------------------------
from Autodesk.AutoCAD.Geometry import Point2d
from Autodesk.AutoCAD.DatabaseServices import (
    OpenMode, ObjectId, Polyline, LayerTableRecord, SymbolUtilityServices)

from Autodesk.AutoCAD.Colors import Color, ColorMethod

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
from _helpers import _ensure_layer

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

    # ---------------------------------------------------------------
    try:
        data["Items"].append(_ensure_layer(tr, db, "DEV-SCRATCH"))

        ms = tr.GetObject(SymbolUtilityServices.GetBlockModelSpaceId(db), OpenMode.ForWrite)
        pl = Polyline()
        pl.AddVertexAt(0, Point2d(0.0, 0.0),  0.0, 0.0, 0.0)
        pl.AddVertexAt(1, Point2d(50.0, 20.0), 0.0, 0.0, 0.0)
        pl.Layer = "DEV-SCRATCH"
        pid = ms.AppendEntity(pl)
        tr.AddNewlyCreatedDBObject(pl, True) 

        data["Items"].append(f"Polyline {str(pid)} created")
    except Exception as e:
        data["Warnings"].append(str(e))
        data["Warnings"].append(traceback.format_exc())

    # ---------------------------------------------------------------

    return data                    # becomes results["Data"] in the node