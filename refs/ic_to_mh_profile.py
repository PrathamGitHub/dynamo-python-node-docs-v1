
import os
import csv
import clr
import System

# AutoCAD/Civil3D .NET assemblies
clr.AddReference("AcMgd")
clr.AddReference("AcDbMgd")
clr.AddReference("AeccDbMgd")

# Pressure pipes assembly (needed for pressure networks)
try:
    clr.AddReference("AeccPressurePipesMgd")
    HAS_PRESSURE = True
except:
    HAS_PRESSURE = False

from Autodesk.AutoCAD.ApplicationServices.Core import Application
from Autodesk.AutoCAD.DatabaseServices import (
    OpenMode, Polyline, Line, SymbolUtilityServices, LayerTableRecord,
    ObjectId, Intersect
)
from Autodesk.AutoCAD.Geometry import (
    Point2d, Point3d, Vector3d, Plane, Point3dCollection
)

from Autodesk.Civil.ApplicationServices import CivilApplication
from Autodesk.Civil.DatabaseServices import (
    Alignment, PolylineOptions, AlignmentType,
    Profile, ProfileView
)
from Autodesk.Civil import BandType

# Pressure pipes extension (only if available)
if HAS_PRESSURE:
    from Autodesk.Civil.ApplicationServices import CivilDocumentPressurePipesExtension

# -----------------------------
# INPUTS
# -----------------------------
network_name = IN[0]
ic_prefix = IN[1]
out_path = IN[2] if len(IN) > 2 else None

def _opt_str(i, default_value=""):
    try:
        if len(IN) > i and IN[i] is not None:
            s = str(IN[i]).strip()
            return s if s else default_value
    except:
        pass
    return default_value

def _opt_int(i, default_value):
    try:
        if len(IN) > i and IN[i] is not None:
            return int(IN[i])
    except:
        pass
    return default_value

def _opt_float(i, default_value):
    try:
        if len(IN) > i and IN[i] is not None:
            return float(IN[i])
    except:
        pass
    return default_value

# Optional alignment inputs
DESIRED_ALIGNMENT_STYLE = _opt_str(3, "ProposedAlignment")
DESIRED_ALIGNMENT_LABELSET = _opt_str(4, "No Labels")

# Profile/ProfileView inputs
SURFACE_NAME = _opt_str(5, "")
PROFILEVIEW_STYLE_NAME = _opt_str(6, "")
BANDSET_NAME = _opt_str(7, "")
BAND_DATASOURCE_NAME = _opt_str(8, "")
COLUMNS = max(1, _opt_int(9, 3))

# Crossing inputs (lists)
RAW_GRAVITY_CROSS_LIST = IN[10] if len(IN) > 10 else []
RAW_PRESSURE_CROSS_LIST = IN[11] if len(IN) > 11 else []

# IN[12] = ON_ALIGN_TOL (meters). Default 0.01m = 1 cm
ON_ALIGN_TOL = abs(_opt_float(12, 0.01))

# Settings
SITE_ID = ObjectId.Null
TEMP_LAYER = "DYN_TEMP"

# Placement/layout
MARGIN_X = 50.0
MARGIN_Y = 50.0
SPACING_X = 25.0
SPACING_Y = 40.0

# Profile view size estimate (stable)
MAX_PV_WIDTH = 1200.0
MIN_PV_WIDTH = 250.0
PV_HEIGHT_DEFAULT = 250.0

doc = Application.DocumentManager.MdiActiveDocument
db = doc.Database
civdoc = CivilApplication.ActiveDocument

# -----------------------------
# HELPERS
# -----------------------------
def normalize_name_list(x):
    """Accept list or single string; return unique cleaned list (case-insensitive)."""
    names = []
    if x is None:
        return names
    if isinstance(x, str):
        x = [x]
    try:
        for item in x:
            if item is None:
                continue
            s = str(item).strip()
            if s:
                names.append(s)
    except:
        s = str(x).strip()
        if s:
            names.append(s)

    seen = set()
    out = []
    for n in names:
        k = n.lower()
        if k not in seen:
            seen.add(k)
            out.append(n)
    return out

GRAVITY_CROSS_NET_NAMES = normalize_name_list(RAW_GRAVITY_CROSS_LIST)
PRESSURE_CROSS_NET_NAMES = normalize_name_list(RAW_PRESSURE_CROSS_LIST)

def resolve_csv_path(path_in):
    if not path_in:
        temp_dir = os.environ.get("TEMP", r"C:\Temp")
        return os.path.join(temp_dir, "YCC_InspectionChambers.csv")
    path_in = str(path_in)
    if path_in.lower().endswith(".csv"):
        return path_in
    return os.path.join(path_in, "YCC_InspectionChambers.csv")

def try_get_point3d(obj):
    for attr in ("Position", "Location", "InsertionPoint", "Point"):
        if hasattr(obj, attr):
            try:
                pt = getattr(obj, attr)
                if hasattr(pt, "X") and hasattr(pt, "Y") and hasattr(pt, "Z"):
                    return pt
            except:
                pass
    return None

def get_pipe_end_structure_ids(pipe_obj):
    for a, b in (("StartStructureId", "EndStructureId"),
                 ("StartStructure", "EndStructure")):
        if hasattr(pipe_obj, a) and hasattr(pipe_obj, b):
            try:
                sv = getattr(pipe_obj, a)
                ev = getattr(pipe_obj, b)
                if hasattr(sv, "ObjectId"): sv = sv.ObjectId
                if hasattr(ev, "ObjectId"): ev = ev.ObjectId
                return sv, ev
            except:
                pass
    return None, None

def get_pipe_points(pipe_obj):
    sp = ep = None
    if hasattr(pipe_obj, "StartPoint"):
        try: sp = pipe_obj.StartPoint
        except: sp = None
    if hasattr(pipe_obj, "EndPoint"):
        try: ep = pipe_obj.EndPoint
        except: ep = None
    return sp, ep

def build_unique_name(existing_set, base):
    if base not in existing_set:
        existing_set.add(base)
        return base
    i = 1
    while True:
        cand = f"{base} {i}"
        if cand not in existing_set:
            existing_set.add(cand)
            return cand
        i += 1

def ensure_layer(tr, layer_name):
    lt = tr.GetObject(db.LayerTableId, OpenMode.ForRead)
    for lid in lt:
        ltr = tr.GetObject(lid, OpenMode.ForRead)
        if ltr.Name.lower() == layer_name.lower():
            if ltr.IsLocked:
                ltrw = tr.GetObject(lid, OpenMode.ForWrite)
                ltrw.IsLocked = False
            return lid
    lt.UpgradeOpen()
    ltr_new = LayerTableRecord()
    ltr_new.Name = layer_name
    ltr_new.IsLocked = False
    new_id = lt.Add(ltr_new)
    tr.AddNewlyCreatedDBObject(ltr_new, True)
    return new_id

def get_style_id_by_name_or_first(style_coll, desired_name, warnings, kind):
    try:
        ids = list(style_coll.ToObjectIds())
    except:
        ids = []
    if not ids:
        raise Exception(f"No {kind} found in drawing. Import styles from template.")
    if desired_name:
        try:
            if style_coll.Contains(desired_name):
                return style_coll.get_Item(desired_name), desired_name
        except:
            pass
        warnings.append(f'{kind} "{desired_name}" not found. Using first available.')
    return ids[0], "<FirstAvailable>"

def find_surface_id_by_name(tr, surface_name):
    if not surface_name:
        return ObjectId.Null
    for sid in civdoc.GetSurfaceIds():
        s = tr.GetObject(sid, OpenMode.ForRead)
        nm = getattr(s, "Name", "")
        if str(nm).strip().lower() == surface_name.strip().lower():
            return sid
    return ObjectId.Null

def compute_network_extents(tr, net):
    xs, ys = [], []
    for sid in net.GetStructureIds():
        s = tr.GetObject(sid, OpenMode.ForRead)
        pt = try_get_point3d(s)
        if pt:
            xs.append(pt.X); ys.append(pt.Y)
    for pid in net.GetPipeIds():
        p = tr.GetObject(pid, OpenMode.ForRead)
        sp, ep = get_pipe_points(p)
        if sp:
            xs.append(sp.X); ys.append(sp.Y)
        if ep:
            xs.append(ep.X); ys.append(ep.Y)
    if not xs:
        return (0.0, 0.0, 0.0, 0.0)
    return (min(xs), min(ys), max(xs), max(ys))

def add_parts_to_profile_view(tr, ids_to_add, pv_id, warnings):
    for oid in ids_to_add:
        try:
            part = tr.GetObject(oid, OpenMode.ForWrite)
            if hasattr(part, "AddToProfileView"):
                part.AddToProfileView(pv_id)
        except Exception as e:
            warnings.append(f"AddToProfileView failed for {str(oid)}: {str(e)}")

def add_pressure_pipes_to_profile_view(tr, pressure_pipe_ids, pv_id, warnings):
    for oid in pressure_pipe_ids:
        try:
            ppart = tr.GetObject(oid, OpenMode.ForWrite)
            if hasattr(ppart, "AddToProfileView"):
                ppart.AddToProfileView(pv_id)
        except Exception as e:
            warnings.append(f"Pressure pipe AddToProfileView failed for {str(oid)}: {str(e)}")

def set_band_inputs(pv, datasource_id, surface_profile_id, warnings):
    def _apply(items):
        changed = False
        for item in items:
            try:
                bt = item.BandType
                if (bt == BandType.PipeNetwork or bt == BandType.SectionalData) and datasource_id != ObjectId.Null:
                    item.DataSourceId = datasource_id
                    item.ShowLabels = True
                    changed = True
                if bt == BandType.ProfileData and surface_profile_id != ObjectId.Null:
                    item.Profile1Id = surface_profile_id
                    item.Profile2Id = surface_profile_id
                    item.ShowLabels = True
                    changed = True
            except:
                pass
        return changed

    try:
        bottom = pv.Bands.GetBottomBandItems()
        if _apply(bottom):
            pv.Bands.SetBottomBandItems(bottom)
    except:
        pass
    try:
        top = pv.Bands.GetTopBandItems()
        if _apply(top):
            pv.Bands.SetTopBandItems(top)
    except:
        pass

# --------- Robust ProfileView creation (retries on duplicates) ----------
def create_profile_view_unique(aln_id, insert_pt, bandset_id, pv_style_id, base_name):
    """
    Robust: retries even if we cannot reliably list existing PV names.
    Duplicate PV name throws ArgumentException per API docs. 
    """
    for i in range(0, 5000):
        name = base_name if i == 0 else f"{base_name} ({i})"
        try:
            pv_id = ProfileView.Create(aln_id, insert_pt, name, bandset_id, pv_style_id)
            return pv_id, name
        except Exception as e:
            msg = str(e).lower()
            if "duplicate" in msg or "duplicated" in msg:
                continue
            raise
    raise Exception("Could not generate a unique Profile View name after many attempts.")

# -------- Intersection-based crossing logic --------
XY_PLANE = Plane(Point3d(0, 0, 0), Vector3d.ZAxis)

def line_intersects_alignment_poly(aln_pline, p1, p2):
    try:
        seg = Line(Point3d(p1.X, p1.Y, 0.0), Point3d(p2.X, p2.Y, 0.0))
        pts = Point3dCollection()
        aln_pline.IntersectWith(seg, Intersect.ExtendNone, XY_PLANE, pts, System.IntPtr.Zero, System.IntPtr.Zero)
        return pts.Count > 0
    except:
        return False

def station_offset(aln, x, y):
    # pythonnet (CPython 3) has no clr.Reference. For `void StationOffset(
    # x, y, out double station, out double offset)` we pass dummy Doubles for
    # the two out params; their type drives overload resolution and the real
    # values come back as a return tuple (station, offset).
    st = 0.0
    off = 0.0
    _, st, off = aln.StationOffset(x, y, st, off)
    return st, off

def endpoint_on_alignment(aln, pt, tol):
    try:
        _, off = station_offset(aln, pt.X, pt.Y)
        return abs(off) <= tol
    except:
        return False

def is_pipe_crossing(aln, aln_pline, sp, ep, tol_on_align):
    if sp is None or ep is None:
        return False
    if not line_intersects_alignment_poly(aln_pline, sp, ep):
        return False
    # Not crossing if pipe runs along alignment (both endpoints on alignment)
    if endpoint_on_alignment(aln, sp, tol_on_align) and endpoint_on_alignment(aln, ep, tol_on_align):
        return False
    return True

def get_pressure_network_ids_by_names(tr, names, warnings):
    if not HAS_PRESSURE:
        warnings.append("AeccPressurePipesMgd not available; skipping pressure networks.")
        return []
    try:
        all_ids = CivilDocumentPressurePipesExtension.GetPressurePipeNetworkIds(civdoc)
    except Exception as e:
        warnings.append(f"Could not get pressure network ids: {str(e)}")
        return []
    wanted = set([n.lower() for n in names])
    out = []
    for oid in all_ids:
        try:
            pn = tr.GetObject(oid, OpenMode.ForRead)
            nm = getattr(pn, "Name", "")
            if str(nm).strip().lower() in wanted:
                out.append(oid)
        except:
            pass
    return out

# -----------------------------
# OUTPUT
# -----------------------------
results = {
    "Network": network_name,
    "Prefix": ic_prefix,
    "CSV": None,
    "IC_Count": 0,
    "AlignmentsCreated": [],
    "ProfilesCreated": [],
    "ProfileViewsCreated": [],
    "Crossings": {
        "OnAlignTol_m": ON_ALIGN_TOL,
        "GravityNetworksRequested": GRAVITY_CROSS_NET_NAMES,
        "PressureNetworksRequested": PRESSURE_CROSS_NET_NAMES
    },
    "Placement": {"Columns": COLUMNS},
    "Warnings": [],
    "Skipped": []
}

csv_path = resolve_csv_path(out_path)
csv_folder = os.path.dirname(csv_path)
if csv_folder and not os.path.exists(csv_folder):
    os.makedirs(csv_folder)

# -----------------------------
# MAIN (LOCK + TRANSACTION)
# -----------------------------
doc_lock = doc.LockDocument()
try:
    tr = db.TransactionManager.StartTransaction()
    try:
        # Find MAIN gravity pipe network for alignment generation
        target_net = None
        for oid in civdoc.GetPipeNetworkIds():
            net = tr.GetObject(oid, OpenMode.ForRead)
            if getattr(net, "Name", "") == network_name and hasattr(net, "GetStructureIds") and hasattr(net, "GetPipeIds"):
                target_net = net
                break
        if target_net is None:
            raise Exception(f'Pipe Network "{network_name}" not found or missing GetStructureIds/GetPipeIds.')

        # Placement start
        minx, miny, maxx, maxy = compute_network_extents(tr, target_net)
        base_x = maxx + MARGIN_X
        base_y = maxy + MARGIN_Y

        # Ensure temp layer exists
        layer_id = ensure_layer(tr, TEMP_LAYER)

        # Alignment style + label set
        align_style_id, _ = get_style_id_by_name_or_first(
            civdoc.Styles.AlignmentStyles, DESIRED_ALIGNMENT_STYLE, results["Warnings"], "Alignment Style"
        )
        align_labelset_id, _ = get_style_id_by_name_or_first(
            civdoc.Styles.LabelSetStyles.AlignmentLabelSetStyles, DESIRED_ALIGNMENT_LABELSET, results["Warnings"], "Alignment Label Set"
        )

        # Surface
        surface_id = find_surface_id_by_name(tr, SURFACE_NAME)
        if SURFACE_NAME and surface_id == ObjectId.Null:
            raise Exception(f'Surface "{SURFACE_NAME}" not found. Check the surface name.')

        # Profile style + label set (first available)
        prof_style_ids = list(civdoc.Styles.ProfileStyles.ToObjectIds())
        prof_labelset_ids = list(civdoc.Styles.LabelSetStyles.ProfileLabelSetStyles.ToObjectIds())
        if not prof_style_ids or not prof_labelset_ids:
            raise Exception("Profile Styles or Profile Label Set Styles not found in drawing (import from template).")
        profile_style_id = prof_style_ids[0]
        profile_labelset_id = prof_labelset_ids[0]

        # Profile View style + Band Set
        pv_style_id, _ = get_style_id_by_name_or_first(
            civdoc.Styles.ProfileViewStyles, PROFILEVIEW_STYLE_NAME, results["Warnings"], "Profile View Style"
        )
        bandset_id, _ = get_style_id_by_name_or_first(
            civdoc.Styles.ProfileViewBandSetStyles, BANDSET_NAME, results["Warnings"], "Profile View Band Set"
        )

        # Band Data source (pipe network id by name)
        datasource_id = ObjectId.Null
        if BAND_DATASOURCE_NAME:
            for oid in civdoc.GetPipeNetworkIds():
                n = tr.GetObject(oid, OpenMode.ForRead)
                if getattr(n, "Name", "") == BAND_DATASOURCE_NAME:
                    datasource_id = oid
                    break
            if datasource_id == ObjectId.Null:
                results["Warnings"].append(f'Data Source "{BAND_DATASOURCE_NAME}" not found. Pipe bands may be empty.')

        # Gravity crossing network ids from names
        gravity_cross_ids = []
        if GRAVITY_CROSS_NET_NAMES:
            wanted = set([n.lower() for n in GRAVITY_CROSS_NET_NAMES])
            for oid in civdoc.GetPipeNetworkIds():
                n = tr.GetObject(oid, OpenMode.ForRead)
                if getattr(n, "Name", "").strip().lower() in wanted:
                    gravity_cross_ids.append(oid)

        # Pressure crossing network ids from names
        pressure_cross_ids = get_pressure_network_ids_by_names(tr, PRESSURE_CROSS_NET_NAMES, results["Warnings"])

        # Pipe connectivity map for MAIN network
        conn = {}
        for pid in target_net.GetPipeIds():
            p = tr.GetObject(pid, OpenMode.ForRead)
            st_id, en_id = get_pipe_end_structure_ids(p)
            if st_id is None or en_id is None:
                continue
            conn.setdefault(st_id, []).append((pid, st_id, en_id))
            conn.setdefault(en_id, []).append((pid, st_id, en_id))

        # IC list + CSV
        ic_rows = [["StructureName", "X", "Y", "Z"]]
        ic_ids = []
        for sid in target_net.GetStructureIds():
            s = tr.GetObject(sid, OpenMode.ForRead)
            sname = getattr(s, "Name", "")
            if sname.startswith(ic_prefix):
                ic_ids.append(sid)
                pt = try_get_point3d(s)
                if pt:
                    ic_rows.append([sname, pt.X, pt.Y, pt.Z])
                else:
                    ic_rows.append([sname, None, None, None])

        results["IC_Count"] = len(ic_ids)

        with open(resolve_csv_path(out_path), "w", newline="", encoding="utf-8") as f:
            csv.writer(f).writerows(ic_rows)
        results["CSV"] = resolve_csv_path(out_path)

        # ModelSpace
        ms_id = SymbolUtilityServices.GetBlockModelSpaceId(db)
        ms = tr.GetObject(ms_id, OpenMode.ForWrite)

        # Placement state
        place = {"x": base_x, "y": base_y, "row_h": 0.0, "col": 0}
        def next_grid_position(pv_w, pv_h):
            place["row_h"] = max(place["row_h"], pv_h)
            place["col"] += 1
            if place["col"] >= COLUMNS:
                place["col"] = 0
                place["x"] = base_x
                place["y"] = place["y"] - (place["row_h"] + SPACING_Y)
                place["row_h"] = 0.0
            else:
                place["x"] = place["x"] + (pv_w + SPACING_X)

        # Cache existing alignment names to prevent alignment duplicates
        existing_align_names = set()
        for aid in civdoc.GetAlignmentIds():
            a = tr.GetObject(aid, OpenMode.ForRead)
            existing_align_names.add(a.Name)

        # Create alignments + profiles + profile views
        for sid in ic_ids:
            start_struct = tr.GetObject(sid, OpenMode.ForRead)
            start_name = getattr(start_struct, "Name", "")

            connected = conn.get(sid, [])
            if not connected:
                results["Skipped"].append(f"{start_name} (no connected pipe found)")
                continue

            for (pipe_id, st_id, en_id) in connected:
                other_sid = en_id if st_id == sid else st_id
                end_struct = tr.GetObject(other_sid, OpenMode.ForRead)

                pipe_obj = tr.GetObject(pipe_id, OpenMode.ForRead)
                sp, ep = get_pipe_points(pipe_obj)
                if sp is None or ep is None:
                    sp = try_get_point3d(start_struct)
                    ep = try_get_point3d(end_struct)
                if sp is None or ep is None:
                    results["Skipped"].append(f"{start_name} -> {getattr(end_struct, 'Name', '')} (no coordinates)")
                    continue

                # Alignment from temp polyline
                pl = Polyline()
                pl.AddVertexAt(0, Point2d(sp.X, sp.Y), 0.0, 0.0, 0.0)
                pl.AddVertexAt(1, Point2d(ep.X, ep.Y), 0.0, 0.0, 0.0)
                pl.Layer = TEMP_LAYER

                pl_id = ms.AppendEntity(pl)
                tr.AddNewlyCreatedDBObject(pl, True)

                aln_name = build_unique_name(existing_align_names, f"Alignment - {start_name}")

                plops = PolylineOptions()
                plops.PlineId = pl_id
                plops.AddCurvesBetweenTangents = False
                plops.EraseExistingEntities = True

                aln_id = Alignment.Create(civdoc, plops, aln_name, SITE_ID, layer_id, align_style_id, align_labelset_id)
                aln = tr.GetObject(aln_id, OpenMode.ForWrite)

                # Surface profile
                surface_profile_id = ObjectId.Null
                if surface_id != ObjectId.Null:
                    prof_name = f"EG - {SURFACE_NAME}"
                    surface_profile_id = Profile.CreateFromSurface(
                        prof_name, aln_id, surface_id,
                        aln.LayerId, profile_style_id, profile_labelset_id
                    )
                    results["ProfilesCreated"].append({"Alignment": aln_name, "Profile": prof_name})

                # -------- FIX: Unique PV naming per alignment + retry on duplicate --------
                insert_pt = Point3d(place["x"], place["y"], 0.0)
                pv_base = f"PV - {aln_name}"
                pv_id, pv_name = create_profile_view_unique(aln_id, insert_pt, bandset_id, pv_style_id, pv_base)
                pv = tr.GetObject(pv_id, OpenMode.ForWrite)

                # Band inputs
                set_band_inputs(pv, datasource_id, surface_profile_id, results["Warnings"])

                # Add main parts
                add_parts_to_profile_view(tr, [pipe_id, sid, other_sid], pv_id, results["Warnings"])

                # Alignment polyline (AutoCAD polyline) for intersection testing
                aln_pl_id = aln.GetPolyline()
                aln_pl = tr.GetObject(aln_pl_id, OpenMode.ForRead)

                # Gravity crossings
                if gravity_cross_ids:
                    crossing_ids = set()
                    for gnet_id in gravity_cross_ids:
                        try:
                            gnet = tr.GetObject(gnet_id, OpenMode.ForRead)
                            for gpid in gnet.GetPipeIds():
                                gp = tr.GetObject(gpid, OpenMode.ForRead)
                                gsp, gep = get_pipe_points(gp)
                                if gsp is None or gep is None:
                                    continue
                                if is_pipe_crossing(aln, aln_pl, gsp, gep, ON_ALIGN_TOL):
                                    crossing_ids.add(gpid)
                                    s1, s2 = get_pipe_end_structure_ids(gp)
                                    if s1 and s1 != ObjectId.Null: crossing_ids.add(s1)
                                    if s2 and s2 != ObjectId.Null: crossing_ids.add(s2)
                        except Exception as e:
                            results["Warnings"].append(f"Gravity crossing scan failed: {str(e)}")

                    # remove main parts
                    for oid in (pipe_id, sid, other_sid):
                        if oid in crossing_ids:
                            crossing_ids.remove(oid)

                    add_parts_to_profile_view(tr, list(crossing_ids), pv_id, results["Warnings"])

                # Pressure crossings (pressure pipes only)
                if HAS_PRESSURE and pressure_cross_ids:
                    pressure_pipe_ids_to_add = set()
                    for pnet_id in pressure_cross_ids:
                        try:
                            pnet = tr.GetObject(pnet_id, OpenMode.ForRead)
                            for ppid in pnet.GetPipeIds():
                                pp = tr.GetObject(ppid, OpenMode.ForRead)
                                sp2 = getattr(pp, "StartPoint", None)
                                ep2 = getattr(pp, "EndPoint", None)
                                if sp2 is None or ep2 is None:
                                    continue
                                if is_pipe_crossing(aln, aln_pl, sp2, ep2, ON_ALIGN_TOL):
                                    pressure_pipe_ids_to_add.add(ppid)
                        except Exception as e:
                            results["Warnings"].append(f"Pressure crossing scan failed: {str(e)}")

                    add_pressure_pipes_to_profile_view(tr, list(pressure_pipe_ids_to_add), pv_id, results["Warnings"])

                # erase generated alignment polyline to keep drawing clean
                try:
                    aln_pl_w = tr.GetObject(aln_pl_id, OpenMode.ForWrite)
                    aln_pl_w.Erase()
                except:
                    pass

                results["ProfileViewsCreated"].append({"Alignment": aln_name, "ProfileView": pv_name})

                # Placement sizing estimate
                try:
                    aln_len = float(aln.Length)
                except:
                    aln_len = 300.0
                pv_w = max(MIN_PV_WIDTH, min(MAX_PV_WIDTH, aln_len + 100.0))
                pv_h = max(PV_HEIGHT_DEFAULT, 250.0)
                next_grid_position(pv_w, pv_h)

        tr.Commit()

    finally:
        tr.Dispose()

finally:
    doc_lock.Dispose()

OUT = results
