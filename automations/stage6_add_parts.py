import clr
import traceback
from Autodesk.AutoCAD.DatabaseServices import OpenMode
from automations import helpers_network as net
from automations import helpers_profileview as pvh
from automations import duckdb_engine as duck

HAS_PRESSURE = False
try:
    clr.AddReference("AeccPressurePipesMgd")
    from Autodesk.Civil.ApplicationServices import CivilDocumentPressurePipesExtension
    HAS_PRESSURE = True
except Exception:
    CivilDocumentPressurePipesExtension = None

# ProfileViewPressurePart availability is a SEPARATE guard from HAS_PRESSURE
HAS_PVPRESSUREPART = False
try:
    from Autodesk.Civil.DatabaseServices import ProfileViewPressurePart
    HAS_PVPRESSUREPART = True
except Exception:
    ProfileViewPressurePart = None

from Autodesk.Civil.DatabaseServices import ProfileViewPart   # gravity: essentially always present
HAS_PVPART = True


def iter_main_pvs(context, con, h2id):
    """Yield (main_handle, pv_id, alignment_id, main_pipe_id, start_struct_id,
    end_struct_id) for each main pipe. Resolves the main pipe + its two structures straight from
    the DuckDB `pipes` row's handle columns via the handle index. pv_id comes from
    the profile view created for this pipe in Stage 5 (looked up by PV name, or
    carried in-run by the orchestrator). Rows whose main handle is absent from the
    drawing are skipped by the caller."""
    civdoc, db, tr = context["civdoc"], context["db"], context["tr"]
    rows = con.execute("""
        SELECT handle, name, start_handle, end_handle
        FROM pipes WHERE role = 'main' ORDER BY name
    """).fetchall()
    for main_handle, pname, sh, eh in rows:
        main_pipe_id = h2id.get(main_handle)
        if main_pipe_id is None:
            continue                                   # deleted since extraction
        s1 = h2id.get(sh)
        s2 = h2id.get(eh)
        aln_name = f"ALN - {pname or main_handle}"
        aln_id = net.find_alignment_id_by_name(tr, civdoc, aln_name)
        pv_id = pvh.find_profile_view_id_by_name(tr, db, f"PV - {aln_name}")
        if pv_id is None or aln_id is None:
            continue                                   # PV/alignment not built (Stage 5 skip)
        yield main_handle, pv_id, aln_id, main_pipe_id, s1, s2


def run(context):
    civdoc, tr, IN = context["civdoc"], context["tr"], context["IN"]
    db = context["db"]                       # AutoCAD Database (Recipe 7/8 contract)
    data = {"Warnings": [], "Skipped": [], "Items": []}
    try:
        duckdb_path = IN[0] if (len(IN) > 0 and IN[0]) else ':memory:'
        grav_style_name = IN[1] if (len(IN) > 1 and IN[1]) else None
        pres_style_name = IN[2] if (len(IN) > 2 and IN[2]) else None
        con = duck.connect(duckdb_path)      # path -> persistent file; None -> in-memory ETL

        # Resolve PV-part styles ONCE before the loop.
        # NB: StylesRoot has NO `ProfileViewPartStyles`. The style that governs how a
        # pipe/structure DRAWS in a profile view is the network part style itself,
        # under civdoc.Styles.PipeStyles / .StructureStyles (pressure: PressurePipeStyles).
        # `net.resolve_part_styles` probes what this build actually exposes and returns
        # the collection object + a chosen style id; None style_name -> first/default.
        grav_pvpart_style = net.resolve_part_styles(civdoc, grav_style_name, data["Warnings"])
        
        # pressure: cannot resolve by name on this build -> borrow a live StyleId.
        # IN[2] (pres_style_name) is reinterpreted as an optional pressure-pipe NAME to
        # copy the style FROM; empty -> first pressure pipe's style.
        pres_pvpart_style = None
        if HAS_PRESSURE:
            pres_pvpart_style = net.pressure_style_from_sample(
                tr, civdoc,
                CivilDocumentPressurePipesExtension.GetPressurePipeNetworkIds,
                data["Warnings"],
                pipe_name=pres_style_name)

        h2id = net.build_handle_index(db, tr, civdoc, HAS_PRESSURE,
                                      CivilDocumentPressurePipesExtension, data["Warnings"])

        for main_handle, pv_id, aln_id, main_pipe_id, s1, s2 in iter_main_pvs(context, con, h2id):
            # 1) main pipe + its two structures
            main_ids = [x for x in (main_pipe_id, s1, s2) if x and not x.IsNull]
            pvpart_main = net.add_parts_to_profile_view(
                tr, db, main_ids, pv_id, ProfileViewPart, HAS_PVPART, data["Warnings"])

            # net.set_pvpart_styles(tr, pvpart_main, grav_pvpart_style, data["Warnings"])

            # 2) crossings from DuckDB (detected once, in Stage 3)
            rows = con.execute("""SELECT cross_handle, cross_kind FROM crossings
                                  WHERE main_handle = ? AND runs_alongside = FALSE""",
                               [main_handle]).fetchall()
            grav_handles = [h for h, k in rows if k == 'gravity_cross' and h in h2id]
            pres_handles = [h for h, k in rows if k == 'pressure_cross' and h in h2id]
            grav_ids = [h2id[h] for h in grav_handles]
            pres_ids = [h2id[h] for h in pres_handles]

            # attach each gravity crossing pipe's end structures too. get_pipe_end_
            # structure_ids takes the OPENED pipe object, so we open it first.
            grav_all = list(grav_ids)
            # for gid in grav_ids:
            #     pipe_obj = tr.GetObject(gid, OpenMode.ForRead)
            #     s_start, s_end = net.get_pipe_end_structure_ids(pipe_obj)
            #     grav_all += [x for x in (s_start, s_end) if x and not x.IsNull]

            pvpart_grav = net.add_parts_to_profile_view(
                tr, db, grav_all, pv_id, ProfileViewPart, HAS_PVPART, data["Warnings"])

            net.set_pvpart_styles(tr, pvpart_grav, grav_pvpart_style, data["Warnings"])

            pvpart_pres = {}
            pres_stats = {"requested": 0, "returned": 0, "missing": 0, "missing_ids": []}
            if HAS_PRESSURE and HAS_PVPRESSUREPART and pres_ids:
                pvpart_pres = net.add_pressure_pipes_to_profile_view(
                    tr, db, pres_ids, pv_id, ProfileViewPressurePart,
                    HAS_PVPRESSUREPART, data["Warnings"])

                net.set_pvpart_styles(tr, pvpart_pres, pres_pvpart_style, data["Warnings"])
                pres_stats = net.pvpart_addition_stats(pres_ids, pvpart_pres)

            # --- add hand-off diagnostic (measured at the RAW add level) ---------
            # Measures coverage of AddToProfileView BEFORE re-keying, so it isolates
            # the "AddToProfileView returned nothing" failure from the separate
            # "re-key dropped a handle" concern below.
            grav_stats = net.pvpart_addition_stats(grav_all, pvpart_grav)

            # Re-key the pvpart maps by cross_handle for Stage 7. Stage 6 builds
            # them {model_oid: pvpart_oid}; Stage 7 looks up by handle. We invert
            # via the handle index (id -> handle) so the hand-off speaks handles.
            # NOTE: end-structure pvparts drop out here on purpose — they have no
            # crossing handle and are not labeled; only crossing PIPE handles survive.
            id2h = {v: k for k, v in h2id.items()}
            grav_by_handle = {id2h.get(mid): pv for mid, pv in pvpart_grav.items() if mid in id2h}
            pres_by_handle = {id2h.get(mid): pv for mid, pv in pvpart_pres.items() if mid in id2h}

            # Re-key LOSS diagnostic: of the crossing PIPES we intended to label,
            # how many survived both the add AND the id->handle re-key? Count against
            # the re-keyed map's KEYS using the crossing HANDLE strings from the query
            # (grav_handles), NOT the ObjectIds in grav_ids. Handle strings are stable
            # dict keys; an ObjectId round-trip (id2h.get(h2id.get(h))) does NOT
            # hash-match across the pythonnet boundary and always missed — that was
            # the bug that reported labelable=0 while the maps were fully populated.
            grav_labelable = sum(1 for h in grav_handles if h in grav_by_handle)
            pres_labelable = sum(1 for h in pres_handles if h in pres_by_handle)
            diagnostics = {
                "grav_add": grav_stats,            # requested/returned/missing at add
                "pres_add": pres_stats,
                "grav_crossings_intended": len(grav_ids),
                "grav_crossings_labelable": grav_labelable,
                "pres_crossings_intended": len(pres_ids),
                "pres_crossings_labelable": pres_labelable,
            }
            if grav_stats["missing"] or pres_stats["missing"] \
                    or grav_labelable < len(grav_ids) or pres_labelable < len(pres_ids):
                data["Warnings"].append(
                    f"PV {main_handle}: add/hand-off gap -> {diagnostics}")

            # In-run hand-off record for the orchestrator (Stage 8) / Stage 7.
            # pv_id + pvpart maps are SESSION objects: kept in Data for the current
            # execution only, never serialized to DuckDB (ObjectIds don't persist).
            data["Items"].append({
                "main_handle": main_handle,
                "pv_id": pv_id,
                "alignment_id": aln_id,              # main alignment (for station calc in Stage 7)
                "pvpart_gravity": grav_by_handle,   # {cross_handle: pvpart_oid}
                "pvpart_pressure": pres_by_handle,   # {cross_handle: pvpressurepart_oid}
                "gravity_crossings": len(grav_by_handle),
                "pressure_crossings": len(pres_by_handle),
                "diagnostics": diagnostics,          # add/hand-off coverage (this stage)
            })
    except Exception as e:
        data["Warnings"].append(str(e))
        data["Warnings"].append(traceback.format_exc())
    return data
