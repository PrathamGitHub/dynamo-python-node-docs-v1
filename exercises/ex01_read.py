# from Autodesk.AutoCAD.ApplicationServices.Core import Application
# from Autodesk.Civil.ApplicationServices import CivilApplication
# from Autodesk.AutoCAD.DatabaseServices import (
#     OpenMode, ObjectId, Polyline, SymbolUtilityServices, LayerTableRecord)
# from Autodesk.AutoCAD.Geometry import Point2d, Point3d
# from Autodesk.Civil.DatabaseServices import Alignment, PolylineOptions

from typing import Any


from Autodesk.AutoCAD.DatabaseServices import (OpenMode, ObjectId)

def run(context):
    """Receives an already-open transaction + doc context. Returns the Data payload.
    Does NOT lock, start a transaction, or commit — the node owns that."""
    doc    = context["doc"]
    db     = context["db"]
    ed     = context["ed"]
    civdoc = context["civdoc"]
    tr     = context["tr"]        # <-- already open; just use it
    IN     = context["IN"]

    data = {"Warnings": [], "Skipped": [], "Items": [""]}

    # Return: the drawing name, the count of pipe networks (civdoc.GetPipeNetworkIds()), and the count of alignments (civdoc.GetAlignmentIds())
    net_ids = list(civdoc.GetPipeNetworkIds())
    aln_ids = list[ObjectId](civdoc.GetAlignmentIds())
    names = [getattr(tr.GetObject(o, OpenMode.ForRead), "Name", "<unnamed>")
                for o in net_ids]
    data["Items"] = {
        "Drawing": db.Filename,
        "NetworkCount": len(net_ids),
        "AlignmentCount": len(aln_ids),
        "NetworkNames": sorted(names),        # stretch goal
    }
    return data                    # becomes results["Data"] in the node