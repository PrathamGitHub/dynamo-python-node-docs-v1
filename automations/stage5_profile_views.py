import traceback
from Autodesk.AutoCAD.DatabaseServices import SymbolUtilityServices, OpenMode, ObjectId
from Autodesk.AutoCAD.Runtime import RXClass
from Autodesk.Civil.DatabaseServices import ProfileView
from automations import helpers_core as core
from automations import helpers_alignment as al
from automations import helpers_profileview as pvh
from automations import duckdb_engine as duck

TEMP_LAYER = "_TEMP_ALIGN_SEED"
MAX_PV_WIDTH, MAX_PV_HEIGHT = 1200.0, 400.0
MARGIN_X, MARGIN_Y = 1000.0, 50.0


def run(context):
    civdoc, tr, IN = context["civdoc"], context["tr"], context["IN"]
    db = context["db"]                               # AutoCAD Database
    data = {"Warnings": [], "Skipped": [], "Items": []}
    try:
        surface_name  = IN[0] if (len(IN) > 0 and IN[0]) else None
        duckdb_path   = IN[1] if (len(IN) > 1 and IN[1]) else None
        band_net_name = IN[2] if (len(IN) > 2 and IN[2]) else None
        con = duck.connect(duckdb_path)              # reconnect; no live con from stage 3/4

        ms = tr.GetObject(db.CurrentSpaceId, OpenMode.ForWrite)

        # --- resolve ONCE ---
        layer_id = core.ensure_layer(tr, db, TEMP_LAYER)
        aln_style_id, _    = core.get_style_id(civdoc.Styles.AlignmentStyles, None, data["Warnings"], "Alignment Style")
        aln_labelset_id, _ = core.get_style_id(civdoc.Styles.LabelSetStyles.AlignmentLabelSetStyles, None, data["Warnings"], "Alignment Label Set")
        prof_style_id, _   = core.get_style_id(civdoc.Styles.ProfileStyles, None, data["Warnings"], "Profile Style")
        prof_lblset_id, _  = core.get_style_id(civdoc.Styles.LabelSetStyles.ProfileLabelSetStyles, None, data["Warnings"], "Profile Label Set")
        pv_style_id, _     = core.get_style_id(civdoc.Styles.ProfileViewStyles, None, data["Warnings"], "Profile View Style")
        bandset_id, _      = core.get_style_id(civdoc.Styles.ProfileViewBandSetStyles, None, data["Warnings"], "Profile View Band Set")
        surface_id = core.find_surface_id(tr, civdoc, surface_name)

        # band data source (a pipe network id, by name; optional)
        datasource_id = ObjectId.Null
        if band_net_name:
            for oid in civdoc.GetPipeNetworkIds():
                if getattr(tr.GetObject(oid, OpenMode.ForRead), "Name", "") == band_net_name:
                    datasource_id = oid
                    break
        
        # grid seeded beside the network extents
        _, _, maxx, maxy = con.execute("""
                    with e as (select st_extent_agg(geom) extent from structures)
                   select st_xmin(extent), st_ymin(extent), st_xmax(extent), st_ymax(extent) from e;
                """).fetchone()
        placer = pvh.GridPlacer(maxx + MARGIN_X, maxy + MARGIN_Y, columns=5)

        # existing alignment names -> avoid duplicate-name errors
        alignment_names = set(getattr(tr.GetObject(a, OpenMode.ForRead), "Name", "")
                    for a in civdoc.GetAlignmentIds())

        # existing profile view names -> avoid duplicate-name errors
        # 1. Fetch all object IDs in Model Space
        ms_id = SymbolUtilityServices.GetBlockModelSpaceId(db)
        ms = tr.GetObject(ms_id, OpenMode.ForRead)
        # 2. Filter for ProfileView IDs using a single-line list comprehension
        pv_class = RXClass.GetClass(ProfileView)
        profile_view_ids = set(obj_id for obj_id in ms if obj_id.ObjectClass.IsDerivedFrom(pv_class))
        profile_view_names = set(getattr(tr.GetObject(pv_id, OpenMode.ForRead), "Name", "") 
            for pv_id in profile_view_ids)
        
        main_pipes = con.execute("""
            SELECT handle, name, start_x, start_y, end_x, end_y
            FROM pipes WHERE role = 'main' ORDER BY name desc limit 6
        """).fetchall()

        for handle, pname, sx, sy, ex, ey in main_pipes:
            try:
                aln_name = core.build_unique_name(alignment_names, f"ALN - {pname or handle}")
                aln_id = al.create_alignment_from_points(
                    civdoc, tr, ms, (sx, sy), (ex, ey), aln_name,
                    layer_id, aln_style_id, aln_labelset_id)
                aln = tr.GetObject(aln_id, OpenMode.ForRead)
                prof_id = al.create_eg_profile(aln_id, surface_id, aln.LayerId,
                                               prof_style_id, prof_lblset_id, f"EG - {aln_name}")

                pv_name = core.build_unique_name(profile_view_names, f"PV - {aln_name}")
                pv_id, pv_name = pvh.create_profile_view_unique(
                    aln_id, placer.current(), bandset_id, pv_style_id, pv_name)
                placer.advance(MAX_PV_WIDTH, MAX_PV_HEIGHT)

                pv = tr.GetObject(pv_id, OpenMode.ForWrite)
                pvh.set_band_inputs(pv, datasource_id, prof_id, data["Warnings"])

                data["Items"].append({"pipe": handle, "pv": pv_name,
                                      "profile": (None if prof_id.IsNull else f"EG - {aln_name}")})
            except Exception as e:
                data["Skipped"].append({"pipe": handle, "reason": str(e)})

        data["Counts"] = {"main_pipes": len(main_pipes), "profile_views": len(data["Items"])}
    except Exception as e:
        data["Warnings"].append(str(e)); data["Warnings"].append(traceback.format_exc())
    return data
