# =============================================================================
# _helpers_netframework.py  â€”  extraction + geometry helpers for the DuckDB
# network-analysis capstone. Merge these into your existing automations/_helpers.py
# (kept separate here only so the diff is obvious).
#
# Convention notes (verified on THIS build, 2025-CPython3):
#   * out-params use the HYBRID form: pass dummy args of the right type AND
#     unpack from the return tuple, leading None for a void return.
#       _, st, off = aln.StationOffset(x, y, 0.0, 0.0)
#     `clr.Reference` does NOT exist here -> do NOT use it (docs Recipe 5 is stale).
#   * only PRIMITIVES cross the Civil3D<->DuckDB boundary. PK = Handle hex string.
#   * geometry travels as 2D WKT; z travels as separate attribute columns.
# =============================================================================
import math

# ---- defensive attribute reader (self-documents real member names) ----------
def get_member(obj, name, cast=None, default=None, missing=None):
    """Read obj.name defensively; record failures in `missing` (a set) so the
    extractor tells you which member spellings are wrong on this build."""
    try:
        val = getattr(obj, name)
    except Exception:
        if missing is not None:
            missing.add(name)
        return default
    if cast is not None and val is not None:
        try:
            return cast(val)
        except Exception:
            return default
    return val


def pt_xyz(p):
    """Point3d -> (x, y, z); (None, None, None) on failure."""
    try:
        return (float(p.X), float(p.Y), float(p.Z))
    except Exception:
        return (None, None, None)


def wkt_line(x1, y1, x2, y2):
    if None in (x1, y1, x2, y2):
        return None
    return "LINESTRING({} {}, {} {})".format(x1, y1, x2, y2)


def wkt_point(x, y):
    if None in (x, y):
        return None
    return "POINT({} {})".format(x, y)


def resolve_handle(tr, oid):
    """ObjectId -> Handle hex string (str), or None if null/unreadable."""
    try:
        if oid is not None and not oid.IsNull:
            from Autodesk.AutoCAD.DatabaseServices import OpenMode
            return tr.GetObject(oid, OpenMode.ForRead).Handle.ToString()
    except Exception:
        pass
    return None


# ---- crossing-quality guards (borrowed from Solution 8, applied post-SQL) ----
# DuckDB ST_Intersects finds ALL plan intersections including degenerate ones
# (near-parallel overlaps, touches at a shared structure). These guards reject
# the false positives, matching the geometric rigor of the 2D Solution 8.
MIN_CROSSING_ANGLE_DEG = 20.0   # reject glancing/near-parallel crossings


def crossing_angle_deg(ax1, ay1, ax2, ay2, bx1, by1, bx2, by2):
    """Angle between pipe A and pipe B direction vectors, in [0, 90] degrees."""
    v1 = (ax2 - ax1, ay2 - ay1)
    v2 = (bx2 - bx1, by2 - by1)
    n1 = math.hypot(*v1)
    n2 = math.hypot(*v2)
    if n1 < 1e-9 or n2 < 1e-9:
        return 0.0
    c = (v1[0] * v2[0] + v1[1] * v2[1]) / (n1 * n2)
    return math.degrees(math.acos(max(-1.0, min(1.0, abs(c)))))