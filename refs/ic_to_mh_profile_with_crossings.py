
# =============================================================================
# Civil 3D Profile View Generator  —  Dynamo CPython3 Script
# =============================================================================
# PURPOSE
#   For every Inspection Chamber (IC) in a named gravity pipe network this
#   script automatically:
#     1. Creates an Alignment along each connected pipe segment.
#     2. Creates a surface Profile (EG) on that alignment (optional).
#     3. Creates a Profile View placed on a grid layout in model space.
#     4. Adds the main pipe/structures to the profile view (Draw = Yes).
#     5. Detects and adds crossing pipes from other gravity and pressure
#        networks, then creates crossing pipe label annotations.
#
# DYNAMO INPUTS (IN[])  — see INPUTS section below for full list.
# OUTPUT (OUT)          — Python dict with counts, warnings and diagnostics.
# =============================================================================

import os
import csv
import clr
import System

# ---------------------------------------------------------------------------
# .NET assembly references
# AcMgd      : AutoCAD managed API (Application, Document)
# AcDbMgd    : AutoCAD database objects (Polyline, Layer, ObjectId, ...)
# AeccDbMgd  : Civil 3D core objects (Alignment, Profile, ProfileView, ...)
# ---------------------------------------------------------------------------
clr.AddReference("AcMgd")
clr.AddReference("AcDbMgd")
clr.AddReference("AeccDbMgd")

# AeccPressurePipesMgd is an optional assembly present only when the Pressure
# Pipes module is installed. HAS_PRESSURE guards all pressure-pipe code paths.
try:
    clr.AddReference("AeccPressurePipesMgd")
    HAS_PRESSURE = True
except:
    HAS_PRESSURE = False

# AutoCAD application and database objects
from Autodesk.AutoCAD.ApplicationServices.Core import Application
from Autodesk.AutoCAD.DatabaseServices import (
    OpenMode, Polyline, Line, SymbolUtilityServices, LayerTableRecord,
    ObjectId, Intersect
)
# 2-D/3-D geometry types used for intersection testing and point creation
from Autodesk.AutoCAD.Geometry import (
    Point2d, Point3d, Vector3d, Plane, Point3dCollection
)

# Civil 3D application and core design objects
from Autodesk.Civil.ApplicationServices import CivilApplication
from Autodesk.Civil.DatabaseServices import (
    Alignment, PolylineOptions, AlignmentType,
    Profile, ProfileView
)
try:
    from Autodesk.Civil.DatabaseServices import CrossingPipeProfileLabel
    HAS_CROSSING_LABEL = True
except:
    HAS_CROSSING_LABEL = False

# ProfileViewPart — the entity created when a pipe is added to a Profile View.
# Its ObjectId is required as the first argument of CrossingPipeProfileLabel.Create.
# Added in Civil 3D 2025 (AeccDbMgd 13.7+).
try:
    from Autodesk.Civil.DatabaseServices import ProfileViewPart
    HAS_PVPART_CLASS = True
except:
    HAS_PVPART_CLASS = False
    ProfileViewPart = None

from Autodesk.Civil import BandType  # Enum for band type checks in set_band_inputs

# Pressure pipes extension — imported only when the assembly loaded successfully
if HAS_PRESSURE:
    from Autodesk.Civil.ApplicationServices import CivilDocumentPressurePipesExtension
    try:
        from Autodesk.Civil.DatabaseServices import CrossingPressurePipeProfileLabel
        HAS_PRESSURE_LABEL = True
    except:
        HAS_PRESSURE_LABEL = False
    try:
        from Autodesk.Civil.DatabaseServices import ProfileViewPressurePart
        HAS_PVPRESSUREPART_CLASS = True
    except:
        HAS_PVPRESSUREPART_CLASS = False
        ProfileViewPressurePart = None
else:
    HAS_PRESSURE_LABEL = False
    HAS_PVPRESSUREPART_CLASS = False
    ProfileViewPressurePart = None

# =============================================================================
# INPUTS  —  Dynamo node wire connections
# =============================================================================
# IN[0]  network_name          : str   — Name of the MAIN gravity pipe network
#                                        (the one whose ICs drive alignment generation)
# IN[1]  ic_prefix             : str   — Prefix that identifies Inspection Chambers
#                                        e.g. "MH-" or "IC-"
# IN[2]  out_path              : str   — Folder path or full .csv path for IC export
#                                        (optional; defaults to %TEMP%)
# IN[3]  alignment_style       : str   — Alignment style name (default: "ProposedAlignment")
# IN[4]  alignment_labelset    : str   — Alignment label set name (default: "No Labels")
# IN[5]  surface_name          : str   — Existing Ground surface name for EG profile
#                                        (optional; leave empty to skip surface profile)
# IN[6]  profileview_style     : str   — Profile View style name
# IN[7]  bandset_name          : str   — Profile View band set style name
# IN[8]  band_datasource_name  : str   — Pipe network name used as band data source
# IN[9]  columns               : int   — Number of columns in the profile view grid layout
# IN[10] gravity_cross_list    : list  — List of gravity pipe network names to scan for
#                                        crossings (e.g. ["SL_TRENCHES", "PN_TRENCH"])
# IN[11] pressure_cross_list   : list  — List of pressure network names to scan for
#                                        crossings (e.g. ["ME_Pipe_(0003)", "POTABLE WATER"])
# IN[12] on_align_tol          : float — Offset tolerance (m) to decide if a pipe endpoint
#                                        sits ON the alignment (not a crossing). Default 0.01 m
# IN[13] gravity_label_style   : str   — Pipe label style for gravity crossing annotations
#                                        (e.g. "WW Ø280 IL=12.88" style). Optional.
# IN[14] pressure_label_style  : str   — Pipe label style for pressure crossing annotations
#                                        (e.g. "GAS Ø125 IL=13.33" style). Optional.
# IN[15] test_limit            : int   — Limit processing to this many ICs (0 or empty = all).
#                                        Use 3-5 for a quick test run.
# =============================================================================

network_name = IN[0]
ic_prefix = IN[1]
out_path = IN[2] if len(IN) > 2 else None

# ---------------------------------------------------------------------------
# Safe input readers — return a typed default when the Dynamo wire is empty,
# disconnected, or wired to an incompatible type.
# ---------------------------------------------------------------------------
def _opt_str(i, default_value=""):
    """Read IN[i] as a stripped string; return default_value if absent/empty."""
    try:
        if len(IN) > i and IN[i] is not None:
            s = str(IN[i]).strip()
            return s if s else default_value
    except:
        pass
    return default_value

def _opt_int(i, default_value):
    """Read IN[i] as an integer; return default_value if absent or non-numeric."""
    try:
        if len(IN) > i and IN[i] is not None:
            return int(IN[i])
    except:
        pass
    return default_value

def _opt_float(i, default_value):
    """Read IN[i] as a float; return default_value if absent or non-numeric."""
    try:
        if len(IN) > i and IN[i] is not None:
            return float(IN[i])
    except:
        pass
    return default_value

# --- Alignment style / label set ---
DESIRED_ALIGNMENT_STYLE    = _opt_str(3, "ProposedAlignment")
DESIRED_ALIGNMENT_LABELSET = _opt_str(4, "No Labels")

# --- Surface, profile view, and band settings ---
SURFACE_NAME          = _opt_str(5, "")
PROFILEVIEW_STYLE_NAME = _opt_str(6, "")
BANDSET_NAME          = _opt_str(7, "")
BAND_DATASOURCE_NAME  = _opt_str(8, "")
COLUMNS               = max(1, _opt_int(9, 3))  # minimum 1 column

# --- Crossing network name lists (raw — normalised below) ---
RAW_GRAVITY_CROSS_LIST  = IN[10] if len(IN) > 10 else []
RAW_PRESSURE_CROSS_LIST = IN[11] if len(IN) > 11 else []

# ON_ALIGN_TOL: a pipe endpoint within this offset (m) of the alignment
# is considered to run ALONG the alignment, not crossing it.
ON_ALIGN_TOL = abs(_opt_float(12, 0.01))

# --- Crossing label style names (optional) ---
# Leave empty to use the first available pipe label style in the drawing.
GRAVITY_CROSSING_LABEL_STYLE  = _opt_str(13, "")
PRESSURE_CROSSING_LABEL_STYLE = _opt_str(14, "")

# --- Test limit (optional) ---
# Set to a positive integer to process only the first N ICs. 0 = process all.
TEST_LIMIT = max(0, _opt_int(15, 0))

# ---------------------------------------------------------------------------
# Script-level constants
# ---------------------------------------------------------------------------

# Civil 3D site for alignments — ObjectId.Null = "no site" (recommended for
# pipe-based alignments so they are not constrained by site geometry rules).
SITE_ID    = ObjectId.Null

# Temporary AutoCAD layer used for the two-vertex polylines that seed the
# Alignment.Create() call. The polyline is erased after the alignment is made.
TEMP_LAYER = "DYN_TEMP"

# ---------------------------------------------------------------------------
# Profile view grid layout constants (drawing units = metres)
# ---------------------------------------------------------------------------
# MARGIN_X / MARGIN_Y  : gap between the network extents and the first PV
# SPACING_X / SPACING_Y: gap between adjacent profile views in the grid
MARGIN_X  = 50.0
MARGIN_Y  = 50.0
SPACING_X = 25.0
SPACING_Y = 40.0

# Estimated profile view width/height used for grid placement.
# Civil 3D resizes the PV automatically; these values only affect spacing.
MAX_PV_WIDTH      = 1200.0
MIN_PV_WIDTH      =  250.0
PV_HEIGHT_DEFAULT =  250.0

# ---------------------------------------------------------------------------
# Active document / database handles
# ---------------------------------------------------------------------------
doc    = Application.DocumentManager.MdiActiveDocument
db     = doc.Database
civdoc = CivilApplication.ActiveDocument

# =============================================================================
# HELPER FUNCTIONS
# =============================================================================

def normalize_name_list(x):
    """
    Accept a single string or a list of strings from a Dynamo input wire.
    Returns a de-duplicated list of stripped, non-empty strings.
    Comparison is case-insensitive but the original casing is preserved.
    """
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
        # Fallback: treat x itself as a single value
        s = str(x).strip()
        if s:
            names.append(s)

    # Remove duplicates while preserving first-seen order
    seen = set()
    out  = []
    for n in names:
        k = n.lower()
        if k not in seen:
            seen.add(k)
            out.append(n)
    return out

# Normalise the raw crossing network name lists once at startup
GRAVITY_CROSS_NET_NAMES  = normalize_name_list(RAW_GRAVITY_CROSS_LIST)
PRESSURE_CROSS_NET_NAMES = normalize_name_list(RAW_PRESSURE_CROSS_LIST)

def resolve_csv_path(path_in):
    """
    Resolve a CSV file path from the user-supplied output path.
    - If empty/None  : writes to %TEMP%\YCC_InspectionChambers.csv
    - If a .csv path : used as-is
    - If a folder    : appends the default filename to the folder
    """
    if not path_in:
        temp_dir = os.environ.get("TEMP", r"C:\Temp")
        return os.path.join(temp_dir, "YCC_InspectionChambers.csv")
    path_in = str(path_in)
    if path_in.lower().endswith(".csv"):
        return path_in
    return os.path.join(path_in, "YCC_InspectionChambers.csv")

def try_get_point3d(obj):
    """
    Attempt to extract a Point3d from a Civil 3D object by probing common
    position attribute names. Returns None if no valid 3-D point is found.
    Different Civil 3D object types expose their location under different names:
      Structure / MH  → Position
      Block reference → InsertionPoint
      Generic         → Location or Point
    """
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
    """
    Return (start_structure_id, end_structure_id) for a Pipe object.
    Tries both direct ObjectId properties and the older object-reference
    properties (which expose an ObjectId sub-property) for compatibility
    across Civil 3D versions. Returns (None, None) on failure.
    """
    for a, b in (("StartStructureId", "EndStructureId"),
                 ("StartStructure",   "EndStructure")):
        if hasattr(pipe_obj, a) and hasattr(pipe_obj, b):
            try:
                sv = getattr(pipe_obj, a)
                ev = getattr(pipe_obj, b)
                # Unwrap to plain ObjectId if the API returned an object
                if hasattr(sv, "ObjectId"): sv = sv.ObjectId
                if hasattr(ev, "ObjectId"): ev = ev.ObjectId
                return sv, ev
            except:
                pass
    return None, None

def get_pipe_points(pipe_obj):
    """
    Return (start_point, end_point) as Point3d objects for any pipe type
    (gravity or pressure). Returns (None, None) if the points cannot be read.
    Using a helper avoids silent failures when attribute access throws.
    """
    sp = ep = None
    if hasattr(pipe_obj, "StartPoint"):
        try: sp = pipe_obj.StartPoint
        except: sp = None
    if hasattr(pipe_obj, "EndPoint"):
        try: ep = pipe_obj.EndPoint
        except: ep = None
    return sp, ep

def build_unique_name(existing_set, base):
    """
    Generate a name that does not already exist in existing_set.
    Appends an incrementing integer suffix if the base name is taken.
    The chosen name is also added to existing_set to prevent re-use.
    """
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
    """
    Return the ObjectId of layer_name, creating it if it does not exist.
    Also unlocks the layer if it was previously locked so we can place
    temporary geometry on it without raising an error.
    """
    lt = tr.GetObject(db.LayerTableId, OpenMode.ForRead)
    for lid in lt:
        ltr = tr.GetObject(lid, OpenMode.ForRead)
        if ltr.Name.lower() == layer_name.lower():
            if ltr.IsLocked:
                ltrw = tr.GetObject(lid, OpenMode.ForWrite)
                ltrw.IsLocked = False
            return lid
    # Layer not found — create it
    lt.UpgradeOpen()
    ltr_new = LayerTableRecord()
    ltr_new.Name = layer_name
    ltr_new.IsLocked = False
    new_id = lt.Add(ltr_new)
    tr.AddNewlyCreatedDBObject(ltr_new, True)
    return new_id

def get_style_id_by_name_or_first(style_coll, desired_name, warnings, kind):
    """
    Resolve a Civil 3D style ObjectId from a style collection.
    - If desired_name is provided and exists in the collection, returns it.
    - If the name is not found, a warning is appended and the first available
      style is returned (so the script continues rather than failing).
    - Raises Exception if the collection is completely empty (nothing to fall
      back to — the drawing template must be set up correctly).
    Returns: (ObjectId, resolved_name)
    """
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
        pass
    return ids[0], "<FirstAvailable>"

# ---------------------------------------------------------------------------
# Known Civil 3D API paths for pipe label style collections.
# Civil 3D separates REGULAR pipe labels from CROSSING pipe labels, and
# separates GRAVITY from PRESSURE. We try all combinations.
# ---------------------------------------------------------------------------
_GRAVITY_CROSSING_LABEL_PATHS = [
    # Civil 3D 2025: CrossProfileLabelStyles is the correct sub-collection name
    ("LabelStyles", "PipeLabelStyles", "CrossProfileLabelStyles"),
    # Older fallback names (pre-2025)
    ("LabelStyles", "PipeLabelStyles", "CrossingPipeLabelStyles"),
    ("LabelStyles", "PipeLabelStyles", "ProfileLabelStyles"),
    ("LabelStyles", "CrossingPipeLabelStyles"),
    ("LabelStyles", "PipeLabelStyles"),
]

_PRESSURE_CROSSING_LABEL_PATHS = [
    # Civil 3D 2025: pressure pipe label styles live under PressurePipeLabelStyles
    # (NOT PipeLabelStyles, which is for gravity). The crossing sub-collection
    # is "CrossProfileLabelStyles" — same leaf name as gravity, different parent.
    ("LabelStyles", "PressurePipeLabelStyles", "CrossProfileLabelStyles"),
    ("LabelStyles", "PressurePipeLabelStyles", "CrossingPressurePipeLabelStyles"),
    ("LabelStyles", "PressurePipeLabelStyles", "ProfileLabelStyles"),
    ("LabelStyles", "PressurePipeLabelStyles"),
    # Older / fallback names
    ("LabelStyles", "PipeLabelStyles", "CrossingPressurePipeLabelStyles"),
    ("LabelStyles", "CrossingPressurePipeLabelStyles"),
]

# Keep old name as alias — used by _resolve_pipe_label_coll (still referenced below)
_PIPE_LABEL_STYLE_PATHS = _GRAVITY_CROSSING_LABEL_PATHS

def _enum_style_coll(coll):
    """
    Try several enumeration strategies on a Civil 3D style collection and
    return a list of ObjectIds. Tries ToObjectIds(), direct iteration, and
    Count/index access — different Civil 3D versions support different methods.
    """
    ids = []
    try:
        ids = list(coll.ToObjectIds())
    except:
        pass
    if ids:
        return ids
    # Direct iteration (some collections implement IEnumerable<ObjectId>)
    try:
        for item in coll:
            ids.append(item)
    except:
        pass
    if ids:
        return ids
    # Count + integer indexer
    try:
        cnt = int(coll.Count)
        for i in range(cnt):
            try:
                ids.append(coll[i])
            except:
                pass
    except:
        pass
    return ids

def _resolve_label_coll(paths):
    """Walk a list of API paths and return the first non-empty (coll, ids) found."""
    for attr_path in paths:
        try:
            obj = civdoc.Styles
            for attr in attr_path:
                obj = getattr(obj, attr)
            ids = _enum_style_coll(obj)
            if ids:
                return obj, ids
        except:
            continue
    # Last attempt: return any collection that exists even if empty
    for attr_path in paths:
        try:
            obj = civdoc.Styles
            for attr in attr_path:
                obj = getattr(obj, attr)
            return obj, []
        except:
            continue
    return None, []

def _resolve_pipe_label_coll():
    """Gravity crossing label collection (backward compat wrapper)."""
    return _resolve_label_coll(_GRAVITY_CROSSING_LABEL_PATHS)

def _lookup_style_id(coll, ids, desired_name, warnings, kind):
    """
    Given a resolved (coll, ids) pair, find desired_name or fall back to ids[0].
    Returns ObjectId.Null when ids is empty.
    """
    if not ids:
        warnings.append(f"No {kind} found in drawing; labels of this type skipped.")
        return ObjectId.Null
    if desired_name:
        try:
            if coll.Contains(desired_name):
                return coll.get_Item(desired_name)
        except:
            pass
        dn_lower = desired_name.strip().lower()
        for oid in ids:
            try:
                # Use an open/close transaction so this works at module scope
                # (outside the main transaction) as well as inside it.
                with db.TransactionManager.StartOpenCloseTransaction() as _oct:
                    s_obj = _oct.GetObject(oid, OpenMode.ForRead)
                    if getattr(s_obj, "Name", "").strip().lower() == dn_lower:
                        return oid
            except:
                pass
        warnings.append(f'{kind} "{desired_name}" not found. Using first available.')
    return ids[0]

def get_gravity_crossing_label_style_id(desired_name, warnings):
    """Resolve a gravity CROSSING pipe label style ObjectId."""
    coll, ids = _resolve_label_coll(_GRAVITY_CROSSING_LABEL_PATHS)
    if coll is None:
        warnings.append("Gravity crossing pipe label style collection not found; labels skipped.")
        return ObjectId.Null
    return _lookup_style_id(coll, ids, desired_name, warnings, "Gravity crossing pipe label style")

def _find_style_by_name_recursive(root, target_name, max_depth=4):
    """
    Walk every collection-like attribute under `root` and return the first
    ObjectId whose underlying style.Name matches target_name (case-insensitive).
    Returns (oid, path_str) or (ObjectId.Null, None).
    """
    if not target_name:
        return ObjectId.Null, None
    target = target_name.strip().lower()
    seen = set()

    def walk(obj, path, depth):
        if depth > max_depth:
            return None
        oid_self = id(obj)
        if oid_self in seen:
            return None
        seen.add(oid_self)
        # Try treating obj as a style collection
        try:
            ids = _enum_style_coll(obj)
        except:
            ids = []
        for oid in ids:
            try:
                with db.TransactionManager.StartOpenCloseTransaction() as _t:
                    so = _t.GetObject(oid, OpenMode.ForRead)
                    nm = getattr(so, "Name", "")
                    if str(nm).strip().lower() == target:
                        return oid, path
            except:
                continue
        # Recurse into attributes that look like collections
        for a in dir(obj):
            if a.startswith("_") or a.startswith("get_"):
                continue
            if a in ("Create", "Dispose", "Finalize", "GetType", "GetHashCode",
                     "MemberwiseClone", "ToString", "ReferenceEquals",
                     "InitializeLifetimeService", "GetLifetimeService",
                     "DeleteUnmanagedObject", "Equals", "Overloads",
                     "UnmanagedObject", "IsDisposed", "AutoDelete",
                     "DefaultLabelStyle", "WrappedOid"):
                continue
            try:
                sub = getattr(obj, a)
            except:
                continue
            if sub is None or isinstance(sub, (str, int, float, bool)):
                continue
            res = walk(sub, f"{path}.{a}", depth + 1)
            if res is not None:
                return res
        return None

    res = walk(root, "Styles", 0)
    if res is None:
        return ObjectId.Null, None
    return res


def get_pressure_crossing_label_style_id(desired_name, warnings):
    """Resolve a pressure CROSSING pipe label style ObjectId.
    Toolspace path: Settings → Pressure Pipe → Label Styles → Crossing Profile
    These styles live on the pressure extension, NOT on civdoc.Styles.
    """
    if not HAS_PRESSURE:
        return ObjectId.Null

    # Get the pressure extension object — styles are on it, not civdoc.Styles
    ext = None
    try:
        ext = CivilDocumentPressurePipesExtension.GetCivilDocumentPressurePipesExtension(civdoc)
    except:
        pass

    # Paths to try on the extension object, mirroring the Toolspace tree:
    # Pressure Pipe → Label Styles → Crossing Profile
    ext_paths = [
        ("Styles", "LabelStyles", "CrossingProfileLabelStyles"),
        ("Styles", "LabelStyles", "CrossProfileLabelStyles"),
        ("Styles", "LabelStyles", "PipeLabelStyles", "CrossProfileLabelStyles"),
        ("LabelStyles", "CrossingProfileLabelStyles"),
        ("LabelStyles", "CrossProfileLabelStyles"),
        ("Styles", "LabelStyles"),  # fallback: any label style
    ]

    roots = []
    if ext is not None:
        roots.append(("ext", ext))
    roots.append(("civdoc", civdoc))  # last resort

    for root_label, root in roots:
        for attr_path in ext_paths:
            try:
                obj = root
                for attr in attr_path:
                    obj = getattr(obj, attr)
                ids = _enum_style_coll(obj)
                if ids:
                    return _lookup_style_id(
                        obj, ids, desired_name, warnings,
                        "Pressure crossing pipe label style"
                    )
            except:
                continue

    # Brute-force search on extension as final fallback
    if ext is not None and desired_name:
        try:
            oid, found_path = _find_style_by_name_recursive(ext, desired_name)
            if oid != ObjectId.Null:
                warnings.append(
                    f'Pressure label style "{desired_name}" found at ext.{found_path}.'
                )
                return oid
        except:
            pass

    warnings.append(
        f'Pressure crossing pipe label style "{desired_name}" not found. '
        f'Pressure labels skipped.'
    )
    return ObjectId.Null

    """Resolve a pressure CROSSING pipe label style ObjectId."""
    # Try the structured path list first (fast path).
    coll, ids = _resolve_label_coll(_PRESSURE_CROSSING_LABEL_PATHS)
    if coll is not None:
        return _lookup_style_id(coll, ids, desired_name, warnings,
                                "Pressure crossing pipe label style")
    # Fallback: brute-force recursive search for the style by exact name.
    if desired_name:
        oid, found_path = _find_style_by_name_recursive(civdoc.Styles, desired_name)
        if oid != ObjectId.Null:
            warnings.append(
                f'Pressure crossing label style "{desired_name}" found at {found_path}.'
            )
            return oid
    warnings.append(
        f'Pressure crossing pipe label style "{desired_name}" not found anywhere '
        f'under civdoc.Styles. Pressure labels skipped.'
    )
    return ObjectId.Null

def get_pipe_label_style_id(desired_name, warnings):
    """
    Backward-compatible wrapper — resolves a gravity crossing pipe label style.
    Used for callers that don't distinguish gravity vs pressure.
    """
    return get_gravity_crossing_label_style_id(desired_name, warnings)

def _segment_intersection_2d(x1, y1, x2, y2, x3, y3, x4, y4):
    """Return (x, y) intersection of segments AB and CD, or None."""
    dx12, dy12 = x2 - x1, y2 - y1
    dx34, dy34 = x4 - x3, y4 - y3
    denom = dx12 * dy34 - dy12 * dx34
    if abs(denom) < 1e-10:
        return None
    dx13, dy13 = x3 - x1, y3 - y1
    t = (dx13 * dy34 - dy13 * dx34) / denom
    u = (dx13 * dy12 - dy13 * dx12) / denom
    if 0.0 <= t <= 1.0 and 0.0 <= u <= 1.0:
        return (x1 + t * dx12, y1 + t * dy12)
    return None


def create_crossing_pipe_labels(tr, pipe_ids, pv_id, label_style_id, warnings,
                                use_pressure_api=False, raw_pipe_ids=None,
                                pressure_stations=None):
    """
    Create a crossing pipe label for each pipe in the context of a ProfileView.

    Civil 3D 2025 API:
      Gravity: CrossingPipeProfileLabel.Create(pvpartId, profileViewId)
               CrossingPipeProfileLabel.Create(pvpartId, profileViewId, labelStyleId)
      Pressure: CrossingPressurePipeProfileLabel.Create(pvPressurePartId, labelStyleId)
                NOTE: Pressure has only ONE overload — labelStyleId is REQUIRED,
                and there is NO profileViewId parameter.

    - label_style_id of ObjectId.Null = use default style overload (gravity only).
    - use_pressure_api=True uses CrossingPressurePipeProfileLabel instead.
      Runtime overload in Civil 3D 2025.2:
        Create(pressurePipeId, profileViewId, station, labelStyleId)
      pressure_stations: dict {pvpart_oid: station_along_alignment}
    """
    created = 0
    return created # cancelling label function for now
    pressure_stations = pressure_stations or {}

    for pid in pipe_ids:
        try:
            if use_pressure_api and HAS_PRESSURE_LABEL:
                if label_style_id == ObjectId.Null:
                    warnings.append(
                        "CrossingPressurePipeProfileLabel.Create requires a label "
                        "style ID, but none was resolved. Skipping pressure labels."
                    )
                    return created
                # 4-arg overload: (pipeId, profileViewId, station, styleId)
                raw_pid = raw_pipe_ids.get(pid, pid) if raw_pipe_ids else pid
                station = pressure_stations.get(pid, 0.0)
                CrossingPressurePipeProfileLabel.Create(
                    raw_pid, pv_id, float(station), label_style_id)
            else:
                if not HAS_CROSSING_LABEL:
                    warnings.append("CrossingPipeProfileLabel not available in this Civil 3D version.")
                    return 0
                if label_style_id != ObjectId.Null:
                    CrossingPipeProfileLabel.Create(pid, pv_id, label_style_id)
                else:
                    CrossingPipeProfileLabel.Create(pid, pv_id)
            created += 1
        except Exception as e:
            warnings.append(
                f"CrossingLabel.Create failed for pipe {str(pid)}: {str(e)}"
            )
    return created

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

def _scan_pvparts_from_modelspace(tr, missing_oids, pvpart_class, warnings):
    """
    Fallback for when AddToProfileView() returns void/None.
    Scans the drawing's ModelSpace for ProfileViewPart (or ProfileViewPressurePart)
    entities whose ModelPartId matches any oid in missing_oids.
    Returns {model_part_oid: pvpart_oid} for matched entries.
    Only invoked when the primary return-value approach fails.
    """
    found = {}
    if not missing_oids or pvpart_class is None:
        return found
    target_map = {str(o): o for o in missing_oids}
    try:
        ms_id = SymbolUtilityServices.GetBlockModelSpaceId(db)
        ms = tr.GetObject(ms_id, OpenMode.ForRead)
        for eid in ms:
            try:
                obj = tr.GetObject(eid, OpenMode.ForRead)
                if isinstance(obj, pvpart_class):
                    mid_str = str(obj.ModelPartId)
                    if mid_str in target_map:
                        found[target_map[mid_str]] = eid
            except:
                pass
    except Exception as e:
        warnings.append(f"ProfileViewPart fallback scan error: {e}")
    return found


def add_parts_to_profile_view(tr, ids_to_add, pv_id, warnings):
    """
    Add gravity pipe network parts (Pipe or Structure) to a Profile View so
    they are drawn (Draw = Yes in Profile View Properties → Pipe Networks).

    Civil 3D 2025: Part.AddToProfileView() returns the ProfileViewPart ObjectId,
    which is required as the first argument of CrossingPipeProfileLabel.Create.
    Returns a dict {part_oid: pvpart_oid} for parts successfully added.
    """
    pvpart_map = {}
    for oid in ids_to_add:
        try:
            part = tr.GetObject(oid, OpenMode.ForWrite)
            if hasattr(part, "AddToProfileView"):
                result = part.AddToProfileView(pv_id)
                # Civil 3D 2025 returns the ProfileViewPart ObjectId
                try:
                    if result is not None and not result.IsNull:
                        pvpart_map[oid] = result
                except:
                    pass
        except Exception as e:
            warnings.append(f"AddToProfileView failed for {str(oid)}: {str(e)}")
    # Fallback: if AddToProfileView returned void/None, scan ModelSpace for
    # ProfileViewPart objects whose ModelPartId matches our target pipe IDs.
    missing = [o for o in ids_to_add if o not in pvpart_map]
    if missing and HAS_PVPART_CLASS:
        fallback = _scan_pvparts_from_modelspace(tr, missing, ProfileViewPart, warnings)
        if fallback:
            pvpart_map.update(fallback)
            warnings.append(
                f"Note: {len(fallback)} gravity ProfileViewPart ID(s) recovered via "
                f"ModelSpace scan (AddToProfileView returned void)."
            )
    return pvpart_map

def add_pressure_pipes_to_profile_view(tr, pressure_pipe_ids, pv_id, warnings):
    """
    Add pressure pipe network parts to a Profile View.
    Returns a dict {part_oid: pvpart_oid} for parts successfully added.
    The pvpart_oid is required by CrossingPressurePipeProfileLabel.Create.
    """
    pvpart_map = {}
    for oid in pressure_pipe_ids:
        try:
            ppart = tr.GetObject(oid, OpenMode.ForWrite)
            if hasattr(ppart, "AddToProfileView"):
                result = ppart.AddToProfileView(pv_id)
                try:
                    if result is not None and not result.IsNull:
                        pvpart_map[oid] = result
                except:
                    pass
        except Exception as e:
            warnings.append(f"Pressure pipe AddToProfileView failed for {str(oid)}: {str(e)}")
    # Fallback: scan ModelSpace for ProfileViewPressurePart with matching ModelPartId.
    missing = [o for o in pressure_pipe_ids if o not in pvpart_map]
    if missing and HAS_PVPRESSUREPART_CLASS:
        fallback = _scan_pvparts_from_modelspace(
            tr, missing, ProfileViewPressurePart, warnings
        )
        if fallback:
            pvpart_map.update(fallback)
            warnings.append(
                f"Note: {len(fallback)} pressure ProfileViewPart ID(s) recovered via "
                f"ModelSpace scan (AddToProfileView returned void)."
            )
    return pvpart_map

def set_band_inputs(pv, datasource_id, surface_profile_id, warnings):
    """
    Wire up the Profile View band data sources after the PV is created.
    Civil 3D bands are created empty; this function connects them to:
      - PipeNetwork / SectionalData bands  → the named pipe network (datasource_id)
      - ProfileData bands                  → the surface EG profile (surface_profile_id)
    Also enables label display for each connected band.
    """
    def _apply(items):
        """Apply data source IDs to a list of band items; return True if any changed."""
        changed = False
        for item in items:
            try:
                bt = item.BandType
                # Connect pipe network bands to the chosen network
                if (bt == BandType.PipeNetwork or bt == BandType.SectionalData) and datasource_id != ObjectId.Null:
                    item.DataSourceId = datasource_id
                    item.ShowLabels   = True
                    changed = True
                # Connect profile data bands to the surface EG profile
                if bt == BandType.ProfileData and surface_profile_id != ObjectId.Null:
                    item.Profile1Id = surface_profile_id
                    item.Profile2Id = surface_profile_id
                    item.ShowLabels = True
                    changed = True
            except:
                pass
        return changed

    # Apply to bottom bands (most common location for pipe network bands)
    try:
        bottom = pv.Bands.GetBottomBandItems()
        if _apply(bottom):
            pv.Bands.SetBottomBandItems(bottom)
    except:
        pass
    # Apply to top bands (some templates place elevation bands at the top)
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

# ---------------------------------------------------------------------------
# Intersection-based crossing detection
# ---------------------------------------------------------------------------
# A crossing pipe is one whose centre-line INTERSECTS the alignment polyline
# in plan (XY plane) and does NOT run along the alignment.
# The algorithm:
#   1. Project both the candidate pipe and the alignment to Z=0.
#   2. Check for a geometric intersection (IntersectWith).
#   3. If both pipe endpoints are within ON_ALIGN_TOL of the alignment the
#      pipe runs alongside it rather than crossing — exclude those pipes.
# ---------------------------------------------------------------------------

def _cross2d(ax, ay, bx, by):
    """2-D cross product of vectors (ax,ay) and (bx,by)."""
    return ax * by - ay * bx

def segments_intersect_2d(x1, y1, x2, y2, x3, y3, x4, y4):
    """
    Pure Python 2-D segment intersection test (Z coordinates ignored).
    Returns True if segment (x1,y1)→(x2,y2) properly crosses
    segment (x3,y3)→(x4,y4).
    Uses the parametric cross-product method; parallel/collinear segments
    return False (treated as non-crossing for our purposes).
    This replaces the AutoCAD IntersectWith API call, which requires both
    entities to be registered in the database and fails silently otherwise.
    """
    dx12, dy12 = x2 - x1, y2 - y1
    dx34, dy34 = x4 - x3, y4 - y3
    denom = _cross2d(dx12, dy12, dx34, dy34)
    if abs(denom) < 1e-10:
        return False  # parallel or collinear
    dx13, dy13 = x3 - x1, y3 - y1
    t = _cross2d(dx13, dy13, dx34, dy34) / denom
    u = _cross2d(dx13, dy13, dx12, dy12) / denom
    return 0.0 <= t <= 1.0 and 0.0 <= u <= 1.0

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
    """
    Return True if point pt lies within tol metres of the alignment (i.e. the
    absolute cross-track offset is ≤ tol). Used to filter out pipes that run
    alongside the alignment rather than crossing it.
    """
    try:
        _, off = station_offset(aln, pt.X, pt.Y)
        return abs(off) <= tol
    except:
        return False

def is_pipe_crossing(aln, aln_sp, aln_ep, sp, ep, tol_on_align):
    """
    Determine whether the pipe defined by endpoints sp/ep is a true crossing
    of the given alignment (defined by its start point aln_sp and end point aln_ep).
    Rules:
      - All four endpoints must be valid (not None).
      - The pipe centre-line must geometrically intersect the alignment segment
        in 2-D (pure Python math, no AutoCAD API dependency).
      - The pipe must NOT run along the alignment (both endpoints within
        tol_on_align of the alignment centre-line are treated as 'alongside').
    """
    if sp is None or ep is None or aln_sp is None or aln_ep is None:
        return False
    if not segments_intersect_2d(
        aln_sp.X, aln_sp.Y, aln_ep.X, aln_ep.Y,
        sp.X, sp.Y, ep.X, ep.Y
    ):
        return False
    # Exclude pipes that run parallel/alongside the alignment
    if endpoint_on_alignment(aln, sp, tol_on_align) and endpoint_on_alignment(aln, ep, tol_on_align):
        return False
    return True

def get_pressure_network_ids_by_names(tr, names, warnings):
    """
    Return the ObjectIds of pressure pipe networks whose names appear in the
    'names' list (case-insensitive match).
    Returns an empty list if:
      - The pressure pipes assembly is not loaded (HAS_PRESSURE = False).
      - The Civil 3D API call to enumerate networks fails.
      - No network name matches the requested list.
    All failures are non-fatal — a warning is appended instead of raising.
    """
    if not HAS_PRESSURE:
        return []
    try:
        all_ids = CivilDocumentPressurePipesExtension.GetPressurePipeNetworkIds(civdoc)
    except:
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

# =============================================================================
# OUTPUT DICTIONARY  —  populated throughout the script; returned as OUT
# =============================================================================
# Structure:
#   Network            : str   — main network name
#   Prefix             : str   — IC prefix filter used
#   CSV                : str   — full path of the exported IC CSV file
#   IC_Count           : int   — number of ICs found with the given prefix
#   AlignmentsCreated  : list  — [{Alignment}] per created alignment
#   ProfilesCreated    : list  — [{Alignment, Profile}] per surface profile
#   ProfileViewsCreated: list  — [{Alignment, ProfileView}] per profile view
#   Crossings:
#     OnAlignTol_m           : float — tolerance used for along-alignment filter
#     GravityNetworksRequested: list  — network names from IN[10]
#     PressureNetworksRequested:list  — network names from IN[11]
#     GravityLabelStyle      : str   — label style name from IN[13]
#     PressureLabelStyle     : str   — label style name from IN[14]
#     Found                  : list  — per-alignment crossing diagnostics
#                                      {Alignment, Type, PipesFound:{net:count},
#                                       TotalPartsAdded}
#     LabelsCreated          : list  — {Alignment, Type, Count} where labels
#                                      were successfully created
#   Placement          : {Columns}
#   Warnings           : list  — non-fatal issues encountered during the run
#   Skipped            : list  — ICs/pipes skipped due to missing data
# =============================================================================
results = {
    "Network": network_name,
    "Prefix":  ic_prefix,
    "CSV":     None,
    "IC_Count": 0,
    "AlignmentsCreated":   [],
    "ProfilesCreated":     [],
    "ProfileViewsCreated": [],
    "Crossings": {
        "OnAlignTol_m":              ON_ALIGN_TOL,
        "GravityNetworksRequested":  GRAVITY_CROSS_NET_NAMES,
        "PressureNetworksRequested": PRESSURE_CROSS_NET_NAMES,
        "GravityLabelStyle":         GRAVITY_CROSSING_LABEL_STYLE,
        "PressureLabelStyle":        PRESSURE_CROSSING_LABEL_STYLE,
        "Found":         [],   # diagnostic: crossing counts per alignment/network
        "LabelsCreated": []    # label creation results per alignment
    },
    "Placement": {"Columns": COLUMNS},
    "Warnings": [],
    "Skipped":  []
}

csv_path = resolve_csv_path(out_path)
csv_folder = os.path.dirname(csv_path)
if csv_folder and not os.path.exists(csv_folder):
    os.makedirs(csv_folder)

# =============================================================================
# MAIN EXECUTION  —  Document lock + single database transaction
# =============================================================================
# Everything runs inside a single transaction so all created objects are
# committed together or rolled back together on error. The document lock is
# required when running from Dynamo outside the AutoCAD command thread.
# =============================================================================

doc_lock = doc.LockDocument()
try:
    tr = db.TransactionManager.StartTransaction()
    try:

        # ------------------------------------------------------------------
        # STEP 1: Locate the main gravity pipe network
        # ------------------------------------------------------------------
        # The network identified by IN[0] drives all alignment generation.
        # We validate it has the expected gravity-network methods.
        target_net = None
        for oid in civdoc.GetPipeNetworkIds():
            net = tr.GetObject(oid, OpenMode.ForRead)
            if (getattr(net, "Name", "") == network_name
                    and hasattr(net, "GetStructureIds")
                    and hasattr(net, "GetPipeIds")):
                target_net = net
                break
        if target_net is None:
            raise Exception(f'Pipe Network "{network_name}" not found or missing GetStructureIds/GetPipeIds.')

        # ------------------------------------------------------------------
        # STEP 1b: Enumerate ALL network names in the drawing (diagnostic)
        # ------------------------------------------------------------------
        # Populated into results["AvailableNetworks"] so you can read the
        # exact names from the Watch node and copy them into IN[10] / IN[11].
        avail_gravity   = []
        avail_pressure  = []
        for oid in civdoc.GetPipeNetworkIds():
            try:
                n = tr.GetObject(oid, OpenMode.ForRead)
                avail_gravity.append(getattr(n, "Name", str(oid)))
            except:
                pass
        if HAS_PRESSURE:
            try:
                all_press = CivilDocumentPressurePipesExtension.GetPressurePipeNetworkIds(civdoc)
                for oid in all_press:
                    try:
                        pn = tr.GetObject(oid, OpenMode.ForRead)
                        avail_pressure.append(getattr(pn, "Name", str(oid)))
                    except:
                        pass
            except:
                pass
        results["AvailableNetworks"] = {
            "Gravity":  sorted(avail_gravity),
            "Pressure": sorted(avail_pressure)
        }

        # ------------------------------------------------------------------
        # STEP 2: Compute grid placement origin
        # ------------------------------------------------------------------
        # Profile views are placed in a grid to the right of (and above) the
        # network extents so they do not overlap the pipe network in plan.
        minx, miny, maxx, maxy = compute_network_extents(tr, target_net)
        base_x = maxx + MARGIN_X
        base_y = maxy + MARGIN_Y

        # ------------------------------------------------------------------
        # STEP 3: Resolve styles, label sets, and data sources
        # ------------------------------------------------------------------

        # Ensure the temporary layer exists (used to host the seed polyline)
        layer_id = ensure_layer(tr, TEMP_LAYER)

        # Alignment style and label set
        align_style_id, _ = get_style_id_by_name_or_first(
            civdoc.Styles.AlignmentStyles,
            DESIRED_ALIGNMENT_STYLE, results["Warnings"], "Alignment Style"
        )
        align_labelset_id, _ = get_style_id_by_name_or_first(
            civdoc.Styles.LabelSetStyles.AlignmentLabelSetStyles,
            DESIRED_ALIGNMENT_LABELSET, results["Warnings"], "Alignment Label Set"
        )

        # Existing Ground (EG) surface — optional; skip profile creation if absent
        surface_id = find_surface_id_by_name(tr, SURFACE_NAME)
        if SURFACE_NAME and surface_id == ObjectId.Null:
            raise Exception(f'Surface "{SURFACE_NAME}" not found. Check the surface name.')

        # Profile style and label set — use first available in the drawing
        prof_style_ids    = list(civdoc.Styles.ProfileStyles.ToObjectIds())
        prof_labelset_ids = list(civdoc.Styles.LabelSetStyles.ProfileLabelSetStyles.ToObjectIds())
        if not prof_style_ids or not prof_labelset_ids:
            raise Exception("Profile Styles or Profile Label Set Styles not found in drawing (import from template).")
        profile_style_id    = prof_style_ids[0]
        profile_labelset_id = prof_labelset_ids[0]

        # Profile View style and band set
        pv_style_id, _ = get_style_id_by_name_or_first(
            civdoc.Styles.ProfileViewStyles,
            PROFILEVIEW_STYLE_NAME, results["Warnings"], "Profile View Style"
        )
        bandset_id, _ = get_style_id_by_name_or_first(
            civdoc.Styles.ProfileViewBandSetStyles,
            BANDSET_NAME, results["Warnings"], "Profile View Band Set"
        )

        # ------------------------------------------------------------------
        # ------------------------------------------------------------------
        # STEP 3b: Label style diagnostic
        # Crossing label styles — resolved once here to avoid repeated lookups
        # in the inner alignment loop. ObjectId.Null means no labels created.
        gravity_label_style_id = get_gravity_crossing_label_style_id(
            GRAVITY_CROSSING_LABEL_STYLE, results["Warnings"]
        )
        pressure_label_style_id = (
            get_pressure_crossing_label_style_id(PRESSURE_CROSSING_LABEL_STYLE, results["Warnings"])
            if HAS_PRESSURE else ObjectId.Null
        )

        # Band data source — the pipe network whose data populates the PV bands
        datasource_id = ObjectId.Null
        if BAND_DATASOURCE_NAME:
            for oid in civdoc.GetPipeNetworkIds():
                n = tr.GetObject(oid, OpenMode.ForRead)
                if getattr(n, "Name", "") == BAND_DATASOURCE_NAME:
                    datasource_id = oid
                    break
            pass  # band datasource not found — bands will be empty but not fatal

        # ------------------------------------------------------------------
        # STEP 4: Collect crossing network ObjectIds
        # ------------------------------------------------------------------

        # Gravity crossing networks — iterate all gravity pipe networks and
        # match by name against the normalised IN[10] list
        gravity_cross_ids = []
        if GRAVITY_CROSS_NET_NAMES:
            wanted = set([n.lower() for n in GRAVITY_CROSS_NET_NAMES])
            found_grav = set()
            for oid in civdoc.GetPipeNetworkIds():
                n = tr.GetObject(oid, OpenMode.ForRead)
                nm = getattr(n, "Name", "").strip()
                if nm.lower() in wanted:
                    gravity_cross_ids.append(oid)
                    found_grav.add(nm.lower())
            pass  # unmatched gravity network names are silently ignored

        # Pressure crossing networks — uses the pressure-API helper
        pressure_cross_ids = get_pressure_network_ids_by_names(
            tr, PRESSURE_CROSS_NET_NAMES, results["Warnings"]
        )


        # ------------------------------------------------------------------
        # STEP 5: Build a pipe connectivity map for the main network
        # ------------------------------------------------------------------
        # conn maps each structure ObjectId → list of (pipe_id, start_id, end_id)
        # This lets us quickly find all pipes connected to each IC structure
        # without iterating the full network for every IC.
        conn = {}
        for pid in target_net.GetPipeIds():
            p = tr.GetObject(pid, OpenMode.ForRead)
            st_id, en_id = get_pipe_end_structure_ids(p)
            if st_id is None or en_id is None:
                continue
            conn.setdefault(st_id, []).append((pid, st_id, en_id))
            conn.setdefault(en_id, []).append((pid, st_id, en_id))

        # ------------------------------------------------------------------
        # STEP 6: Find all IC structures and export to CSV
        # ------------------------------------------------------------------
        ic_rows = [["StructureName", "X", "Y", "Z"]]  # CSV header row
        ic_ids  = []
        for sid in target_net.GetStructureIds():
            s     = tr.GetObject(sid, OpenMode.ForRead)
            sname = getattr(s, "Name", "")
            if sname.startswith(ic_prefix):
                ic_ids.append(sid)
                pt = try_get_point3d(s)
                if pt:
                    ic_rows.append([sname, pt.X, pt.Y, pt.Z])
                else:
                    ic_rows.append([sname, None, None, None])

        results["IC_Count"] = len(ic_ids)

        # Apply test limit — slice ic_ids to the first N entries if IN[15] > 0
        if TEST_LIMIT > 0:
            ic_ids = ic_ids[:TEST_LIMIT]

        # Write CSV to disk (folder is created if it does not exist)
        with open(resolve_csv_path(out_path), "w", newline="", encoding="utf-8") as f:
            csv.writer(f).writerows(ic_rows)
        results["CSV"] = resolve_csv_path(out_path)

        # ------------------------------------------------------------------
        # STEP 7: Prepare model space and grid layout tracker
        # ------------------------------------------------------------------
        ms_id = SymbolUtilityServices.GetBlockModelSpaceId(db)
        ms    = tr.GetObject(ms_id, OpenMode.ForWrite)

        # place dict tracks the current insertion point for the next profile view.
        # Profile views are arranged left-to-right in rows of COLUMNS each.
        place = {"x": base_x, "y": base_y, "row_h": 0.0, "col": 0}

        def next_grid_position(pv_w, pv_h):
            """Advance the grid cursor after placing a profile view of size pv_w × pv_h."""
            place["row_h"] = max(place["row_h"], pv_h)
            place["col"] += 1
            if place["col"] >= COLUMNS:
                # Start a new row below the current one
                place["col"]  = 0
                place["x"]    = base_x
                place["y"]    = place["y"] - (place["row_h"] + SPACING_Y)
                place["row_h"] = 0.0
            else:
                # Advance right within the current row
                place["x"] = place["x"] + (pv_w + SPACING_X)

        # Cache existing alignment names to prevent Civil 3D duplicate-name errors
        existing_align_names = set()
        for aid in civdoc.GetAlignmentIds():
            a = tr.GetObject(aid, OpenMode.ForRead)
            existing_align_names.add(a.Name)

        # ==================================================================
        # STEP 8: Main loop — one alignment + profile view per IC pipe
        # ==================================================================
        # For each IC structure, we iterate its connected pipes. Each pipe
        # becomes its own alignment and profile view. ICs with multiple
        # connected pipes produce multiple profile views (one per pipe).
        # ==================================================================
        for sid in ic_ids:
            start_struct = tr.GetObject(sid, OpenMode.ForRead)
            start_name   = getattr(start_struct, "Name", "")

            connected = conn.get(sid, [])
            if not connected:
                # No pipe found connected to this IC — skip with a note
                results["Skipped"].append(f"{start_name} (no connected pipe found)")
                continue

            for (pipe_id, st_id, en_id) in connected:
                # Determine the structure at the other end of this pipe
                other_sid  = en_id if st_id == sid else st_id
                end_struct = tr.GetObject(other_sid, OpenMode.ForRead)

                # Get pipe endpoints; fall back to structure positions if needed
                pipe_obj  = tr.GetObject(pipe_id, OpenMode.ForRead)
                sp, ep    = get_pipe_points(pipe_obj)
                if sp is None or ep is None:
                    sp = try_get_point3d(start_struct)
                    ep = try_get_point3d(end_struct)
                if sp is None or ep is None:
                    results["Skipped"].append(
                        f"{start_name} -> {getattr(end_struct, 'Name', '')} (no coordinates)"
                    )
                    continue

                # --------------------------------------------------------------
                # 8a. Create a temporary 2-vertex polyline as the alignment seed
                # --------------------------------------------------------------
                # Civil 3D's Alignment.Create(PolylineOptions) requires an
                # existing AutoCAD polyline in model space. EraseExistingEntities
                # is set to True so Civil 3D cleans it up after conversion.
                pl = Polyline()
                pl.AddVertexAt(0, Point2d(sp.X, sp.Y), 0.0, 0.0, 0.0)
                pl.AddVertexAt(1, Point2d(ep.X, ep.Y), 0.0, 0.0, 0.0)
                pl.Layer = TEMP_LAYER

                pl_id = ms.AppendEntity(pl)
                tr.AddNewlyCreatedDBObject(pl, True)

                # --------------------------------------------------------------
                # 8b. Create the alignment
                # --------------------------------------------------------------
                aln_name = build_unique_name(existing_align_names, f"Alignment - {start_name}")

                plops = PolylineOptions()
                plops.PlineId                 = pl_id
                plops.AddCurvesBetweenTangents = False
                plops.EraseExistingEntities    = True  # Civil 3D erases the seed polyline

                aln_id = Alignment.Create(
                    civdoc, plops, aln_name, SITE_ID,
                    layer_id, align_style_id, align_labelset_id
                )
                aln = tr.GetObject(aln_id, OpenMode.ForWrite)
                results["AlignmentsCreated"].append({"Alignment": aln_name})

                # --------------------------------------------------------------
                # 8c. Create the EG surface profile (optional)
                # --------------------------------------------------------------
                surface_profile_id = ObjectId.Null
                if surface_id != ObjectId.Null:
                    prof_name = f"EG - {SURFACE_NAME}"
                    surface_profile_id = Profile.CreateFromSurface(
                        prof_name, aln_id, surface_id,
                        aln.LayerId, profile_style_id, profile_labelset_id
                    )
                    results["ProfilesCreated"].append({"Alignment": aln_name, "Profile": prof_name})

                # --------------------------------------------------------------
                # 8d. Create the profile view
                # create_profile_view_unique retries with an integer suffix if
                # the base name already exists (avoids ArgumentException).
                # --------------------------------------------------------------
                insert_pt         = Point3d(place["x"], place["y"], 0.0)
                pv_base           = f"PV - {aln_name}"
                pv_id, pv_name    = create_profile_view_unique(
                    aln_id, insert_pt, bandset_id, pv_style_id, pv_base
                )
                pv = tr.GetObject(pv_id, OpenMode.ForWrite)

                # Wire the band data sources to this profile view
                set_band_inputs(pv, datasource_id, surface_profile_id, results["Warnings"])

                # --------------------------------------------------------------
                # 8e. Add the main pipe and its two end-structures to the PV
                # (sets Draw = Yes for the subject pipe in the profile view)
                # --------------------------------------------------------------
                add_parts_to_profile_view(tr, [pipe_id, sid, other_sid], pv_id, results["Warnings"])

                # sp/ep are the alignment start/end points — used directly in
                # the 2-D segment intersection test (no GetPolyline() needed).

                # --------------------------------------------------------------
                # 8f. Gravity crossing detection and annotation
                # --------------------------------------------------------------
                # Scan every pipe in each gravity crossing network. A pipe is
                # a crossing if its centreline intersects the alignment polyline
                # in plan and it does not run alongside the alignment.
                # Both the pipe AND its end-structures are added to the PV so
                # the full crossing geometry is visible. Only pipes (not
                # structures) receive crossing label annotations.
                # --------------------------------------------------------------
                gravity_crossing_pipe_ids = set()  # pipes only — for label creation
                if gravity_cross_ids:
                    crossing_ids = set()
                    grav_found_per_net = {}
                    for gnet_id in gravity_cross_ids:
                        try:
                            gnet = tr.GetObject(gnet_id, OpenMode.ForRead)
                            net_nm = getattr(gnet, "Name", str(gnet_id))
                            pipes_found = 0
                            for gpid in gnet.GetPipeIds():
                                gp = tr.GetObject(gpid, OpenMode.ForRead)
                                gsp, gep = get_pipe_points(gp)
                                if gsp is None or gep is None:
                                    continue
                                if is_pipe_crossing(aln, sp, ep, gsp, gep, ON_ALIGN_TOL):
                                    crossing_ids.add(gpid)
                                    gravity_crossing_pipe_ids.add(gpid)
                                    pipes_found += 1
                                    s1, s2 = get_pipe_end_structure_ids(gp)
                                    if s1 and s1 != ObjectId.Null: crossing_ids.add(s1)
                                    if s2 and s2 != ObjectId.Null: crossing_ids.add(s2)
                            grav_found_per_net[net_nm] = pipes_found
                        except:
                            pass

                    # Record diagnostic info: how many pipes were found per network
                    results["Crossings"]["Found"].append({
                        "Alignment":      aln_name,
                        "Type":           "Gravity",
                        "PipesFound":     grav_found_per_net,  # {network_name: count}
                        "TotalPartsAdded": len(crossing_ids)   # pipes + structures
                    })

                    # Remove the subject pipe and its structures from the
                    # crossing set so they are not double-added to the PV
                    for oid in (pipe_id, sid, other_sid):
                        crossing_ids.discard(oid)
                        gravity_crossing_pipe_ids.discard(oid)

                    # Add all detected crossing parts (pipes + structures).
                    # Capture the {pipe_oid: pvpart_oid} map — CrossingPipeProfileLabel
                    # requires a ProfileViewPart ObjectId, not the raw pipe ObjectId.
                    pvpart_map = add_parts_to_profile_view(
                        tr, list(crossing_ids), pv_id, results["Warnings"]
                    )

                    # Build list of ProfileViewPart IDs for crossing pipes only
                    grav_pvpart_ids = [
                        pvpart_map[pid]
                        for pid in gravity_crossing_pipe_ids
                        if pid in pvpart_map
                    ]
                    if len(grav_pvpart_ids) < len(gravity_crossing_pipe_ids):
                        results["Warnings"].append(
                            f"{aln_name}: {len(gravity_crossing_pipe_ids) - len(grav_pvpart_ids)} "
                            f"gravity crossing pipes have no ProfileViewPart ID "
                            f"(AddToProfileView may have returned void on this Civil 3D version)."
                        )

                    # Create annotation labels using ProfileViewPart IDs
                    n_labels = create_crossing_pipe_labels(
                        tr, grav_pvpart_ids, pv_id,
                        gravity_label_style_id, results["Warnings"],
                        use_pressure_api=False
                    )
                    if n_labels:
                        results["Crossings"]["LabelsCreated"].append({
                            "Alignment": aln_name, "Type": "Gravity", "Count": n_labels
                        })

                # --------------------------------------------------------------
                # 8g. Pressure crossing detection and annotation
                # --------------------------------------------------------------
                # Same logic as gravity crossings but targets pressure pipe
                # networks (ME, GAS, Water, etc.). Uses get_pipe_points() for
                # robust endpoint access across different pressure pipe types.
                # Structures are not added for pressure networks because
                # pressure networks use fittings rather than Civil 3D structures.
                # --------------------------------------------------------------
                if HAS_PRESSURE and pressure_cross_ids:
                    pressure_pipe_ids_to_add = set()
                    pipe_to_station = {}        # {raw_pipe_oid: station_along_alignment}
                    press_found_per_net = {}
                    for pnet_id in pressure_cross_ids:
                        try:
                            pnet = tr.GetObject(pnet_id, OpenMode.ForRead)
                            net_nm = getattr(pnet, "Name", str(pnet_id))
                            pipes_found = 0
                            for ppid in pnet.GetPipeIds():
                                pp = tr.GetObject(ppid, OpenMode.ForRead)
                                # Use the robust helper (tries StartPoint/EndPoint and other attrs)
                                sp2, ep2 = get_pipe_points(pp)
                                if sp2 is None or ep2 is None:
                                    continue
                                if is_pipe_crossing(aln, sp, ep, sp2, ep2, ON_ALIGN_TOL):
                                    pressure_pipe_ids_to_add.add(ppid)
                                    pipes_found += 1
                                    # Compute station along alignment for the crossing point
                                    ipt = _segment_intersection_2d(
                                        sp.X, sp.Y, ep.X, ep.Y,
                                        sp2.X, sp2.Y, ep2.X, ep2.Y)
                                    if ipt is not None:
                                        try:
                                            st_val, _ = station_offset(aln, ipt[0], ipt[1])
                                            pipe_to_station[ppid] = st_val
                                        except:
                                            pipe_to_station[ppid] = 0.0
                            press_found_per_net[net_nm] = pipes_found
                        except:
                            pass

                    # Diagnostic output for pressure crossings
                    results["Crossings"]["Found"].append({
                        "Alignment":       aln_name,
                        "Type":            "Pressure",
                        "PipesFound":      press_found_per_net,  # {network_name: count}
                        "TotalPartsAdded": len(pressure_pipe_ids_to_add)
                    })

                    # Add detected pressure crossing pipes and capture pvpart map
                    press_pvpart_map = add_pressure_pipes_to_profile_view(
                        tr, list(pressure_pipe_ids_to_add), pv_id, results["Warnings"]
                    )

                    # Build list of ProfileViewPart IDs for pressure crossing pipes
                    # Also keep pvpart→raw_pipe and pvpart→station mappings.
                    press_pvpart_ids = []
                    pvpart_to_raw = {}      # {pvpart_oid: raw_pipe_oid}
                    pvpart_to_station = {}  # {pvpart_oid: station}
                    for pid in pressure_pipe_ids_to_add:
                        if pid in press_pvpart_map:
                            pvpart_oid = press_pvpart_map[pid]
                            press_pvpart_ids.append(pvpart_oid)
                            pvpart_to_raw[pvpart_oid] = pid
                            pvpart_to_station[pvpart_oid] = pipe_to_station.get(pid, 0.0)
                    if len(press_pvpart_ids) < len(pressure_pipe_ids_to_add):
                        results["Warnings"].append(
                            f"{aln_name}: {len(pressure_pipe_ids_to_add) - len(press_pvpart_ids)} "
                            f"pressure crossing pipes have no ProfileViewPart ID."
                        )

                    # Create annotation labels — 4-arg pressure overload needs station
                    n_labels = create_crossing_pipe_labels(
                        tr, press_pvpart_ids, pv_id,
                        pressure_label_style_id, results["Warnings"],
                        use_pressure_api=True,
                        raw_pipe_ids=pvpart_to_raw,
                        pressure_stations=pvpart_to_station
                    )
                    if n_labels:
                        results["Crossings"]["LabelsCreated"].append({
                            "Alignment": aln_name, "Type": "Pressure", "Count": n_labels
                        })

                # --------------------------------------------------------------
                # 8h. Advance the grid cursor
                # --------------------------------------------------------------

                results["ProfileViewsCreated"].append({
                    "Alignment":   aln_name,
                    "ProfileView": pv_name
                })

                # Estimate the profile view width from the alignment length for
                # grid spacing purposes (Civil 3D auto-sizes the PV itself).
                try:
                    aln_len = float(aln.Length)
                except:
                    aln_len = 300.0  # fallback width estimate
                pv_w = max(MIN_PV_WIDTH, min(MAX_PV_WIDTH, aln_len + 100.0))
                pv_h = max(PV_HEIGHT_DEFAULT, 250.0)
                next_grid_position(pv_w, pv_h)

        # Commit all created objects (alignments, profiles, profile views,
        # labels) to the database in a single atomic operation.
        tr.Commit()

    finally:
        tr.Dispose()  # Always dispose the transaction (rolls back if not committed)

finally:
    doc_lock.Dispose()  # Release the document lock acquired at the start

# Return the results dictionary to the Dynamo node output
OUT = results
