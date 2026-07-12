from Autodesk.AutoCAD.DatabaseServices import OpenMode, Polyline
from Autodesk.AutoCAD.Geometry import Point2d
from Autodesk.AutoCAD.DatabaseServices import ObjectId
from Autodesk.Civil.DatabaseServices import Alignment, PolylineOptions, Profile



def create_alignment_from_points(civdoc, tr, ms, sp, ep, name,
                                 layer_id, style_id, labelset_id,
                                 site_id=None):
    """Create a 2-vertex alignment from world points sp -> ep.
      - seeds a temporary AutoCAD Polyline (Civil 3D consumes it and, with
        EraseExistingEntities=True, deletes the seed afterwards).
      - siteId accepts ObjectId.Null (siteless); style_id + labelset_id MUST be
        real ids (resolved + unwrapped upstream).
    Returns the alignment ObjectId."""
    site_id = ObjectId.Null if site_id is None else site_id

    pl = Polyline()
    pl.AddVertexAt(0, Point2d(sp[0], sp[1]), 0.0, 0.0, 0.0)
    pl.AddVertexAt(1, Point2d(ep[0], ep[1]), 0.0, 0.0, 0.0)
    pl.LayerId = layer_id
    pl_id = ms.AppendEntity(pl)
    tr.AddNewlyCreatedDBObject(pl, True)

    plops = PolylineOptions()
    plops.PlineId = pl_id
    plops.AddCurvesBetweenTangents = False       # two-vertex run: no curve fitting
    plops.EraseExistingEntities = True           # Civil 3D deletes the seed polyline

    return Alignment.Create(civdoc, plops, name, site_id,
                            layer_id, style_id, labelset_id)


def create_eg_profile(alignment_id, surface_id, aln_layer_id,
                      profile_style_id, profile_labelset_id, name):
    """Sample an existing-ground profile from a surface along the alignment.
    Returns the profile ObjectId, or ObjectId.Null if there's no surface."""
    if surface_id == ObjectId.Null:
        return ObjectId.Null
    return Profile.CreateFromSurface(name, alignment_id, surface_id,
                                     aln_layer_id, profile_style_id,
                                     profile_labelset_id)