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

def station_offset(aln, x, y):
    # pythonnet (CPython 3) has no clr.Reference. For `void StationOffset(
    # x, y, out double station, out double offset)` we pass dummy Doubles for
    # the two out params; their type drives overload resolution and the real
    # values come back as a return tuple (station, offset).
    st = 0.0
    off = 0.0
    _, st, off = aln.StationOffset(x, y, st, off)
    return st, off

def point_location(aln, st, off=0.0):
    x = 0.0
    y = 0.0
    _, x, y = aln.PointLocation(st, off, x, y)
    return x, y

def endpoint_on_alignment(aln, x, y, tol):           # stretch
    try:
        _, off = station_offset(aln, x, y); return abs(off) <= tol
    except Exception:
        return False