import clr
HAS_PRESSURE_LABEL = False
try:
    clr.AddReference("AeccPressurePipesMgd")
    from Autodesk.Civil.DatabaseServices import CrossingPressurePipeProfileLabel
    HAS_PRESSURE_LABEL = True
except Exception:
    CrossingPressurePipeProfileLabel = None

from Autodesk.Civil.DatabaseServices import (CrossingPipeProfileLabel, ProfileView, ProfileViewPart)
from Autodesk.AutoCAD.DatabaseServices import OpenMode, ObjectId


# Pressure PV-part class: resolve locally so this module runs standalone.
# Name TBD by probe — replace ProfileViewPressurePart if the probe says otherwise.
try:
    from Autodesk.Civil.DatabaseServices import ProfileViewPressurePart
    HAS_PVPRESSUREPART = True
except Exception:
    ProfileViewPressurePart = None
    HAS_PVPRESSUREPART = False

import traceback
from automations import helpers_labels as lbl
from automations import helpers_network as net
from automations import duckdb_engine as duck


def run(context, stage6=None):
    """stage6: list of per-PV records (each {main_handle, pv_id, pvpart_gravity,
    pvpart_pressure}) handed over IN-RUN by the orchestrator (fused path).
    If None (standalone run), the pvpart maps are RE-DERIVED from the drawing via
    the ModelSpace scan — the parts were already drawn by a prior Stage-6 run."""
    civdoc, db, tr, IN = context["civdoc"], context["db"], context["tr"], context["IN"]
    data = {"Warnings": [], "Skipped": [], "Items": []}
    try:
        duckdb_path  = IN[0] if (len(IN) > 0 and IN[0]) else ':memory:'
        grav_style_nm = IN[1] if (len(IN) > 1 and IN[1]) else None
        pres_style_nm = IN[2] if (len(IN) > 2 and IN[2]) else None
        handoff       = IN[3] if (len(IN) > 3 and IN[3]) else None   # Stage-6 OUT["Data"]["Handoff"]
        con = duck.connect(duckdb_path)

        grav_style = lbl.resolve_gravity_label_style(civdoc, grav_style_nm, data["Warnings"])
        pres_style = lbl.resolve_pressure_label_style(db, pres_style_nm, data["Warnings"])

        # Three ways to obtain the per-PV records, in preference order:
        #   A) FUSED (orchestrator): stage6 arg carries live pvpart maps in-run.
        #   B) SERIALISED HAND-OFF (standalone): IN[5] = Stage-6 OUT[0] (handles
        #      only); consume_handoff re-opens handles + scans pvparts. Fast, exact.
        #   C) RE-DERIVE (standalone, no hand-off): full ModelSpace re-scan. Slowest,
        #      but needs no state from Stage 6 at all.
        pvpart_cls = ProfileViewPart
        pvpressurepart_cls = ProfileViewPressurePart if HAS_PVPRESSUREPART else None
        has_pres   = (CrossingPressurePipeProfileLabel is not None)
        if stage6 is not None:
            records = stage6                                                    # A
        else:
            records = lbl.consume_handoff(                                      # B
                context, handoff, con, net, pvpart_cls, 
                pvpressurepart_cls,
                data["Warnings"])
            if records is None:
                records = lbl.rederive_pv_records(                             # C
                    context, con, net, pvpart_cls, has_pres, data["Warnings"])
        for pv_rec in records:                      # one record per main pipe / PV
            pv_id   = pv_rec["pv_id"]
            pv_obj  = tr.GetObject(pv_id, OpenMode.ForRead)
            pv_s, pv_e = lbl.get_profile_view_station_range(pv_obj)
            # {cross_handle: pvpart_oid} for gravity and pressure, from Stage 6
            grav_map = pv_rec["pvpart_gravity"]
            pres_map = pv_rec["pvpart_pressure"]
            # main alignment for this PV -> lets us turn (cross_x, cross_y) into a
            # station. Carried in the Stage-6 record (or resolved by name standalone).
            aln = tr.GetObject(pv_rec["alignment_id"], OpenMode.ForRead)

            # The crossings table has NO main_station column; it carries the crossing
            # POINT (cross_x, cross_y). We derive station at label-time from that point
            # on the main alignment -> exact, and no PV-placement data leaks into the
            # detection schema.
            rows = con.execute("""
                SELECT cross_handle, cross_kind, cross_x, cross_y
                FROM crossings
                WHERE main_handle = ? AND runs_alongside = FALSE
            """, [pv_rec["main_handle"]]).fetchall()

            made = 0
            for cross_handle, cross_kind, cross_x, cross_y in rows:
                if cross_kind == 'gravity_cross':
                    if lbl.create_gravity_label(grav_map.get(cross_handle), pv_id,
                                                grav_style, data["Warnings"]):
                        made += 1
                elif cross_kind == 'pressure_cross':
                    station = lbl.station_of_point(aln, cross_x, cross_y, data["Warnings"])
                    ratio = lbl.station_to_ratio(station, pv_s, pv_e)
                    if lbl.create_pressure_label(pres_map.get(cross_handle), pv_id,
                                                 ratio, pres_style,
                                                 HAS_PRESSURE_LABEL, data["Warnings"]):
                        made += 1
            data["Items"].append({"main": pv_rec["main_handle"], "labels": made})
    except Exception as e:
        data["Warnings"].append(str(e))
        data["Warnings"].append(traceback.format_exc())
    return data