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

# --- force-reload the WHOLE package so nested helper edits are picked up ------
import sys
from pathlib import Path
sys.path.append(str(Path(__file__).resolve().parent))

def unload_package(package_name):
    for name in list(sys.modules.keys()):
        if name == package_name or name.startswith(package_name + "."):
            del sys.modules[name]
unload_package("_helpers")
from _helpers import _opt_str, get_style_id_or_first

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
        w = data["Warnings"]
        _, a = get_style_id_or_first(civdoc.Styles.AlignmentStyles,
                                    _opt_str(IN, 0, ""), w, "Alignment Style")
        _, b = get_style_id_or_first(civdoc.Styles.ProfileViewStyles,
                                    _opt_str(IN, 1, "___bogus___"), w, "Profile View Style")
        data["Items"].append({"AlignmentStyle": a, "ProfileViewStyle": b})
        # ---------------------------------------------------------------

    except Exception as e:
        data["Warnings"].append(str(e))
        data["Warnings"].append(traceback.format_exc())

    return data                    # becomes results["Data"] in the node