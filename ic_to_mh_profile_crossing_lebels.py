# =============================================================================
# LabelPP.py  —  Dynamo CPython3 script for Civil 3D 2025+
# Add crossing labels (gravity + pressure pipes) to Profile Views.
# =============================================================================
# INPUTS (use the [+] button on the Python node to add inputs)
#   IN[0] profile_views        : list  - Dynamo Profile View objects.
#                                        Empty/None -> all PVs in the drawing.
#   IN[1] gravity_label_style  : str   - Crossing Pipe label style name
#                                        (optional; first available is used
#                                        if blank or not found).
#   IN[2] pressure_label_style : str   - Crossing Pressure Pipe label style
#                                        name (optional).
# OUT : summary string with per-PV counts and any errors.
# =============================================================================

import clr

clr.AddReference("AcDbMgd")
clr.AddReference("AcCoreMgd")
clr.AddReference("AcMgd")
clr.AddReference("AeccDbMgd")

from Autodesk.AutoCAD.ApplicationServices import Application
from Autodesk.AutoCAD.DatabaseServices import OpenMode, ObjectId
from Autodesk.Civil.ApplicationServices import CivilApplication
from Autodesk.Civil.DatabaseServices import (
    ProfileView, CrossingPipeProfileLabel,
)

# Pressure-pipe extension assembly is optional.
HAS_PRESSURE = False
try:
    clr.AddReference("AeccPressurePipesMgd")
    from Autodesk.Civil.DatabaseServices import CrossingPressurePipeProfileLabel
    try:
        from Autodesk.Civil.DatabaseServices.Styles import LabelStylesRootPressurePipesExtension
    except:
        LabelStylesRootPressurePipesExtension = None
    HAS_PRESSURE = True
except:
    CrossingPressurePipeProfileLabel = None
    LabelStylesRootPressurePipesExtension = None


# -----------------------------------------------------------------------------
# Inputs
# -----------------------------------------------------------------------------
def _opt_str(i):
    try:
        if len(IN) > i and IN[i] is not None:
            return str(IN[i]).strip()
    except:
        pass
    return ""


def _opt_bool(i, default_value):
    try:
        if len(IN) > i and IN[i] is not None:
            v = IN[i]
            if isinstance(v, bool):
                return v
            s = str(v).strip().lower()
            if s in ("1", "true", "yes", "y", "on"):
                return True
            if s in ("0", "false", "no", "n", "off"):
                return False
    except:
        pass
    return default_value

raw_pvs = IN[0] if (len(IN) > 0 and IN[0] is not None) else []
if not isinstance(raw_pvs, (list, tuple)):
    raw_pvs = [raw_pvs]

GRAVITY_LABEL_STYLE  = _opt_str(1)
PRESSURE_LABEL_STYLE = _opt_str(2)
RUN_GRAVITY = _opt_bool(3, True)
RUN_PRESSURE = _opt_bool(4, False)
PRESSURE_DEBUG = _opt_bool(5, False)

adoc   = Application.DocumentManager.MdiActiveDocument
db     = adoc.Database
civdoc = CivilApplication.ActiveDocument

errors = []


# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------
def unwrap_to_objectid(item):
    if item is None:
        return ObjectId.Null
    if isinstance(item, ObjectId):
        return item
    for attr in ("InternalObjectId", "ObjectId", "Id"):
        if hasattr(item, attr):
            try:
                v = getattr(item, attr)
                if isinstance(v, ObjectId):
                    return v
            except:
                pass
    return ObjectId.Null


def get_style_id(coll, desired_name):
    """Return ObjectId for desired_name, else first style, else Null."""
    if coll is None:
        return ObjectId.Null
    ids = get_collection_objectids(coll)
    if not ids:
        return ObjectId.Null
    if desired_name:
        try:
            if coll.Contains(desired_name):
                return coll.get_Item(desired_name)
        except:
            pass
    return ids[0]


def get_collection_objectids(coll):
    """Best-effort extraction of ObjectIds from a style collection."""
    if coll is None:
        return []

    # 1) Common Civil 3D API pattern.
    try:
        ids = list(coll.ToObjectIds())
    except:
        ids = []
    if ids:
        return ids

    # 2) Some collections are directly enumerable in pythonnet.
    try:
        out = []
        for it in coll:
            if isinstance(it, ObjectId):
                out.append(it)
            elif hasattr(it, "ObjectId"):
                try:
                    oid = it.ObjectId
                    if isinstance(oid, ObjectId):
                        out.append(oid)
                except:
                    pass
        if out:
            return out
    except:
        pass

    # 3) Indexer fallback using Count and Item(i).
    try:
        cnt = int(coll.Count)
    except:
        cnt = 0
    if cnt > 0:
        out = []
        for i in range(cnt):
            try:
                v = coll.Item[i]
            except:
                try:
                    v = coll.get_Item(i)
                except:
                    v = None
            if isinstance(v, ObjectId):
                out.append(v)
            elif hasattr(v, "ObjectId"):
                try:
                    oid = v.ObjectId
                    if isinstance(oid, ObjectId):
                        out.append(oid)
                except:
                    pass
        if out:
            return out

    return []


def normalize_name_for_match(s):
    """Normalize text for style-name matching (case/spacing-insensitive)."""
    try:
        t = str(s).strip().lower()
    except:
        return ""
    if not t:
        return ""
    return " ".join(t.split())


def try_find_style_id_case_insensitive(tr, coll, desired_name):
    """Return (style_id, found) for a style name match (case-insensitive)."""
    if coll is None or not desired_name:
        return ObjectId.Null, False
    target = normalize_name_for_match(desired_name)
    if not target:
        return ObjectId.Null, False

    # Fast exact-name path.
    try:
        if coll.Contains(desired_name):
            return coll.get_Item(desired_name), True
    except:
        pass

    # Robust case-insensitive fallback.
    ids = get_collection_objectids(coll)
    for sid in ids:
        try:
            sobj = tr.GetObject(sid, OpenMode.ForRead)
            n = getattr(sobj, "Name")
            if n is not None and normalize_name_for_match(n) == target:
                return sid, True
        except:
            pass
    return ObjectId.Null, False


def find_all_crossing_style_collections(root, max_depth=8):
    """Collect all label style collections under LabelStyles tree."""
    out = []
    if root is None:
        return out
    seen = set()
    stack = [(root, "", 0)]
    while stack:
        node, path, depth = stack.pop()
        if path in seen:
            continue
        seen.add(path)
        try:
            attrs = dir(node)
        except:
            continue
        for a in attrs:
            if not a or a.startswith("_") or a[0].islower():
                continue
            if a in ("Equals", "GetHashCode", "GetType", "ToString",
                     "ReferenceEquals", "MemberwiseClone"):
                continue
            try:
                child = getattr(node, a)
            except:
                continue
            if child is None or isinstance(child, (str, int, float, bool, bytes)):
                continue
            full = path + "." + a if path else a
            is_coll = hasattr(child, "ToObjectIds")
            if is_coll:
                out.append(child)
            elif (not is_coll) and depth < max_depth:
                cn = type(child).__name__
                if "LabelStyles" in cn or "StyleRoot" in cn or "Styles" in cn:
                    stack.append((child, full, depth + 1))
    return out


def try_find_style_id_in_collections_case_insensitive(tr, colls, desired_name):
    """Return (style_id, found) from any collection by case-insensitive style name."""
    if not desired_name:
        return ObjectId.Null, False
    for coll in (colls or []):
        sid, found = try_find_style_id_case_insensitive(tr, coll, desired_name)
        if found and (not sid.IsNull):
            return sid, True
    return ObjectId.Null, False


def find_crossing_style_collection(root, keyword, max_depth=6):
    """
    Walk a LabelStyles tree to find a 'Crossing<keyword>' label-style
    collection. Bounded by depth and limited to LabelStyles/StyleRoot
    descendants because pythonnet returns new wrappers per attribute access
    (so id()-based cycle detection is unreliable).
    """
    if root is None:
        return None
    keyword_l = keyword.lower()
    seen = set()
    stack = [(root, "", 0)]
    while stack:
        node, path, depth = stack.pop()
        if path in seen:
            continue
        seen.add(path)
        try:
            attrs = dir(node)
        except:
            continue
        for a in attrs:
            if not a or a.startswith("_") or a[0].islower():
                continue
            if a in ("Equals", "GetHashCode", "GetType", "ToString",
                     "ReferenceEquals", "MemberwiseClone"):
                continue
            try:
                child = getattr(node, a)
            except:
                continue
            if child is None or isinstance(child, (str, int, float, bool, bytes)):
                continue
            full = path + "." + a if path else a
            la = a.lower()
            lcn = type(child).__name__.lower()
            is_coll = hasattr(child, "ToObjectIds")
            if is_coll and (
                ("crossing" in la and keyword_l in la) or
                ("crossing" in lcn and keyword_l in lcn)
            ):
                return child
            if not is_coll and depth < max_depth:
                cn = type(child).__name__
                if "LabelStyles" in cn or "StyleRoot" in cn or "Styles" in cn:
                    stack.append((child, full, depth + 1))
    return None


def _get_attr_or_none(obj, attr):
    try:
        return getattr(obj, attr)
    except:
        return None


def _get_nested_attr_or_none(obj, dotted_path):
    """Best-effort nested getattr for dotted Civil API paths."""
    cur = obj
    if cur is None:
        return None
    for p in dotted_path.split("."):
        if not p:
            continue
        try:
            cur = getattr(cur, p)
        except:
            return None
        if cur is None:
            return None
    return cur


def get_pressure_pipe_label_styles_root(styles_root, label_styles_root):
    """
    Resolve LabelStylesPressurePipeRoot via direct properties and pressure-pipe
    extension method paths.
    """
    # Direct property paths first.
    direct = _get_nested_attr_or_none(label_styles_root, "PressurePipeLabelStyles")
    if direct is not None:
        return direct

    direct = _get_nested_attr_or_none(styles_root, "PressurePipeLabelStyles")
    if direct is not None:
        return direct

    # Extension method path (AeccPressurePipesMgd):
    # LabelStylesRootPressurePipesExtension.GetPressurePipeLabelStyles(LabelStylesRoot)
    ext = LabelStylesRootPressurePipesExtension
    if ext is not None:
        for root in (label_styles_root, _get_nested_attr_or_none(styles_root, "LabelStyles")):
            if root is None:
                continue
            try:
                v = ext.GetPressurePipeLabelStyles(root)
                if v is not None:
                    return v
            except:
                pass

    return None


def collect_known_pressure_style_collections(styles_root, label_styles_root):
    """
    Collect pressure crossing/profile style collections from documented paths,
    including newer PlanProfileLabelStyles routes.
    Returns [(path, collection), ...].
    """
    out = []
    seen = set()

    probes = []
    pressure_root = get_pressure_pipe_label_styles_root(styles_root, label_styles_root)

    if pressure_root is not None:
        probes.extend([
            ("PressurePipeRoot.CrossingProfileLabelStyles", pressure_root,
             "CrossingProfileLabelStyles"),
            ("PressurePipeRoot.PlanProfileLabelStyles", pressure_root,
             "PlanProfileLabelStyles"),
            ("PressurePipeRoot.LabelStyles", pressure_root,
             "LabelStyles"),
        ])

    if label_styles_root is not None:
        probes.extend([
            ("LabelStyles.PressurePipeLabelStyles.CrossingProfileLabelStyles", label_styles_root,
             "PressurePipeLabelStyles.CrossingProfileLabelStyles"),
            ("LabelStyles.PressurePipeLabelStyles.PlanProfileLabelStyles", label_styles_root,
             "PressurePipeLabelStyles.PlanProfileLabelStyles"),
            ("LabelStyles.PressurePipeLabelStyles.PlanProfileLabelStyles.CrossingProfileLabelStyles", label_styles_root,
             "PressurePipeLabelStyles.PlanProfileLabelStyles.CrossingProfileLabelStyles"),
            ("LabelStyles.PressurePipeLabelStyles.LabelStyles", label_styles_root,
             "PressurePipeLabelStyles.LabelStyles"),
        ])
    if styles_root is not None:
        probes.extend([
            ("Styles.PressurePipeLabelStyles.CrossingProfileLabelStyles", styles_root,
             "PressurePipeLabelStyles.CrossingProfileLabelStyles"),
            ("Styles.PressurePipeLabelStyles.PlanProfileLabelStyles", styles_root,
             "PressurePipeLabelStyles.PlanProfileLabelStyles"),
            ("Styles.PressurePipeLabelStyles.PlanProfileLabelStyles.CrossingProfileLabelStyles", styles_root,
             "PressurePipeLabelStyles.PlanProfileLabelStyles.CrossingProfileLabelStyles"),
            ("Styles.PressurePipeLabelStyles.LabelStyles", styles_root,
             "PressurePipeLabelStyles.LabelStyles"),
            ("Styles.LabelStyles.PressurePipeLabelStyles.CrossingProfileLabelStyles", styles_root,
             "LabelStyles.PressurePipeLabelStyles.CrossingProfileLabelStyles"),
            ("Styles.LabelStyles.PressurePipeLabelStyles.PlanProfileLabelStyles", styles_root,
             "LabelStyles.PressurePipeLabelStyles.PlanProfileLabelStyles"),
            ("Styles.LabelStyles.PressurePipeLabelStyles.PlanProfileLabelStyles.CrossingProfileLabelStyles", styles_root,
             "LabelStyles.PressurePipeLabelStyles.PlanProfileLabelStyles.CrossingProfileLabelStyles"),
            ("Styles.LabelStyles.PressurePipeLabelStyles.LabelStyles", styles_root,
             "LabelStyles.PressurePipeLabelStyles.LabelStyles"),
        ])

    for disp, root, rel in probes:
        coll = _get_nested_attr_or_none(root, rel)
        if coll is None:
            continue
        # Keep only collection-like objects.
        ids = get_collection_objectids(coll)
        if ids or hasattr(coll, "ToObjectIds") or hasattr(coll, "Count"):
            if disp not in seen:
                seen.add(disp)
                out.append((disp, coll))

    return out


def find_pressure_crossing_style_collection(styles_root, label_styles_root):
    """
    Pressure-only style discovery.
    Tries direct Civil 3D paths first, then falls back to generic traversal.
    """
    if label_styles_root is None and styles_root is None:
        return None

    # Prefer pressure root from extension/direct property paths.
    pressure_root = get_pressure_pipe_label_styles_root(styles_root, label_styles_root)
    if pressure_root is not None:
        for pname in ("CrossingProfileLabelStyles", "PlanProfileLabelStyles", "LabelStyles"):
            coll = _get_attr_or_none(pressure_root, pname)
            if coll is not None and get_collection_objectids(coll):
                return coll

    # Prefer documented and extension pressure style paths first.
    known = collect_known_pressure_style_collections(styles_root, label_styles_root)
    for _p, coll in known:
        if get_collection_objectids(coll):
            return coll

    # Try exact known path first and ensure it is readable.
    try:
        cps = label_styles_root.PressurePipeLabelStyles.CrossProfileLabelStyles
        if cps is not None and get_collection_objectids(cps):
            return cps
    except:
        pass

    # Most common direct path in Civil 3D for pressure crossing profile styles.
    pps = _get_attr_or_none(label_styles_root, "PressurePipeLabelStyles")
    if pps is not None:
        cps = _get_attr_or_none(pps, "CrossProfileLabelStyles")
        if cps is not None and get_collection_objectids(cps):
            return cps

    # Some versions expose a slightly different container name.
    pps_alt = _get_attr_or_none(label_styles_root, "PressureLabelStyles")
    if pps_alt is not None:
        cps_alt = _get_attr_or_none(pps_alt, "CrossProfileLabelStyles")
        if cps_alt is not None and get_collection_objectids(cps_alt):
            return cps_alt

    # Fallback: keyword traversal.
    coll = find_crossing_style_collection(label_styles_root, "PressurePipe")
    if coll is not None:
        return coll

    # Last fallback for variants that use shorter naming.
    return find_crossing_style_collection(label_styles_root, "Pressure")


def collect_pressure_crossing_style_collections(label_styles_root, max_depth=8):
    """
    Pressure-only diagnostics helper.
    Returns [(path, collection), ...] for pressure crossing profile style collections.
    """
    out = []
    if label_styles_root is None:
        return out

    # Exact known path from Civil 3D API examples.
    try:
        cps_exact = label_styles_root.PressurePipeLabelStyles.CrossProfileLabelStyles
        if cps_exact is not None:
            out.append(("PressurePipeLabelStyles.CrossProfileLabelStyles", cps_exact))
    except:
        pass

    # Known direct paths first.
    pps = _get_attr_or_none(label_styles_root, "PressurePipeLabelStyles")
    if pps is not None:
        cps = _get_attr_or_none(pps, "CrossProfileLabelStyles")
        if cps is not None and hasattr(cps, "ToObjectIds"):
            out.append(("PressurePipeLabelStyles.CrossProfileLabelStyles", cps))

    pps_alt = _get_attr_or_none(label_styles_root, "PressureLabelStyles")
    if pps_alt is not None:
        cps_alt = _get_attr_or_none(pps_alt, "CrossProfileLabelStyles")
        if cps_alt is not None and hasattr(cps_alt, "ToObjectIds"):
            out.append(("PressureLabelStyles.CrossProfileLabelStyles", cps_alt))

    # Generic traversal fallback.
    seen_paths = set([p for p, _ in out])
    stack = [(label_styles_root, "", 0)]
    while stack:
        node, path, depth = stack.pop()
        try:
            attrs = dir(node)
        except:
            continue
        for a in attrs:
            if not a or a.startswith("_") or a[0].islower():
                continue
            if a in ("Equals", "GetHashCode", "GetType", "ToString",
                     "ReferenceEquals", "MemberwiseClone"):
                continue
            try:
                child = getattr(node, a)
            except:
                continue
            if child is None or isinstance(child, (str, int, float, bool, bytes)):
                continue

            full = path + "." + a if path else a
            is_coll = hasattr(child, "ToObjectIds")
            la = a.lower()
            lcn = type(child).__name__.lower()
            lfull = full.lower()

            if is_coll:
                has_cross = ("cross" in la) or ("cross" in lcn) or ("cross" in lfull)
                has_press = ("press" in la) or ("press" in lcn) or ("press" in lfull)
                if has_cross and has_press and full not in seen_paths:
                    seen_paths.add(full)
                    out.append((full, child))
            elif depth < max_depth:
                cn = type(child).__name__
                if "LabelStyles" in cn or "StyleRoot" in cn or "Styles" in cn:
                    stack.append((child, full, depth + 1))

    return out


def collect_crossing_profile_style_collections(root, max_depth=8):
    """
    Generic diagnostics helper.
    Returns [(path, collection), ...] for collections that look like
    crossing-profile label-style collections, even when names do not include
    "pressure".
    """
    out = []
    if root is None:
        return out

    seen_paths = set()
    stack = [(root, "", 0)]
    while stack:
        node, path, depth = stack.pop()
        try:
            attrs = dir(node)
        except:
            continue
        for a in attrs:
            if not a or a.startswith("_") or a[0].islower():
                continue
            if a in ("Equals", "GetHashCode", "GetType", "ToString",
                     "ReferenceEquals", "MemberwiseClone"):
                continue
            try:
                child = getattr(node, a)
            except:
                continue
            if child is None or isinstance(child, (str, int, float, bool, bytes)):
                continue

            full = path + "." + a if path else a
            la = a.lower()
            lcn = type(child).__name__.lower()
            lfull = full.lower()
            is_coll = hasattr(child, "ToObjectIds")

            if is_coll:
                has_cross = ("cross" in la) or ("cross" in lcn) or ("cross" in lfull)
                has_profile = ("profile" in la) or ("profile" in lcn) or ("profile" in lfull)
                if has_cross and has_profile and full not in seen_paths:
                    seen_paths.add(full)
                    out.append((full, child))
            elif depth < max_depth:
                cn = type(child).__name__
                if "LabelStyles" in cn or "StyleRoot" in cn or "Styles" in cn:
                    stack.append((child, full, depth + 1))

    return out


def get_style_names_from_collection(tr, coll):
    """Return style names from a style collection in display order."""
    names = []
    if coll is None:
        return names

    # 1) Prefer ObjectId-based access.
    ids = get_collection_objectids(coll)
    for sid in ids:
        try:
            sobj = tr.GetObject(sid, OpenMode.ForRead)
            n = getattr(sobj, "Name")
            if n is not None:
                names.append(str(n))
        except:
            pass
    if names:
        return names

    # 2) Fallback: some runtimes enumerate style objects directly.
    try:
        for it in coll:
            try:
                if hasattr(it, "Name"):
                    n = getattr(it, "Name")
                    if n is not None:
                        names.append(str(n))
                        continue
            except:
                pass
            try:
                oid = None
                if isinstance(it, ObjectId):
                    oid = it
                elif hasattr(it, "ObjectId"):
                    oid = it.ObjectId
                if isinstance(oid, ObjectId) and (not oid.IsNull):
                    sobj = tr.GetObject(oid, OpenMode.ForRead)
                    n = getattr(sobj, "Name")
                    if n is not None:
                        names.append(str(n))
            except:
                pass
    except:
        pass
    return names


def get_public_member_names(obj):
    """Return public member names for diagnostics."""
    try:
        attrs = dir(obj)
    except:
        return []
    out = []
    for a in attrs:
        if not a or a.startswith("_") or a[0].islower():
            continue
        if a in ("Equals", "GetHashCode", "GetType", "ToString",
                 "ReferenceEquals", "MemberwiseClone"):
            continue
        out.append(a)
    return sorted(out)


def get_members_with_keywords(obj, keywords):
    """Return public member names containing any keyword."""
    ks = [str(k).lower() for k in (keywords or []) if k]
    if not ks:
        return []
    out = []
    for a in get_public_member_names(obj):
        la = a.lower()
        if any(k in la for k in ks):
            out.append(a)
    return out


def collect_style_collections_with_paths(root, max_depth=8):
    """Collect (path, collection) pairs under a style root."""
    out = []
    if root is None:
        return out

    seen_paths = set()
    stack = [(root, "", 0)]
    while stack:
        node, path, depth = stack.pop()
        for a in get_public_member_names(node):
            try:
                child = getattr(node, a)
            except:
                continue
            if child is None or isinstance(child, (str, int, float, bool, bytes)):
                continue

            full = path + "." + a if path else a
            is_coll = hasattr(child, "ToObjectIds") or hasattr(child, "Count")
            if is_coll:
                if full not in seen_paths:
                    seen_paths.add(full)
                    out.append((full, child))
            elif depth < max_depth:
                cn = type(child).__name__
                if ("LabelStyles" in cn) or ("StyleRoot" in cn) or ("Styles" in cn):
                    stack.append((child, full, depth + 1))
    return out


def collect_profile_view_ids(tr):
    pv_ids = []
    if raw_pvs:
        for item in raw_pvs:
            oid = unwrap_to_objectid(item)
            if not oid.IsNull:
                try:
                    obj = tr.GetObject(oid, OpenMode.ForRead)
                    if isinstance(obj, ProfileView):
                        pv_ids.append(oid)
                except:
                    pass
        return pv_ids
    # Fallback: scan every block in the drawing.
    bt = tr.GetObject(db.BlockTableId, OpenMode.ForRead)
    for btr_id in bt:
        try:
            btr = tr.GetObject(btr_id, OpenMode.ForRead)
        except:
            continue
        for eid in btr:
            try:
                obj = tr.GetObject(eid, OpenMode.ForRead)
                if type(obj).__name__ == "ProfileView":
                    pv_ids.append(eid)
            except:
                pass
    return pv_ids


def _extract_ids_from_accessor_value(v):
    """Normalize accessor return values to a list of ObjectIds."""
    if v is None:
        return []
    if hasattr(v, "ToObjectIds"):
        try:
            ids = list(v.ToObjectIds())
            if ids:
                return ids
        except:
            pass
    try:
        ids = []
        for it in v:
            if isinstance(it, ObjectId):
                ids.append(it)
            elif hasattr(it, "ObjectId"):
                try:
                    oid = it.ObjectId
                    if isinstance(oid, ObjectId):
                        ids.append(oid)
                except:
                    pass
        return ids
    except:
        return []


def get_crossing_ids(tr, pv_obj, names, preferred_type_name=None):
    """
    Try each candidate accessor on the ProfileView and return a list of
    ObjectIds (the crossings available for labeling). Confirmed accessors
    on Civil 3D 2025: GetAvailablePipeProfileLabelIds (gravity) and
    GetPressureNetworkPartsInGraph (pressure).
    """
    fallback_ids = []
    for name in names:
        if not hasattr(pv_obj, name):
            continue
        try:
            v = getattr(pv_obj, name)
            if callable(v):
                v = v()
        except:
            continue
        ids = _extract_ids_from_accessor_value(v)
        if not ids:
            continue

        # Keep first non-empty as fallback.
        if not fallback_ids:
            fallback_ids = ids

        # Prefer accessors that already return wrapper ids of the desired type.
        if preferred_type_name:
            sample = ids[:3]
            ok = 0
            for oid in sample:
                try:
                    o = tr.GetObject(oid, OpenMode.ForRead)
                    if type(o).__name__ == preferred_type_name:
                        ok += 1
                except:
                    pass
            if ok > 0:
                return ids

    return fallback_ids


def collect_all_wrapper_ids(tr, wrapper_type_name):
    """Collect all wrapper ids of a given type in the drawing."""
    out = []
    bt = tr.GetObject(db.BlockTableId, OpenMode.ForRead)
    for btr_id in bt:
        try:
            btr = tr.GetObject(btr_id, OpenMode.ForRead)
        except:
            continue
        for eid in btr:
            try:
                obj = tr.GetObject(eid, OpenMode.ForRead)
                if type(obj).__name__ == wrapper_type_name:
                    out.append(eid)
            except:
                pass
    return out


def get_wrapper_ids_in_pv_block(tr, pv_obj, wrapper_type_name):
    """Return wrapper ids of a given type inside this ProfileView block."""
    out = []
    try:
        btr = tr.GetObject(pv_obj.BlockId, OpenMode.ForRead)
    except:
        return out
    for eid in btr:
        try:
            obj = tr.GetObject(eid, OpenMode.ForRead)
            if type(obj).__name__ == wrapper_type_name:
                out.append(eid)
        except:
            pass
    return out


def build_dynamic_ref_map(tr, wrapper_ids):
    """
    Build map { referenced_source_oid_str : wrapper_oid } by probing all
    public attributes on wrapper objects and collecting ObjectId-valued attrs.
    """
    ref_map = {}
    for wid in wrapper_ids:
        try:
            wobj = tr.GetObject(wid, OpenMode.ForRead)
            attrs = [a for a in dir(wobj) if a and not a.startswith("_") and a[0].isupper()]
        except:
            continue
        for a in attrs:
            if a in ("Application", "Database", "Document", "ObjectId", "OwnerId", "BlockId"):
                continue
            try:
                v = getattr(wobj, a)
                if hasattr(v, "ObjectId"):
                    v = v.ObjectId
                if isinstance(v, ObjectId) and not v.IsNull:
                    key = str(v)
                    if key not in ref_map:
                        ref_map[key] = wid
            except:
                pass
    return ref_map


def get_station_of_oid(tr, oid):
    """Best-effort station extraction for sorting; returns None if unavailable."""
    try:
        o = tr.GetObject(oid, OpenMode.ForRead)
    except:
        return None
    for a in ("Station", "StationOffset", "RawStation", "GraphStation"):
        if hasattr(o, a):
            try:
                v = getattr(o, a)
                if isinstance(v, (int, float)):
                    return float(v)
            except:
                pass
    return None


def _to_float_or_none(v):
    try:
        if isinstance(v, (int, float)):
            return float(v)
        return float(str(v))
    except:
        return None


def get_profile_view_station_mid(pv_obj):
    """Best-effort profile-view station midpoint for pressure label placement."""
    start_names = (
        "StationStart", "StartStation", "GraphStartStation", "ProfileStartStation",
    )
    end_names = (
        "StationEnd", "EndStation", "GraphEndStation", "ProfileEndStation",
    )

    s0 = None
    s1 = None

    for a in start_names:
        if hasattr(pv_obj, a):
            try:
                s0 = _to_float_or_none(getattr(pv_obj, a))
            except:
                s0 = None
            if s0 is not None:
                break

    for a in end_names:
        if hasattr(pv_obj, a):
            try:
                s1 = _to_float_or_none(getattr(pv_obj, a))
            except:
                s1 = None
            if s1 is not None:
                break

    if s0 is not None and s1 is not None:
        if s1 < s0:
            s0, s1 = s1, s0
        return (s0 + s1) * 0.5

    return s0 if s0 is not None else s1


def get_profile_view_station_range(pv_obj):
    """Best-effort profile-view station range as (start, end), else (None, None)."""
    start_names = (
        "StationStart", "StartStation", "GraphStartStation", "ProfileStartStation",
    )
    end_names = (
        "StationEnd", "EndStation", "GraphEndStation", "ProfileEndStation",
    )

    s0 = None
    s1 = None

    for a in start_names:
        if hasattr(pv_obj, a):
            try:
                s0 = _to_float_or_none(getattr(pv_obj, a))
            except:
                s0 = None
            if s0 is not None:
                break

    for a in end_names:
        if hasattr(pv_obj, a):
            try:
                s1 = _to_float_or_none(getattr(pv_obj, a))
            except:
                s1 = None
            if s1 is not None:
                break

    if s0 is None or s1 is None:
        return (None, None)
    if s1 < s0:
        s0, s1 = s1, s0
    return (s0, s1)


def station_to_ratio(station, pv_start, pv_end):
    """Convert station to normalized ratio in [0,1] for pressure label API."""
    try:
        st = float(station)
    except:
        return None
    if pv_start is None or pv_end is None:
        return None
    try:
        den = float(pv_end) - float(pv_start)
        if den <= 0.0:
            return None
        r = (st - float(pv_start)) / den
    except:
        return None
    if r < 0.0:
        return 0.0
    if r > 1.0:
        return 1.0
    return r


def get_station_for_pressure_crossing(tr, crossing_oid, wrapper_oid, pv_obj, pv_mid):
    """
    Resolve a usable station for pressure label Create(..., station, style).
    """
    st = get_station_of_oid(tr, crossing_oid)
    if st is not None:
        return st
    if isinstance(wrapper_oid, ObjectId) and (not wrapper_oid.IsNull):
        st = get_station_of_oid(tr, wrapper_oid)
        if st is not None:
            return st
    if pv_mid is not None:
        return pv_mid
    return get_profile_view_station_mid(pv_obj)


def get_wrapper_candidates_for_pv(tr, pv_obj, all_wrapper_ids, wrapper_type_name):
    """
    Return wrapper ids likely belonging to this PV.
    Uses block ownership first, then scans all wrappers for ObjectId-valued
    attrs referencing PV ObjectId or PV BlockId.
    """
    pv_oid = pv_obj.ObjectId
    pv_oid_s = str(pv_oid)
    pv_block_s = str(pv_obj.BlockId)

    candidates = []
    seen = set()

    # 1) Direct block ownership.
    local = get_wrapper_ids_in_pv_block(tr, pv_obj, wrapper_type_name)
    for wid in local:
        k = str(wid)
        if k not in seen:
            seen.add(k)
            candidates.append(wid)

    # 2) Reference-based ownership hints.
    for wid in all_wrapper_ids:
        ks = str(wid)
        if ks in seen:
            continue
        try:
            wobj = tr.GetObject(wid, OpenMode.ForRead)
            attrs = [a for a in dir(wobj) if a and not a.startswith("_") and a[0].isupper()]
        except:
            continue
        belongs = False
        for a in attrs:
            if a in ("Application", "Database", "Document"):
                continue
            try:
                v = getattr(wobj, a)
                if hasattr(v, "ObjectId"):
                    v = v.ObjectId
                if isinstance(v, ObjectId) and not v.IsNull:
                    sv = str(v)
                    if sv == pv_oid_s or sv == pv_block_s:
                        belongs = True
                        break
            except:
                pass
        if belongs:
            seen.add(ks)
            candidates.append(wid)

    return candidates


def wrapper_is_in_pv(tr, wrapper_oid, pv_obj):
    """True if wrapper belongs to the given ProfileView by BlockId or OwnerId."""
    try:
        w = tr.GetObject(wrapper_oid, OpenMode.ForRead)
        if str(w.BlockId) == str(pv_obj.BlockId):
            return True
    except:
        pass
    try:
        w = tr.GetObject(wrapper_oid, OpenMode.ForRead)
        if hasattr(w, "OwnerId") and str(w.OwnerId) == str(pv_obj.ObjectId):
            return True
    except:
        pass
    return False


def pair_crossings_to_wrappers_by_order(tr, crossing_ids, wrapper_ids):
    """
    Deterministic fallback pairing when explicit source->wrapper mapping fails.
    Sort both lists by station when possible, otherwise by ObjectId string.
    """
    if not crossing_ids or not wrapper_ids:
        return {}

    def _sort_key(oid):
        st = get_station_of_oid(tr, oid)
        if st is None:
            return (1, str(oid))
        return (0, st)

    xs = sorted(crossing_ids, key=_sort_key)
    ws = sorted(wrapper_ids, key=_sort_key)
    n = min(len(xs), len(ws))
    m = {}
    for i in range(n):
        m[str(xs[i])] = ws[i]
    return m


def get_profileviewpart_source_oid(tr, pvpart_oid):
    """Return source ObjectId for a ProfileViewPart wrapper, else Null."""
    try:
        obj = tr.GetObject(pvpart_oid, OpenMode.ForRead)
    except:
        return ObjectId.Null
    if type(obj).__name__ != "ProfileViewPart":
        return ObjectId.Null

    # Probe common names first.
    for a in ("PartId", "NetworkPartId", "ModelPartId", "SourceId", "EntityId", "ReferencedEntityId"):
        if not hasattr(obj, a):
            continue
        try:
            v = getattr(obj, a)
            if hasattr(v, "ObjectId"):
                v = v.ObjectId
            if isinstance(v, ObjectId) and not v.IsNull:
                return v
        except:
            pass

    # Then probe any ObjectId-valued public attribute; return the one that
    # points to a Pipe if found.
    try:
        attrs = [a for a in dir(obj) if a and not a.startswith("_") and a[0].isupper()]
    except:
        attrs = []
    for a in attrs:
        if a in ("Application", "Database", "Document", "ObjectId", "OwnerId", "BlockId"):
            continue
        try:
            v = getattr(obj, a)
            if hasattr(v, "ObjectId"):
                v = v.ObjectId
            if isinstance(v, ObjectId) and not v.IsNull and is_pipe_oid(tr, v):
                return v
        except:
            pass
    return ObjectId.Null


def is_pipe_oid(tr, oid):
    try:
        o = tr.GetObject(oid, OpenMode.ForRead)
        return type(o).__name__ == "Pipe"
    except:
        return False


def is_pressure_pipe_oid(tr, oid):
    """Best-effort check for pressure pipe source objects."""
    try:
        o = tr.GetObject(oid, OpenMode.ForRead)
        tn = type(o).__name__.lower()
        # Different builds may use slightly different type names.
        return ("pressure" in tn and "pipe" in tn)
    except:
        return False


def get_pressure_source_oid(tr, oid):
    """
    Resolve a crossing/wrapper/source id to the actual pressure pipe source id.
    Returns ObjectId.Null when unresolved.
    """
    if oid is None or oid.IsNull:
        return ObjectId.Null
    try:
        obj = tr.GetObject(oid, OpenMode.ForRead)
    except:
        return ObjectId.Null

    # Already a pressure pipe id.
    if is_pressure_pipe_oid(tr, oid):
        return oid

    # Probe common source-id attributes.
    for a in ("PartId", "NetworkPartId", "ModelPartId", "SourceId", "EntityId", "ReferencedEntityId"):
        if not hasattr(obj, a):
            continue
        try:
            v = getattr(obj, a)
            if hasattr(v, "ObjectId"):
                v = v.ObjectId
            if isinstance(v, ObjectId) and (not v.IsNull) and is_pressure_pipe_oid(tr, v):
                return v
        except:
            pass

    # Generic probe of ObjectId-valued public attributes.
    try:
        attrs = [a for a in dir(obj) if a and (not a.startswith("_")) and a[0].isupper()]
    except:
        attrs = []
    for a in attrs:
        if a in ("Application", "Database", "Document", "ObjectId", "OwnerId", "BlockId"):
            continue
        try:
            v = getattr(obj, a)
            if hasattr(v, "ObjectId"):
                v = v.ObjectId
            if isinstance(v, ObjectId) and (not v.IsNull) and is_pressure_pipe_oid(tr, v):
                return v
        except:
            pass

    return ObjectId.Null


def get_type_name_of_oid(tr, oid):
    """Return CLR type name for an ObjectId, or empty string."""
    try:
        if oid is None or oid.IsNull:
            return ""
        o = tr.GetObject(oid, OpenMode.ForRead)
        return type(o).__name__
    except:
        return ""


def gravity_candidate_is_pipe_based(tr, oid):
    """
    True if oid is:
    - a Pipe object id, or
    - a ProfileViewPart whose source object is Pipe.
    """
    try:
        o = tr.GetObject(oid, OpenMode.ForRead)
    except:
        return False

    tn = type(o).__name__
    if tn == "Pipe":
        return True
    if tn == "ProfileViewPart":
        src = get_profileviewpart_source_oid(tr, oid)
        return (not src.IsNull) and is_pipe_oid(tr, src)
    return False


def get_pipe_based_pvparts(tr, wrapper_ids):
    """Return ProfileViewPart ids whose source object resolves to a Pipe."""
    out = []
    for wid in wrapper_ids:
        try:
            wobj = tr.GetObject(wid, OpenMode.ForRead)
            if type(wobj).__name__ != "ProfileViewPart":
                continue
        except:
            continue
        src = get_profileviewpart_source_oid(tr, wid)
        if not src.IsNull and is_pipe_oid(tr, src):
            out.append(wid)
    return out


def resolve_wrapper_id_for_crossing(tr, crossing_oid, wrapper_type_name, wrapper_ids, ref_map):
    """
    Resolve crossing id to the wrapper id required by Label.Create.
    1) If crossing id already is wrapper type, use directly.
    2) Else map via dynamically discovered reference ObjectIds.
    """
    try:
        cobj = tr.GetObject(crossing_oid, OpenMode.ForRead)
        if type(cobj).__name__ == wrapper_type_name:
            return crossing_oid
    except:
        pass

    return ref_map.get(str(crossing_oid), ObjectId.Null)


def try_add_to_profile_view(tr, source_oid, pv_oid, expected_wrapper_type):
    """
    Fallback: try calling AddToProfileView on a source network part to create
    the wrapper object required by Crossing*ProfileLabel.Create.
    Returns ObjectId.Null on failure.
    """
    obj = None
    try:
        obj = tr.GetObject(source_oid, OpenMode.ForWrite)
    except:
        try:
            obj = tr.GetObject(source_oid, OpenMode.ForRead)
        except:
            obj = None
    if obj is None or not hasattr(obj, "AddToProfileView"):
        return ObjectId.Null

    try:
        ret = obj.AddToProfileView(pv_oid)
        if isinstance(ret, ObjectId) and not ret.IsNull:
            try:
                robj = tr.GetObject(ret, OpenMode.ForRead)
                if type(robj).__name__ == expected_wrapper_type:
                    return ret
            except:
                pass
    except:
        pass
    return ObjectId.Null


GRAV_NAMES = (
    "GetAvailablePipeProfileLabelIds",          # returns ProfileViewPart wrapper ids directly
    "GetAvailableSpanningPipeProfileLabelIds",
    "GetPipeNetworkPartsInGraph",
    "GetPipePartsInGraph",
    "GetProfileViewParts",
    "ProfileViewParts",
)
PRESS_NAMES = (
    "GetPressureNetworkPartsInGraph",
    "GetAvailablePressurePipeProfileLabelIds",
)


def create_gravity_label(source_oid, pv_oid, style_id, wrapper_oid=ObjectId.Null):
    """
    Try the 3-arg overload first; if no style is available (Null id), try
    a 2-arg overload that picks a default. Returns (True, '') on success or
    (False, error_message) on failure.
    """
    last = ""
    # 1) Preferred: direct source id path (worked in your earlier script).
    if not style_id.IsNull:
        try:
            CrossingPipeProfileLabel.Create(source_oid, pv_oid, style_id)
            return True, ""
        except Exception as e:
            last = str(e)
    try:
        CrossingPipeProfileLabel.Create(source_oid, pv_oid)
        return True, ""
    except Exception as e:
        last = str(e)

    # 2) Fallback: wrapper id path.
    if isinstance(wrapper_oid, ObjectId) and not wrapper_oid.IsNull:
        if not style_id.IsNull:
            try:
                CrossingPipeProfileLabel.Create(wrapper_oid, pv_oid, style_id)
                return True, ""
            except Exception as e:
                last = str(e)
        try:
            CrossingPipeProfileLabel.Create(wrapper_oid, pv_oid)
            return True, ""
        except Exception as e:
            last = str(e)

    return False, last


def create_pressure_label(source_oid, pv_oid, style_id, wrapper_oid=ObjectId.Null):
    if CrossingPressurePipeProfileLabel is None:
        return False, "CrossingPressurePipeProfileLabel not available"
    sid = style_id if isinstance(style_id, ObjectId) else ObjectId.Null
    errs = []

    def _try(name, fn):
        try:
            fn()
            return True
        except Exception as e:
            errs.append("{0}: {1}".format(name, one_line(e)))
            return False

    # Try all likely overload patterns; some builds require station even when style is null.
    if _try("src,pv,station,style", lambda: CrossingPressurePipeProfileLabel.Create(source_oid, pv_oid, float(0.0), sid)):
        return True, ""
    if isinstance(wrapper_oid, ObjectId) and not wrapper_oid.IsNull:
        if _try("wrap,pv,station,style", lambda: CrossingPressurePipeProfileLabel.Create(wrapper_oid, pv_oid, float(0.0), sid)):
            return True, ""

    if _try("src,pv,station", lambda: CrossingPressurePipeProfileLabel.Create(source_oid, pv_oid, float(0.0))):
        return True, ""
    if isinstance(wrapper_oid, ObjectId) and not wrapper_oid.IsNull:
        if _try("wrap,pv,station", lambda: CrossingPressurePipeProfileLabel.Create(wrapper_oid, pv_oid, float(0.0))):
            return True, ""

    if _try("src,pv,style", lambda: CrossingPressurePipeProfileLabel.Create(source_oid, pv_oid, sid)):
        return True, ""
    if isinstance(wrapper_oid, ObjectId) and not wrapper_oid.IsNull:
        if _try("wrap,pv,style", lambda: CrossingPressurePipeProfileLabel.Create(wrapper_oid, pv_oid, sid)):
            return True, ""

    if _try("src,pv", lambda: CrossingPressurePipeProfileLabel.Create(source_oid, pv_oid)):
        return True, ""
    if isinstance(wrapper_oid, ObjectId) and not wrapper_oid.IsNull:
        if _try("wrap,pv", lambda: CrossingPressurePipeProfileLabel.Create(wrapper_oid, pv_oid)):
            return True, ""

    # Return concise diagnostics with the first distinct failures.
    uniq = []
    seen = set()
    for m in errs:
        if m not in seen:
            seen.add(m)
            uniq.append(m)
    return False, " | ".join(uniq[:4])


def create_pressure_label_with_station(source_oid, pv_oid, ratio, style_id,
                                       wrapper_oid=ObjectId.Null):
    """Pressure-only creator for runtime-supported Create(..., ratio, style)."""
    if CrossingPressurePipeProfileLabel is None:
        return False, "CrossingPressurePipeProfileLabel not available"

    sid = style_id if isinstance(style_id, ObjectId) else ObjectId.Null
    if sid.IsNull:
        return False, "Pressure style id is null"

    try:
        rr = float(ratio)
    except:
        return False, "Pressure ratio is not available"

    if rr < 0.0:
        rr = 0.0
    elif rr > 1.0:
        rr = 1.0

    last = ""
    try:
        CrossingPressurePipeProfileLabel.Create(source_oid, pv_oid, rr, sid)
        return True, ""
    except Exception as e:
        last = one_line(e)

    if isinstance(wrapper_oid, ObjectId) and not wrapper_oid.IsNull:
        try:
            CrossingPressurePipeProfileLabel.Create(wrapper_oid, pv_oid, rr, sid)
            return True, ""
        except Exception as e:
            last = one_line(e)

    return False, last


def create_pressure_label_with_station_candidates(source_oids, pv_oid, ratio,
                                                  style_id, wrapper_oid=ObjectId.Null):
    """
    Try pressure label Create with multiple source-id candidates.
    Returns (ok, err, used_oid).
    """
    # Unique, non-null candidates preserving order.
    uniq = []
    seen = set()
    for oid in (source_oids or []):
        try:
            key = str(oid)
        except:
            key = ""
        if (not key) or (key in seen):
            continue
        seen.add(key)
        if isinstance(oid, ObjectId) and (not oid.IsNull):
            uniq.append(oid)

    last = ""
    for oid in uniq:
        ok, err = create_pressure_label_with_station(oid, pv_oid, ratio, style_id, wrapper_oid)
        if ok:
            return True, "", oid
        if err:
            last = err
    return False, last, ObjectId.Null


def get_pressure_create_overload_signatures():
    """Best-effort dump of available static Create overloads for diagnostics."""
    out = []
    if CrossingPressurePipeProfileLabel is None:
        return out
    try:
        ovs = CrossingPressurePipeProfileLabel.Create.Overloads
        try:
            for ov in ovs:
                out.append(str(ov))
        except:
            out.append(str(ovs))
    except Exception as e:
        out.append("<unavailable: {0}>".format(one_line(e)))
    return out


def one_line(msg):
    try:
        s = str(msg)
        return s.splitlines()[0] if s else ""
    except:
        return ""


def get_object_description(tr, oid, _depth=0):
    """Return the Civil 3D Description value for a crossing/source object."""
    if oid is None or oid.IsNull or _depth > 2:
        return ""
    try:
        obj = tr.GetObject(oid, OpenMode.ForRead)
    except:
        return ""

    # 1) Exact Description field on the current object.
    try:
        v = getattr(obj, "Description")
        if (v is not None) and (not callable(v)):
            s = str(v).strip()
            if s:
                return s
    except:
        pass

    # 2) If this is a ProfileViewPart, resolve its source part and read Description there.
    try:
        if type(obj).__name__ == "ProfileViewPart":
            src = get_profileviewpart_source_oid(tr, oid)
            if (src is not None) and (not src.IsNull):
                return get_object_description(tr, src, _depth + 1)
    except:
        pass

    # 3) Generic source-id probes for other wrapper-like objects.
    for attr in ("PartId", "NetworkPartId", "ModelPartId", "SourceId", "EntityId", "ReferencedEntityId"):
        try:
            v = getattr(obj, attr)
        except:
            continue
        try:
            if hasattr(v, "ObjectId"):
                v = v.ObjectId
            if isinstance(v, ObjectId) and (not v.IsNull):
                d = get_object_description(tr, v, _depth + 1)
                if d:
                    return d
        except:
            continue

    return ""


def label_gravity_for_pv(tr, pv_obj, pv_oid, pv_name, grav_style_id,
                         grav_coll, grav_search_colls, all_grav_wrapper_ids):
    # GetAvailablePipeProfileLabelIds returns raw Pipe ObjectIds (used for
    # the crossing count only). Create() requires ProfileViewPart wrapper ids,
    # which live directly inside the ProfileView's own block.
    grav_ids = get_crossing_ids(tr, pv_obj, GRAV_NAMES)
    pv_wrapper_ids = get_wrapper_ids_in_pv_block(tr, pv_obj, "ProfileViewPart")
    created = 0
    local_errors = []
    style_match_count = 0
    style_missing_count = 0
    missing_style_names = set()

    _SKIP_MSGS = (
        "source of profileviewpart should be a pipe",
        "profileviewpart is not in profileview",
    )
    for wid in pv_wrapper_ids:
        raw_desc = get_object_description(tr, wid)
        style_for_obj = grav_style_id
        matched_for_obj = False
        missing_name_for_obj = ""

        # If Description is present, try to use a style with the same name.
        if raw_desc:
            sid, found = try_find_style_id_case_insensitive(tr, grav_coll, raw_desc)
            if (not found) or sid.IsNull:
                sid, found = try_find_style_id_in_collections_case_insensitive(
                    tr, grav_search_colls, raw_desc
                )
            if found and (not sid.IsNull):
                style_for_obj = sid
                matched_for_obj = True
            else:
                missing_name_for_obj = raw_desc

        ok, err = create_gravity_label(wid, pv_oid, style_for_obj)
        if ok:
            created += 1
            if raw_desc:
                if matched_for_obj:
                    style_match_count += 1
                else:
                    style_missing_count += 1
                    missing_style_names.add(missing_name_for_obj)
        elif err:
            el = one_line(err).lower()
            if not any(m in el for m in _SKIP_MSGS):
                local_errors.append(
                    "Gravity label on PV '{0}': {1}".format(pv_name, one_line(err))
                )

    return (
        len(grav_ids),
        created,
        0,
        local_errors,
        style_match_count,
        style_missing_count,
        sorted(list(missing_style_names)),
    )


def label_pressure_for_pv(tr, pv_obj, pv_oid, pv_name, press_style_id,
                          all_press_wrapper_ids, pressure_debug_enabled=False):
    press_ids = get_crossing_ids(
        tr, pv_obj, PRESS_NAMES,
        preferred_type_name="ProfileViewPressurePart"
    )
    press_wrapper_ids = get_wrapper_candidates_for_pv(
        tr, pv_obj, all_press_wrapper_ids, "ProfileViewPressurePart"
    )
    press_ref_map = build_dynamic_ref_map(tr, press_wrapper_ids)
    press_order_map = pair_crossings_to_wrappers_by_order(
        tr, press_ids, press_wrapper_ids
    )

    created = 0
    local_errors = []
    pv_mid = get_profile_view_station_mid(pv_obj)
    pv_start, pv_end = get_profile_view_station_range(pv_obj)
    diag_once = False

    for pid in press_ids:
        pvpart_oid = resolve_wrapper_id_for_crossing(
            tr, pid, "ProfileViewPressurePart", press_wrapper_ids, press_ref_map
        )
        if pvpart_oid.IsNull:
            pvpart_oid = press_order_map.get(str(pid), ObjectId.Null)
        if pvpart_oid.IsNull:
            pvpart_oid = try_add_to_profile_view(
                tr, pid, pv_oid, "ProfileViewPressurePart"
            )
            if not pvpart_oid.IsNull:
                press_wrapper_ids.append(pvpart_oid)
                press_ref_map = build_dynamic_ref_map(tr, press_wrapper_ids)

        source_oid = get_pressure_source_oid(tr, pid)
        if source_oid.IsNull and (not pvpart_oid.IsNull):
            source_oid = get_pressure_source_oid(tr, pvpart_oid)

        source_candidates = []
        if not source_oid.IsNull:
            source_candidates.append(source_oid)
        source_candidates.append(pid)
        if not pvpart_oid.IsNull:
            source_candidates.append(pvpart_oid)

        st = get_station_for_pressure_crossing(
            tr, pid, pvpart_oid, pv_obj, pv_mid
        )
        ratio = station_to_ratio(st, pv_start, pv_end)
        if ratio is None:
            ratio = 0.5

        ok, err, used_oid = create_pressure_label_with_station_candidates(
            source_candidates, pv_oid, ratio, press_style_id, pvpart_oid
        )
        if ok:
            created += 1
        elif err:
            if press_style_id.IsNull and "No pressure crossing label style found/imported" in err:
                # keep message concise and avoid one line per crossing when style is missing
                continue
            if pressure_debug_enabled:
                if not diag_once:
                    diag_once = True
                    local_errors.append(
                        "Pressure debug PV '{0}': pidType={1}, pvpartType={2}, srcType={3}, station={4}, ratio={5}, styleNull={6}".format(
                            pv_name,
                            get_type_name_of_oid(tr, pid) or "(unknown)",
                            get_type_name_of_oid(tr, pvpart_oid) or "(unknown)",
                            get_type_name_of_oid(tr, source_oid) or "(unresolved)",
                            "{0:.3f}".format(st) if isinstance(st, (int, float)) else "(none)",
                            "{0:.3f}".format(ratio) if isinstance(ratio, (int, float)) else "(none)",
                            str(press_style_id.IsNull),
                        )
                    )
                local_errors.append(
                    "Pressure label on PV '{0}': {1}".format(
                        pv_name, one_line(err)
                    )
                )
            else:
                # In non-debug mode, keep one concise error per profile view.
                if not diag_once:
                    diag_once = True
                    local_errors.append(
                        "Pressure label on PV '{0}': {1}".format(
                            pv_name, one_line(err)
                        )
                    )

    return len(press_ids), created, local_errors


# -----------------------------------------------------------------------------
# Main
# -----------------------------------------------------------------------------
gravity_labels = 0
pressure_labels = 0
skipped_non_pipe_grav = 0
pv_report = []  # (name, n_grav, n_press, lbl_grav, lbl_press)
grav_style_match_total = 0
grav_style_missing_total = 0
pressure_style_debug = []

with adoc.LockDocument():
    with db.TransactionManager.StartTransaction() as tr:

        grav_coll = None
        grav_search_colls = []
        grav_style_id = ObjectId.Null
        try:
            all_crossing_colls = find_all_crossing_style_collections(civdoc.Styles.LabelStyles)
            # Exclude pressure collections; keep likely gravity crossing-profile collections.
            grav_search_colls = [
                c for c in all_crossing_colls
                if "pressure" not in type(c).__name__.lower()
            ]
            grav_coll = find_crossing_style_collection(
                civdoc.Styles.LabelStyles, "Pipe",
            )
            if grav_coll is not None and "pressure" in type(grav_coll).__name__.lower():
                grav_coll = None
            if grav_coll is None and grav_search_colls:
                grav_coll = grav_search_colls[0]
            elif grav_coll is not None:
                grav_search_colls = [grav_coll] + grav_search_colls
            grav_style_id = get_style_id(grav_coll, GRAVITY_LABEL_STYLE)
        except Exception as e:
            errors.append("Gravity style lookup: {0}".format(e))

        press_style_id = ObjectId.Null
        if HAS_PRESSURE:
            try:
                pressure_style_debug = []
                styles_root = civdoc.Styles
                label_styles_root = civdoc.Styles.LabelStyles
                if PRESSURE_DEBUG:
                    known_pressure_candidates = collect_known_pressure_style_collections(
                        styles_root, label_styles_root
                    )
                    pressure_candidates = collect_pressure_crossing_style_collections(
                        label_styles_root
                    )
                    generic_labelstyle_candidates = collect_crossing_profile_style_collections(
                        label_styles_root
                    )
                    generic_styles_root_candidates = collect_crossing_profile_style_collections(
                        styles_root
                    )

                    # Merge and dedupe by path to show a complete runtime view.
                    merged_candidates = []
                    merged_seen = set()
                    for ppath, pcoll in (known_pressure_candidates +
                                         pressure_candidates +
                                         generic_labelstyle_candidates +
                                         generic_styles_root_candidates):
                        if ppath in merged_seen:
                            continue
                        merged_seen.add(ppath)
                        merged_candidates.append((ppath, pcoll))

                    pressure_style_debug.append(
                        "Pressure crossing style collections found: {0}".format(
                            len(pressure_candidates)
                        )
                    )
                    pressure_style_debug.append(
                        "Known pressure path collections found: {0}".format(
                            len(known_pressure_candidates)
                        )
                    )
                    pressure_style_debug.append(
                        "Crossing/profile collections found (all roots): {0}".format(
                            len(merged_candidates)
                        )
                    )

                    pressure_style_debug.append(
                        "Styles root type: {0}".format(type(styles_root).__name__)
                    )
                    pressure_style_debug.append(
                        "LabelStyles root type: {0}".format(type(label_styles_root).__name__)
                    )

                    pressure_root_obj = get_pressure_pipe_label_styles_root(
                        styles_root, label_styles_root
                    )
                    if pressure_root_obj is None:
                        pressure_style_debug.append(
                            "Pressure pipe root (extension/direct): (none)"
                        )
                    else:
                        pressure_style_debug.append(
                            "Pressure pipe root (extension/direct) type: {0}".format(
                                type(pressure_root_obj).__name__
                            )
                        )
                        pr_members = get_members_with_keywords(
                            pressure_root_obj, ("cross", "profile", "label", "plan")
                        )
                        pressure_style_debug.append(
                            "Pressure pipe root members: {0}".format(
                                ", ".join(pr_members[:20]) if pr_members else "(none)"
                            )
                        )

                    s_members = get_members_with_keywords(
                        styles_root, ("pressure", "pipe", "label")
                    )
                    ls_members = get_members_with_keywords(
                        label_styles_root, ("pressure", "pipe", "label")
                    )
                    pressure_style_debug.append(
                        "Styles root pressure-like members: {0}".format(
                            ", ".join(s_members[:20]) if s_members else "(none)"
                        )
                    )
                    pressure_style_debug.append(
                        "LabelStyles root pressure-like members: {0}".format(
                            ", ".join(ls_members[:20]) if ls_members else "(none)"
                        )
                    )

                    all_style_colls = collect_style_collections_with_paths(styles_root, max_depth=8)
                    all_labelstyle_colls = collect_style_collections_with_paths(label_styles_root, max_depth=8)
                    pressure_path_hits = []
                    for ppath, pcoll in (all_style_colls + all_labelstyle_colls):
                        lpath = ppath.lower()
                        if ("pressure" in lpath) or ("cross" in lpath and "profile" in lpath):
                            pressure_path_hits.append((ppath, pcoll))

                    seen_hit_paths = set()
                    unique_hits = []
                    for ppath, pcoll in pressure_path_hits:
                        if ppath in seen_hit_paths:
                            continue
                        seen_hit_paths.add(ppath)
                        unique_hits.append((ppath, pcoll))

                    pressure_style_debug.append(
                        "Pressure/cross-profile collection path hits: {0}".format(len(unique_hits))
                    )
                    for idx, (ppath, pcoll) in enumerate(unique_hits[:15]):
                        pids = get_collection_objectids(pcoll)
                        pressure_style_debug.append(
                            "  path[{0}] {1} (count={2})".format(idx + 1, ppath, len(pids))
                        )

                    ov_sigs = get_pressure_create_overload_signatures()
                    pressure_style_debug.append(
                        "Pressure Create overloads seen: {0}".format(len(ov_sigs))
                    )
                    for sig in ov_sigs[:6]:
                        pressure_style_debug.append("  sig: {0}".format(sig))
                    for idx, (ppath, pcoll) in enumerate(merged_candidates[:10]):
                        pnames = get_style_names_from_collection(tr, pcoll)
                        pressure_style_debug.append(
                            "  [{0}] {1}".format(idx + 1, ppath)
                        )
                        if pnames:
                            pressure_style_debug.append(
                                "      styles: {0}".format(", ".join(pnames))
                            )
                        else:
                            pressure_style_debug.append("      styles: (none)")

                    press_coll = find_pressure_crossing_style_collection(
                        styles_root, label_styles_root
                    )
                    if press_coll is None and pressure_candidates:
                        press_coll = pressure_candidates[0][1]
                    if press_coll is None:
                        for _ppath, _pcoll in merged_candidates:
                            if get_style_names_from_collection(tr, _pcoll):
                                press_coll = _pcoll
                                break
                    if press_coll is None and merged_candidates:
                        press_coll = merged_candidates[0][1]
                else:
                    # Fast production path without heavy diagnostics.
                    press_coll = find_pressure_crossing_style_collection(
                        styles_root, label_styles_root
                    )
                    if press_coll is None:
                        kp = collect_known_pressure_style_collections(
                            styles_root, label_styles_root
                        )
                        if kp:
                            press_coll = kp[0][1]
                    if press_coll is None:
                        gp = collect_crossing_profile_style_collections(label_styles_root)
                        if gp:
                            press_coll = gp[0][1]

                press_style_id = get_style_id(press_coll, PRESSURE_LABEL_STYLE)
            except Exception as e:
                errors.append("Pressure style lookup: {0}".format(e))

        if RUN_PRESSURE and HAS_PRESSURE and press_style_id.IsNull:
            errors.append("Pressure style lookup: no crossing pressure style found; trying default overloads")

        pv_ids = collect_profile_view_ids(tr)
        all_grav_wrapper_ids = collect_all_wrapper_ids(tr, "ProfileViewPart")
        all_press_wrapper_ids = collect_all_wrapper_ids(tr, "ProfileViewPressurePart")

        for pv_oid in pv_ids:
            try:
                pv_obj = tr.GetObject(pv_oid, OpenMode.ForRead)
                pv_name = getattr(pv_obj, "Name", str(pv_oid))
            except Exception as e:
                errors.append("Open PV {0}: {1}".format(pv_oid, e))
                continue

            ng = 0
            np_ = 0
            lg = 0
            lp = 0
            style_match = 0
            style_missing = 0
            missing_names = []

            if RUN_GRAVITY:
                (ng, lg, skipped, grav_errors,
                 style_match, style_missing, missing_names) = label_gravity_for_pv(
                    tr, pv_obj, pv_oid, pv_name, grav_style_id, grav_coll,
                    grav_search_colls,
                    all_grav_wrapper_ids
                )
                gravity_labels += lg
                skipped_non_pipe_grav += skipped
                errors.extend(grav_errors)
                grav_style_match_total += style_match
                grav_style_missing_total += style_missing

            if RUN_PRESSURE and HAS_PRESSURE:
                np_, lp, press_errors = label_pressure_for_pv(
                    tr, pv_obj, pv_oid, pv_name, press_style_id,
                    all_press_wrapper_ids, PRESSURE_DEBUG
                )
                pressure_labels += lp
                errors.extend(press_errors)

            pv_report.append((pv_name, ng, np_, lg, lp))

        tr.Commit()

# -----------------------------------------------------------------------------
# Output
# -----------------------------------------------------------------------------
lines = [
    "Profile views processed: {0}".format(len(pv_report)),
    "Gravity run enabled   : {0}".format(RUN_GRAVITY),
    "Pressure run enabled  : {0}".format(RUN_PRESSURE),
    "Gravity labels created : {0}".format(gravity_labels),
    "Pressure labels created: {0}".format(pressure_labels),
    "",
    "  {0:<40} {1:>8} {2:>8} {3:>8} {4:>8}".format(
        "Profile View", "GravX", "PressX", "GravLbl", "PressLbl"
    ),
]

if skipped_non_pipe_grav > 0:
    lines.append("Skipped non-pipe gravity crossings: {0}".format(skipped_non_pipe_grav))
    lines.append("")

for name, ng, np_, lg, lp in pv_report:
    lines.append("  {0:<40} {1:>8} {2:>8} {3:>8} {4:>8}".format(
        str(name)[:40], ng, np_, lg, lp,
    ))

if RUN_GRAVITY:
    lines.append("")
    lines.append("Gravity style matches by description: {0}".format(grav_style_match_total))
    lines.append("Gravity labels without matching style: {0}".format(grav_style_missing_total))

if RUN_PRESSURE and HAS_PRESSURE and pressure_style_debug:
    lines.append("")
    lines.append("Pressure style discovery:")
    lines.extend(pressure_style_debug)

if errors:
    seen = set()
    unique = []
    for e in errors:
        if e not in seen:
            seen.add(e)
            unique.append(e)
    lines.append("")
    lines.append("Errors ({0} total, {1} unique):".format(len(errors), len(unique)))
    for e in unique[:10]:
        lines.append("  - " + e)
    if len(unique) > 10:
        lines.append("  ...and {0} more unique".format(len(unique) - 10))

OUT = "\n".join(lines)
