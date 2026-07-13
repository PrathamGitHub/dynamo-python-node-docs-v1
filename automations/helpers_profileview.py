from Autodesk.AutoCAD.DatabaseServices import ObjectId
from Autodesk.AutoCAD.Geometry import Point3d
from Autodesk.Civil.DatabaseServices import ProfileView
from Autodesk.Civil import BandType


class GridPlacer:
    """Lays profile views out left-to-right, top-to-bottom on a grid.
    Civil 3D auto-sizes the PV; the width/height here only drive spacing."""
    def __init__(self, base_x, base_y, columns=5,
                 spacing_x=25.0, spacing_y=40.0):
        self.base_x = base_x
        self.columns = max(1, int(columns))
        self.spacing_x = spacing_x
        self.spacing_y = spacing_y
        self.col = 0
        self.x = base_x
        self.y = base_y
        self.row_h = 0.0

    def current(self):
        """The insertion point for the PV about to be created (x, y, 0)."""
        return (self.x, self.y, 0.0)

    def advance(self, pv_w, pv_h):
        """Move the cursor after placing a PV of size pv_w x pv_h."""
        self.row_h = max(self.row_h, pv_h)
        self.col += 1
        if self.col >= self.columns:
            self.col = 0
            self.x = self.base_x
            self.y = self.y - (self.row_h + self.spacing_y)   # next row, downward
            self.row_h = 0.0
        else:
            self.x = self.x + (pv_w + self.spacing_x)          # next column, rightward


def create_profile_view_unique(aln_id, insert_pt, bandset_id, pv_style_id,
                               base_name):
    """Create a ProfileView with a base name. If the name is already in use, 
    create_profile_view_unique_race_condition_workaround is called to 
    create a new name with a suffix until a unique name is found."""
    pt = Point3d(insert_pt[0], insert_pt[1], insert_pt[2])
    try:
        pv_id = ProfileView.Create(aln_id, pt, base_name, bandset_id, pv_style_id)
        return pv_id, base_name
    except Exception as e:
        if "duplicat" in str(e).lower():      # matches 'duplicate' / 'duplicated'
            return create_profile_view_unique_race_condition_workaround(aln_id, insert_pt, bandset_id, pv_style_id, f"{base_name}")
        raise                                 # a real error -> surface it
        

def create_profile_view_unique_race_condition_workaround(aln_id, insert_pt, bandset_id, pv_style_id,
                               base_name, max_tries=5000):
    """Create a ProfileView, retrying with an integer suffix on duplicate names.
    insert_pt is an (x, y, z) tuple. Returns (pv_id, resolved_name).
    Re-raises any exception that is NOT a duplicate-name error."""
    pt = Point3d(insert_pt[0], insert_pt[1], insert_pt[2])
    for i in range(max_tries):
        name = base_name if i == 0 else f"{base_name} ({i+1})"
        try:
            pv_id = ProfileView.Create(aln_id, pt, name, bandset_id, pv_style_id)
            return pv_id, name
        except Exception as e:
            if "duplicat" in str(e).lower():      # matches 'duplicate' / 'duplicated'
                continue
            raise                                 # a real error -> surface it
    raise Exception(f"Could not find a unique Profile View name from '{base_name}'.")

    
def set_band_inputs(pv, datasource_id, surface_profile_id, warnings):
    """Connect the PV's bands to their data sources and enable labels.
    Applies to both bottom and top band items (templates vary). Null ids are
    skipped, so a PV with no surface still gets its pipe bands wired."""
    def apply(items):
        changed = False
        for item in items:
            try:
                bt = item.BandType
                if (bt in (BandType.PipeNetwork, BandType.SectionalData)
                        and datasource_id != ObjectId.Null):
                    item.DataSourceId = datasource_id
                    item.ShowLabels = True
                    changed = True
                if bt == BandType.ProfileData and surface_profile_id != ObjectId.Null:
                    item.Profile1Id = surface_profile_id
                    item.Profile2Id = surface_profile_id
                    item.ShowLabels = True
                    changed = True
            except Exception as e:
                warnings.append(f"band wire failed ({bt}): {e}")
        return changed

    for get, set_ in (("GetBottomBandItems", "SetBottomBandItems"),
                      ("GetTopBandItems", "SetTopBandItems")):
        try:
            items = getattr(pv.Bands, get)()
            if apply(items):
                getattr(pv.Bands, set_)(items)      # write-back is REQUIRED
        except Exception:
            pass