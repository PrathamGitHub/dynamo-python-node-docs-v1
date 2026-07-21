# Description -> crossing-label-style mapping: load CSVs, fail-hard coverage/validation,
# resolve per-description style ObjectIds for gravity + pressure crossing labels.
from automations import helpers_core as core
from automations import helpers_labels as labels


# ---------- 1. LOAD ----------
def load_label_maps(con, gravity_csv, pressure_csv):
    """Load both description->style CSVs into DuckDB map tables.
    utf-8-sig BOM tolerant (Excel), style trimmed, blank style -> NULL,
    description NULL/'' unified to '' so the empty-description bucket is one key.
    Returns (n_gravity_rows, n_pressure_rows)."""
    for tbl, path in (("label_map_gravity", gravity_csv),
                      ("label_map_pressure", pressure_csv)):
        con.execute(f"""CREATE OR REPLACE TABLE "{tbl}" AS
            SELECT COALESCE(NULLIF(TRIM(CAST(description AS VARCHAR)), ''), '') AS description,
                   NULLIF(TRIM(CAST(label_style_name AS VARCHAR)), '')          AS label_style_name
            FROM read_csv_auto('{path}', header=true, all_varchar=true)""")
    g = con.execute('SELECT count(*) FROM label_map_gravity').fetchone()[0]
    p = con.execute('SELECT count(*) FROM label_map_pressure').fetchone()[0]
    return g, p


# ---------- 2. COVERAGE (data-only, fail-hard) ----------
def check_coverage(con):
    """Fail-hard structural coverage. For each role, every DISTINCT crossing-pipe
    description used in `crossings` must have a map row WITH a non-blank style.
    NULL/'' description unified to ''. Returns list[str] of problems (empty = OK)."""
    problems = []
    for role, tbl in (("gravity_cross", "label_map_gravity"),
                      ("pressure_cross", "label_map_pressure")):
        # descriptions actually USED by crossings (join crossings -> pipes on cross_handle)
        rows = con.execute(f"""
            WITH used AS (
                SELECT DISTINCT COALESCE(NULLIF(TRIM(p.description),''),'') AS d,
                       count(*) AS n
                FROM crossings c
                JOIN pipes p ON p.handle = c.cross_handle
                WHERE c.cross_kind = '{role}'
                GROUP BY 1)
            SELECT u.d, u.n,
                   (m.description IS NULL)          AS uncovered,
                   (m.label_style_name IS NULL)     AS blank
            FROM used u
            LEFT JOIN "{tbl}" m ON u.d = m.description
            WHERE m.description IS NULL OR m.label_style_name IS NULL
            ORDER BY u.n DESC""").fetchall()
        tag = role.replace("_cross", "")
        for d, n, uncovered, blank in rows:
            if uncovered:
                problems.append(f"[{tag}] NO mapping row for description {d!r} ({n} crossings)")
            elif blank:
                problems.append(f"[{tag}] style BLANK for description {d!r} ({n} crossings)")
    return problems


# ---------- 3. RESOLVE (style names -> ObjectIds, fail-hard on invalid) ----------
def resolve_gravity_style_map(con, civdoc, warnings):
    """{description -> gravity crossing-label StyleId}. Validates each CSV style name
    against CrossProfileLabelStyles.Contains; unknown name -> hard error."""
    coll = civdoc.Styles.LabelStyles.PipeLabelStyles.CrossProfileLabelStyles
    rows = con.execute("""SELECT description, label_style_name FROM label_map_gravity
                          WHERE label_style_name IS NOT NULL""").fetchall()
    out, bad = {}, []
    for desc, name in rows:
        if coll.Contains(name):
            out[desc] = core.unwrap_oid(coll.get_Item(name))
        else:
            bad.append(name)
    if bad:
        raise ValueError("Gravity label styles not found in drawing "
                         f"(check spelling / import to template): {sorted(set(bad))}")
    return out


def resolve_pressure_style_map(con, db, warnings):
    """{description -> pressure crossing-label StyleId}. Pressure styles are BORROWED
    from placed labels (no collection on this build), so each CSV style name must
    match a placed label's StyleName; unmatched -> hard error telling the user to
    place one label with that style once."""
    rows = con.execute("""SELECT description, label_style_name FROM label_map_pressure
                          WHERE label_style_name IS NOT NULL""").fetchall()

    borrowable, _first = labels.available_pressure_label_styles(db, warnings)

    out, bad = {}, []
    for desc, name in rows:
        # sid = labels.resolve_pressure_label_style(db, name, warnings)   # borrow by StyleName
        sid = borrowable.get(str(name).strip())                         # exact borrow by StyleName
        if sid is None or sid.IsNull:
            bad.append(name)
        else:
            out[desc] = sid
    if bad:
        raise ValueError("Pressure label styles not borrowable (no PLACED label uses "
                        #  f"them — place one crossing label with each style once): {sorted(set(bad))}")
                        f"them — place one crossing label with each style once). "
                        f"Missing requested styles: {sorted(set(bad))}. "
                        f"Available placed styles: {sorted(borrowable.keys())}")
    return out