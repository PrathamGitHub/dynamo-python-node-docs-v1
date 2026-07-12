def unload_package(package_name):
    import sys
    for name in list(sys.modules.keys()):
        if name == package_name or name.startswith(package_name + "."):
            del sys.modules[name]

def _opt_str(src, i, default=""):
    try:
        if len(src) > i and src[i] is not None:
            s = str(src[i]).strip(); return s if s else default
    except Exception: pass
    return default

def _opt_int(src, i, default):
    try:
        if len(src) > i and src[i] is not None: return int(src[i])
    except Exception: pass
    return default

def _opt_float(src, i, default):
    try:
        if len(src) > i and src[i] is not None: return float(src[i])
    except Exception: pass
    return default

def normalize_name_list(x):
    if x is None: return []
    if isinstance(x, str): x = [x]
    seen, out = set(), []
    for item in x:
        if item is None: continue
        s = str(item).strip()
        if s and s.lower() not in seen: seen.add(s.lower()); out.append(s)
    return out

def _ensure_layer(tr, db, name):
    from Autodesk.AutoCAD.DatabaseServices import (OpenMode, LayerTableRecord)

    lt = tr.GetObject(db.LayerTableId, OpenMode.ForRead)
    if lt.Has(name): 
        return f"Layer {name} already exists"
    lt = tr.GetObject(db.LayerTableId, OpenMode.ForWrite)
    rec = LayerTableRecord(); rec.Name = name
    lid = lt.Add(rec); tr.AddNewlyCreatedDBObject(rec, True)
    return f"Layer {name} created with id {lid}"

def cleanup(tr, db, layer_name):
    from Autodesk.AutoCAD.DatabaseServices import (OpenMode, SymbolUtilityServices)

    ms = tr.GetObject(SymbolUtilityServices.GetBlockModelSpaceId(db), OpenMode.ForRead)
    ids = [o for o in ms
           if getattr(tr.GetObject(o, OpenMode.ForRead), "Layer", "") == layer_name]
    n = 0
    for oid in ids:                                    # second pass: safe to erase
        tr.GetObject(oid, OpenMode.ForWrite).Erase(); n += 1
    return n

def get_style_id_or_first(coll, desired, warnings, kind):
    try: ids = list(coll.ToObjectIds())
    except Exception: ids = []
    if not ids:
        raise Exception(f"No {kind} in drawing. Import styles from template.")
    if desired:
        try:
            if coll.Contains(desired): return coll.get_Item(desired), desired
        except Exception: pass
        warnings.append(f'{kind} "{desired}" not found; using first available.')
    return ids[0], "<FirstAvailable>"

# def get_alignment_labelset_id(civdoc, desired_name=None):
#     """Return a valid Alignment label-set style ObjectId.
#     Prefers `desired_name`; else the first available. Raises if none exist."""
#     coll = civdoc.Styles.LabelSetStyles.AlignmentLabelSetStyles
#     return get_style_id_or_first(coll, desired_name, [], "Alignment label set style")
# def get_alignment_labelset_id(civdoc, desired_name=None):
#     coll = civdoc.Styles.LabelSetStyles.AlignmentLabelSetStyles

#     raw = coll.ToObjectIds()
#     print("ToObjectIds type:", type(raw))          # <-- diagnostic
#     ids = list(raw)
#     print("len:", len(ids), "elem0 type:", type(ids[0]) if ids else None)  # <-- diagnostic

#     if not ids:
#         raise Exception("No Alignment label set styles. Import from template.")
#     chosen = ids[0]
#     print("chosen type:", type(chosen))             # <-- diagnostic
#     return chosen

def build_unique_name(existing, base):
    if base not in existing:
        existing.add(base); return base
    i = 1
    while f"{base} {i}" in existing: i += 1
    existing.add(f"{base} {i}"); return f"{base} {i}"

def _pt_of(tr, sid):
    from Autodesk.AutoCAD.DatabaseServices import OpenMode

    s = tr.GetObject(sid, OpenMode.ForRead)
    try:
        p = s.Position; return (p.X, p.Y, p.Z)
    except Exception:
        return (None, None, None)

def station_offset(aln, x, y):
    # pythonnet (CPython 3) has no clr.Reference. For `void StationOffset(
    # x, y, out double station, out double offset)` we pass dummy Doubles for
    # the two out params; their type drives overload resolution and the real
    # values come back as a return tuple (station, offset).
    _, st, off = aln.StationOffset(x, y, 0.0, 0.0)
    return st, off

def point_location(aln, st, off=0.0):
    _, x, y = aln.PointLocation(st, off, 0.0, 0.0)
    return x, y

def endpoint_on_alignment(aln, x, y, tol):           # stretch
    try:
        _, off = station_offset(aln, x, y); return abs(off) <= tol
    except Exception:
        return False

