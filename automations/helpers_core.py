from Autodesk.AutoCAD.DatabaseServices import OpenMode, LayerTableRecord
from Autodesk.AutoCAD.DatabaseServices import ObjectId

def unwrap_oid(item):
    """Some CPython3 collection accessors return an ObjectId wrapped in a tuple.
    Return the bare ObjectId. Detect the real thing by its IsNull member."""
    if hasattr(item, "IsNull"):        # already a bare ObjectId
        return item
    if isinstance(item, tuple) and item and hasattr(item[0], "IsNull"):
        return item[0]
    return item                        # leave anything else untouched


def ensure_layer(tr, db, layer_name):
    """Return the ObjectId of layer_name, creating it if absent. Unlocks it if
    locked so we can host temporary seed geometry without an exception."""
    lt = tr.GetObject(db.LayerTableId, OpenMode.ForRead)
    for lid in lt:
        ltr = tr.GetObject(lid, OpenMode.ForRead)
        if ltr.Name.lower() == layer_name.lower():
            if ltr.IsLocked:
                tr.GetObject(lid, OpenMode.ForWrite).IsLocked = False
            return lid
    lt.UpgradeOpen()
    rec = LayerTableRecord()
    rec.Name = layer_name
    rec.IsLocked = False
    new_id = lt.Add(rec)
    tr.AddNewlyCreatedDBObject(rec, True)
    return new_id


def get_style_id(style_coll, desired_name, warnings, kind):
    """Resolve a style ObjectId from a collection.
      - desired_name present + found -> that style
      - name missing/not found       -> warn, fall back to the FIRST available
      - collection empty             -> raise (template not set up)
    Returns (ObjectId, resolved_name). Every id is unwrapped."""
    try:
        ids = [unwrap_oid(i) for i in style_coll.ToObjectIds()]
    except Exception as e:
        ids = []
        warnings.append(f"Could not enumerate {kind}s: {e}")
    if not ids:
        raise Exception(f"No {kind} in drawing. Import styles from the template.")
    if desired_name:
        try:
            if style_coll.Contains(desired_name):
                return unwrap_oid(style_coll.get_Item(desired_name)), desired_name
        except Exception:
            pass
        warnings.append(f"{kind} '{desired_name}' not found; using first available.")
    return ids[0], "<FirstAvailable>"


def find_surface_id(tr, civdoc, surface_name):
    """Surface ObjectId by name, or ObjectId.Null if name is empty/not found."""
    if not surface_name:
        return ObjectId.Null
    for sid in civdoc.GetSurfaceIds():
        nm = getattr(tr.GetObject(sid, OpenMode.ForRead), "Name", "")
        if str(nm).strip().lower() == surface_name.strip().lower():
            return sid
    return ObjectId.Null


def build_unique_name(existing_set, base):
    """A name not already in existing_set; append ' 1', ' 2', ... if taken.
    Records the chosen name in existing_set to prevent reuse within a run."""
    if base not in existing_set:
        existing_set.add(base)
        return base
    i = 1
    while True:
        cand = f"{base} ({i})"
        if cand not in existing_set:
            existing_set.add(cand)
            return cand
        i += 1
