import traceback
import clr
import time, os
from Autodesk.AutoCAD.DatabaseServices import SymbolUtilityServices, OpenMode, ObjectId
from Autodesk.Civil.DatabaseServices import ProfileViewPart, ProfileViewPressurePart
from automations import helpers_core as core
from automations import helpers_alignment as al
from automations import helpers_profileview as pvh
from automations import helpers_network as net
from automations import helpers_labels as lbl
from automations import duckdb_engine as duck

HAS_PRESSURE = False
try:
    clr.AddReference("AeccPressurePipesMgd")
    from Autodesk.Civil.ApplicationServices import CivilDocumentPressurePipesExtension
    HAS_PRESSURE = True
except Exception:
    CivilDocumentPressurePipesExtension = None

HAS_PRESSURE_LABEL = False
try:
    clr.AddReference("AeccPressurePipesMgd")
    from Autodesk.Civil.DatabaseServices import CrossingPressurePipeProfileLabel
    HAS_PRESSURE_LABEL = True
except Exception:
    CrossingPressurePipeProfileLabel = None

# ProfileViewPressurePart availability is a SEPARATE guard from HAS_PRESSURE
HAS_PVPRESSUREPART = False
try:
    from Autodesk.Civil.DatabaseServices import ProfileViewPressurePart
    HAS_PVPRESSUREPART = True
except Exception:
    ProfileViewPressurePart = None

from Autodesk.Civil.DatabaseServices import ProfileViewPart   # gravity: essentially always present
HAS_PVPART = True

# pressure guards + ProfileViewPart classes live at module top (see Stage 6).

TEMP_LAYER   = "_TEMP_ALIGN_SEED"
CLASH_LAYER  = "_XING_CLASH_MARKERS"
MAX_PV_W, MAX_PV_H = 1200.0, 400.0
MARGIN_X, MARGIN_Y = 1000.0, 50.0

_T0 = time.time()
_LOG_PATH = None   # set in run()

def _plog(msg):
    """Append a timestamped progress line and flush immediately so it's visible
    via `tail -f` while the node is still running (Dynamo buffers stdout until
    the node returns)."""
    line = f"[{time.time()-_T0:8.1f}s] {msg}"
    try:
        if _LOG_PATH:
            with open(_LOG_PATH, "a", encoding="utf-8") as f:
                f.write(line + "\n")
                f.flush()
                os.fsync(f.fileno())
    except Exception:
        pass


def run(context):
    start_time = time.time()
    civdoc, tr, IN = context["civdoc"], context["tr"], context["IN"]
    db = context["db"]
    data = {"Warnings": [], "Skipped": [], "Items": [], "Counts": {}, "pv_handles": []}
    try:
        main_network = IN[0] if (len(IN) > 0 and IN[0]) else None
        surface_name = IN[1] if (len(IN) > 1 and IN[1]) else None
        duckdb_path  = IN[2] if (len(IN) > 2 and IN[2]) else ':memory:'
        aln_style_nm = IN[3] if (len(IN) > 3 and IN[3]) else None
        aln_labelset_nm = IN[4] if (len(IN) > 4 and IN[4]) else None
        prof_style_nm = IN[5] if (len(IN) > 5 and IN[5]) else None
        prof_labelset_nm = IN[6] if (len(IN) > 6 and IN[6]) else None
        pv_style_nm = IN[7] if (len(IN) > 7 and IN[7]) else None
        bandset_nm = IN[8] if (len(IN) > 8 and IN[8]) else None
        grav_style_nm = IN[9] if (len(IN) > 9 and IN[9]) else None
        pres_style_nm = IN[10] if (len(IN) > 10 and IN[10]) else None
        grav_lblstyle_nm = IN[11] if (len(IN) > 11 and IN[11]) else None
        pres_lblstyle_nm = IN[12] if (len(IN) > 12 and IN[12]) else None
        grav_map_csv = IN[13] if (len(IN) > 13 and IN[13]) else None
        pres_map_csv = IN[14] if (len(IN) > 14 and IN[14]) else None

        global _LOG_PATH, _T0
        _T0 = time.time()
        _LOG_PATH = os.path.join(
            os.path.dirname(duckdb_path) if duckdb_path and duckdb_path != ':memory:' else ".",
            "progress.log")
        # truncate previous run's log
        try:
            open(_LOG_PATH, "w").close()
        except Exception:
            pass

        _plog(f"START connect={duckdb_path}")
        con = duck.connect(duckdb_path)

        # label-style mapping
        from automations import label_mapping as lmap
        grav_style_map, pres_style_map = {}, {}
        if grav_map_csv or pres_map_csv:
            lmap.load_label_maps(con, grav_map_csv, pres_map_csv)
            problems = lmap.check_coverage(con)
            if problems:                          # FAIL-HARD
                raise ValueError("Label-style mapping incomplete: "
                                 + " ".join(problems))
            grav_style_map = lmap.resolve_gravity_style_map(con, civdoc, data["Warnings"])
            pres_style_map = lmap.resolve_pressure_style_map(con, db, data["Warnings"])

        # detection must already be built into `crossings` (Stage 3). Guard it.
        n_x = con.execute("SELECT count(*) FROM crossings").fetchone()[0]
        if main_network is None:
            raise ValueError("main network name (IN[0]) is required.")

        # band data source (a pipe network id, by name)
        datasource_id = ObjectId.Null
        if main_network:
            for oid in civdoc.GetPipeNetworkIds():
                if getattr(tr.GetObject(oid, OpenMode.ForRead), "Name", "") == main_network:
                    datasource_id = oid
                    break

        # --- resolve ONCE ---
        # ms = tr.GetObject(db.CurrentSpaceId, OpenMode.ForWrite)
        ms_id = SymbolUtilityServices.GetBlockModelSpaceId(db)
        ms = tr.GetObject(ms_id, OpenMode.ForWrite)
        seed_layer  = core.ensure_layer(tr, db, TEMP_LAYER)
        clash_layer = core.ensure_layer(tr, db, CLASH_LAYER)
        aln_style, _ = core.get_style_id(civdoc.Styles.AlignmentStyles, aln_style_nm, data["Warnings"], "Alignment Style")
        aln_lblset, _ = core.get_style_id(civdoc.Styles.LabelSetStyles.AlignmentLabelSetStyles, aln_labelset_nm, data["Warnings"], "Alignment Label Set")
        prof_style, _ = core.get_style_id(civdoc.Styles.ProfileStyles, prof_style_nm, data["Warnings"], "Profile Style")
        prof_lblset, _ = core.get_style_id(civdoc.Styles.LabelSetStyles.ProfileLabelSetStyles, prof_labelset_nm, data["Warnings"], "Profile Label Set")
        pv_style, _ = core.get_style_id(civdoc.Styles.ProfileViewStyles, pv_style_nm, data["Warnings"], "Profile View Style")
        bandset, _ = core.get_style_id(civdoc.Styles.ProfileViewBandSetStyles, bandset_nm, data["Warnings"], "Band Set")
        grav_lblstyle = lbl.resolve_gravity_label_style(civdoc, grav_lblstyle_nm, data["Warnings"])
        pres_lblstyle = lbl.resolve_pressure_label_style(db, pres_lblstyle_nm, data["Warnings"])
        surface_id  = core.find_surface_id(tr, civdoc, surface_name)

        # PV-part display styles for CROSSING parts (main parts keep their style).
        # Gravity: civdoc.Styles.PipeStyles (get_Item accessor). Pressure: NOT under
        # civdoc.Styles on this build + write-only .Name -> copy a live pipe's StyleId.
        # IN[5] optional gravity crossing style name; IN[6] optional pressure SOURCE
        # pipe name to copy from (empty -> first pressure pipe). See Stage 6.
        grav_part_style = net.resolve_part_styles(
            civdoc, grav_style_nm, data["Warnings"])
        pres_part_style = None
        if HAS_PRESSURE:
            pres_part_style = net.pressure_style_from_sample(
                tr, civdoc, CivilDocumentPressurePipesExtension.GetPressurePipeNetworkIds,
                data["Warnings"], pipe_name=pres_style_nm)

        h2id = net.build_handle_index(db, tr, civdoc, HAS_PRESSURE,
                                      CivilDocumentPressurePipesExtension, data["Warnings"])
        id2h = {v: k for k, v in h2id.items()}

        # grid seeded from network extents (DuckDB)
        _, _, maxx, maxy = con.execute(
            "SELECT st_xmin(e), st_ymin(e), st_xmax(e), st_ymax(e) "
            "FROM (SELECT st_extent_agg(geom) e FROM structures)").fetchone()
        placer = pvh.GridPlacer(maxx + MARGIN_X, maxy + MARGIN_Y, columns=5)
        aln_names = set(getattr(tr.GetObject(a, OpenMode.ForRead), "Name", "")
                        for a in civdoc.GetAlignmentIds())

        _plog("fetching mains ...")
        mains = con.execute("""
                                SELECT p.handle, p.name, p.start_x, p.start_y, p.end_x, p.end_y,
                                    p.start_handle, p.end_handle
                                FROM pipes p
                                JOIN structures s ON p.start_handle = s.handle
                                WHERE p.role = 'main'
                                AND s.name LIKE 'IC-%'
                                ORDER BY p.name
                            """).fetchall()
        total_mains = len(mains)
        _plog(f"mains fetched: n={total_mains}")

        labels_total = 0
        for idx, (mh, pname, sx, sy, ex, ey, sh, eh) in enumerate(mains, start=1):
            _plog(f"PV {idx}/{total_mains}  pipe={pname!r} mh={mh}  START")
            t_pv = time.time()
            try:
                labels_made = _process_main_pipe(
                    context, con, ms, mh, pname, (sx, sy), (ex, ey), sh, eh, h2id, id2h,
                    dict(seed_layer=seed_layer, clash_layer=clash_layer, aln_names=aln_names,
                         aln_style=aln_style, aln_lblset=aln_lblset, prof_style=prof_style,
                         prof_lblset=prof_lblset, pv_style=pv_style, bandset=bandset,
                         grav_lblstyle=grav_lblstyle, pres_lblstyle=pres_lblstyle,
                         grav_part_style=grav_part_style, pres_part_style=pres_part_style,
                         surface_id=surface_id, placer=placer, datasource_id=datasource_id,
                         grav_style_map=grav_style_map, pres_style_map=pres_style_map),
                    data)
                labels_total += labels_made
                _plog(f"PV {idx}/{total_mains}  pipe={pname!r}  DONE  labels={labels_made}  "
                      f"({time.time()-t_pv:.1f}s)")
            except Exception as e:
                data["Skipped"].append({"pipe": mh, "reason": str(e)})
                _plog(f"PV {idx}/{total_mains}  pipe={pname!r}  SKIPPED  {e}")

        audit_path = _export_audit(con, duckdb_path)
        data["Counts"] = {"main_pipes": len(mains), "crossings": n_x,
                          "labels": labels_total, "audit": audit_path}
    except Exception as e:
        data["Warnings"].append(str(e)); data["Warnings"].append(traceback.format_exc())
    finally:
        end_time = time.time()
        data["Timing"] = {"total": f"{end_time - start_time:.2f} seconds"}
    return data


def _process_main_pipe(context, con, ms, mh, pname, sp, ep, sh, eh, h2id, id2h, R, data):
    """One main pipe: alignment -> EG profile -> PV -> add parts (pvpart map is a
    LOCAL) -> label crossings -> plan markers. Returns labels created."""
    civdoc, tr = context["civdoc"], context["tr"]
    db = context["db"]

    aln_name = core.build_unique_name(R["aln_names"], f"ALN - {pname or mh}")
    aln_id = al.create_alignment_from_points(civdoc, tr, ms, sp, ep, aln_name,
                                             R["seed_layer"], R["aln_style"], R["aln_lblset"])
    aln = tr.GetObject(aln_id, OpenMode.ForRead)
    prof_id = al.create_eg_profile(aln_id, R["surface_id"], aln.LayerId,
                                   R["prof_style"], R["prof_lblset"], f"EG - {aln_name}")
    pv_id, _ = pvh.create_profile_view_unique(aln_id, R["placer"].current(),
                                              R["bandset"], R["pv_style"], f"PV - {aln_name}")
    pv_handle = tr.GetObject(pv_id, OpenMode.ForRead).Handle.ToString()
    data["pv_handles"].append(pv_handle)

    R["placer"].advance(MAX_PV_W, MAX_PV_H)
    pv = tr.GetObject(pv_id, OpenMode.ForWrite)
    pvh.set_band_inputs(pv, R["datasource_id"], prof_id, data["Warnings"])

    # main pipe + structures
    main_ids = [x for x in (h2id.get(mh), h2id.get(sh), h2id.get(eh)) if x and not x.IsNull]
    net.add_parts_to_profile_view(tr, db, main_ids, pv_id, ProfileViewPart, HAS_PVPART, data["Warnings"])

    # crossings for THIS main pipe (detected once, Stage 3)
    rows = con.execute("""SELECT c.cross_handle, c.cross_kind, c.cross_x, c.cross_y,
                                 c.verdict, c.angle_class, c.cross_z,
                                 COALESCE(NULLIF(TRIM(p.description),''),'') AS description
                          FROM crossings c
                          JOIN pipes p ON p.handle = c.cross_handle
                          WHERE c.main_handle=?""", [mh]).fetchall()
    grav_ids = [h2id[h] for h, k, *_ in rows if k == 'gravity_cross' and h in h2id]
    pres_ids = [h2id[h] for h, k, *_ in rows if k == 'pressure_cross' and h in h2id]

    grav_all = list(grav_ids)
    # for gid in grav_ids:
    #     s1, s2 = net.get_pipe_end_structure_ids(tr.GetObject(gid, OpenMode.ForRead))
    #     grav_all += [x for x in (s1, s2) if x and not x.IsNull]

    # pvpart maps: LOCAL variables, re-keyed by cross_handle
    g_map_id = net.add_parts_to_profile_view(tr, db, grav_all, pv_id, ProfileViewPart, HAS_PVPART, data["Warnings"])
    net.set_pvpart_styles(tr, g_map_id, R["grav_part_style"], data["Warnings"])   # crossing gravity parts
    grav_map = {id2h[i]: pv_ for i, pv_ in g_map_id.items() if i in id2h}
    pres_map = {}
    if HAS_PRESSURE and HAS_PVPRESSUREPART and pres_ids:
        p_map_id = net.add_pressure_pipes_to_profile_view(tr, db, pres_ids, pv_id,
                        ProfileViewPressurePart, HAS_PVPRESSUREPART, data["Warnings"])
        net.set_pvpart_styles(tr, p_map_id, R["pres_part_style"], data["Warnings"])  # crossing pressure parts
        pres_map = {id2h[i]: pv_ for i, pv_ in p_map_id.items() if i in id2h}

    pv_s, pv_e = lbl.get_profile_view_station_range(pv)
    made = 0
    label_recs = []                                    # (label_oid, station, cross_z)
    for ch, kind, cx, cy, verdict, angle_class, cross_z, description in rows:
        station = lbl.station_of_point(aln, cx, cy, data["Warnings"])
        if kind == 'gravity_cross':
            loid = lbl.create_gravity_label(grav_map.get(ch), pv_id,
                        R["grav_style_map"].get(description, R["grav_lblstyle"]),  # per-desc, fallback to global
                        data["Warnings"])
            if loid:
                made += 1
                label_recs.append((loid, station, cross_z))
        elif kind == 'pressure_cross':
            ratio = lbl.station_to_ratio(station, pv_s, pv_e)
            if ratio is None:
                data["Warnings"].append("pressure label skipped: null ratio (degenerate PV range)")
                continue
            loid = lbl.create_pressure_label(pres_map.get(ch), pv_id, ratio,
                        R["pres_style_map"].get(description, R["pres_lblstyle"]),
                        HAS_PRESSURE_LABEL, data["Warnings"])
            if loid:
                made += 1
                label_recs.append((loid, station, cross_z))
        _plan_marker(tr, ms, R["clash_layer"], cx, cy, verdict,
                     f"{kind[0].upper()} {angle_class}", data["Warnings"])

    # spread crossing labels outside the grid (after all are created)
    lbl.spread_crossing_labels(tr, pv, label_recs, data["Warnings"])
    return made


from Autodesk.AutoCAD.DatabaseServices import Circle, MText
from Autodesk.AutoCAD.Geometry import Point3d

_VERDICT_COLOR = {"CLASH": 1, "TIGHT": 2, "CLEAR": 3}   # ACI: red / yellow / green
_MARKER_R = 1.5


def _plan_marker(tr, ms, layer_id, x, y, verdict, tag, warnings):
    """Circle + MText at (x, y), colour by verdict. Purely diagnostic; never fatal."""
    try:
        # Guarantee ModelSpace is writable — the caller may hand us a ForRead ms.
        if not ms.IsWriteEnabled:
            ms.UpgradeOpen()
        c = Circle(); c.Center = Point3d(x, y, 0.0); c.Radius = _MARKER_R
        c.LayerId = layer_id
        c.ColorIndex = _VERDICT_COLOR.get(verdict, 7)
        ms.AppendEntity(c); tr.AddNewlyCreatedDBObject(c, True)

        t = MText(); t.Location = Point3d(x + _MARKER_R, y + _MARKER_R, 0.0)
        t.Contents = f"{verdict} | {tag}"; t.TextHeight = _MARKER_R
        t.LayerId = layer_id; t.ColorIndex = _VERDICT_COLOR.get(verdict, 7)
        ms.AppendEntity(t); tr.AddNewlyCreatedDBObject(t, True)
        return True                                  # <-- was missing; success was None
    except Exception as e:
        warnings.append(f"Plan marker failure: {str(e)}")
        warnings.append(traceback.format_exc())
        return False      


def _export_audit(con, duckdb_path):
    """Write the full crossings classification to CSV next to the .duckdb file (or
    cwd if in-memory). Returns the path."""
    import os
    out = os.path.join(os.path.dirname(duckdb_path) if duckdb_path else ".",
                       "crossings_audit.csv")
    con.execute(f"""COPY (
        SELECT main_name, cross_name, cross_net, cross_kind,
               angle_deg, angle_class, main_z, cross_z, dz, verdict,
               runs_alongside, cross_x, cross_y
        FROM crossings
        ORDER BY verdict, angle_class, main_name
    ) TO '{out}' (HEADER, DELIMITER ',')""")
    return out


