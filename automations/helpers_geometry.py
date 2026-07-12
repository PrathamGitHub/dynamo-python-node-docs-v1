"""Geometry primitives: robust point reads, WKT emitters, and the out-parameter
helpers for station/offset. Public API — imported across the project."""


def try_get_point3d(obj):
    """Extract a Point3d by probing the attribute names different Civil 3D types
    use for their location. Returns None if none yield a valid 3-D point.
      Structure -> Position | BlockRef -> InsertionPoint | generic -> Location/Point
    """
    for attr in ("Position", "Location", "InsertionPoint", "Point"):
        if hasattr(obj, attr):
            try:
                pt = getattr(obj, attr)
                if hasattr(pt, "X") and hasattr(pt, "Y") and hasattr(pt, "Z"):
                    return pt
            except Exception:
                pass
    return None


def pt_xyz(p):
    """Point3d -> (x, y, z) floats; (None, None, None) on failure."""
    try:
        return float(p.X), float(p.Y), float(p.Z)
    except Exception:
        return (None, None, None)


def wkt_line(x1, y1, x2, y2):
    if None in (x1, y1, x2, y2):
        return None
    return f"LINESTRING({x1} {y1}, {x2} {y2})"


def wkt_point(x, y):
    if None in (x, y):
        return None
    return f"POINT({x} {y})"


def station_offset(aln, x, y):
    """(x, y) -> (station, offset) on `aln`. Hybrid out-param convention:
    pass dummy Doubles AND unpack from the return tuple (leading None = void).
    Raises PointNotOnEntityException if the point cannot be projected."""
    _, st, off = aln.StationOffset(x, y, 0.0, 0.0)
    return float(st), float(off)


def point_location(aln, st, off=0.0):
    """(station, offset) -> (easting, northing) on `aln`. Hybrid convention."""
    _, x, y = aln.PointLocation(st, off, 0.0, 0.0)
    return float(x), float(y)