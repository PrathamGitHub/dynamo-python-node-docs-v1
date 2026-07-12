import traceback
from Autodesk.AutoCAD.DatabaseServices import OpenMode
from Autodesk.AutoCAD.ApplicationServices.Core import Application
from automations import helpers_core as core
from automations import helpers_alignment as al
from automations import duckdb_engine as duck

TEMP_LAYER = "_TEMP_ALIGN_SEED"


def run(context):
    civdoc, tr, IN = context["civdoc"], context["tr"], context["IN"]
    db = context["db"]                   # AutoCAD Database (Recipe 7/8 contract)
    data = {"Warnings": [], "Skipped": [], "Items": []}
    missing = set()
    try:
        surface_name = IN[0] if (len(IN) > 0 and IN[0]) else None
        duckdb_path = IN[1] if (len(IN) > 1 and IN[1]) else None
        
        # --- DuckDB connection from the pipeline (carried under a DISTINCT key,
        # NOT context["db"] which is the AutoCAD Database). See note below. ---
        con = duck.connect(duckdb_path)                       # None = in-memory ETL

        # --- resolve ONCE ---
        ms = tr.GetObject(db.CurrentSpaceId, OpenMode.ForWrite)
        layer_id = core.ensure_layer(tr, db, TEMP_LAYER)
        aln_style_id, _ = core.get_style_id(civdoc.Styles.AlignmentStyles,
                                            None, data["Warnings"], "Alignment Style")
        aln_labelset_id, _ = core.get_style_id(
            civdoc.Styles.LabelSetStyles.AlignmentLabelSetStyles,
            None, data["Warnings"], "Alignment Label Set")
        surface_id = core.find_surface_id(tr, civdoc, surface_name)
        prof_style_id, _ = core.get_style_id(civdoc.Styles.ProfileStyles,
                                             None, data["Warnings"], "Profile Style")
        prof_labelset_id, _ = core.get_style_id(
            civdoc.Styles.LabelSetStyles.ProfileLabelSetStyles,
            None, data["Warnings"], "Profile Label Set")

        # --- the MAIN pipes to profile, straight from DuckDB (option A) ---
        main_pipes = con.execute("""
            SELECT handle, name, start_x, start_y, end_x, end_y
            FROM pipes WHERE role = 'main' ORDER BY name
        """).fetchall()

        alignment_names = set()
        # Get alignment names from the Civil Document. 
        # Uncomment only if you need to get all the alignment names.
        # It takes too long to get all the alignment names.
        # We are using the main pipe name to create a unique alignment name.

        # alignment_ids = civdoc.GetAlignmentIds()
        # for align_id in alignment_ids:
        #     # Open each alignment object for reading
        #     alignment_obj = tr.GetObject(align_id, OpenMode.ForRead)
        #     # Extract and store the name
        #     alignment_names.add(alignment_obj.Name)
        for handle, pname, sx, sy, ex, ey in main_pipes:
            try:
                aln_name = core.build_unique_name(alignment_names, f"ALN - {pname or handle}")
                aln_id = al.create_alignment_from_points(
                    civdoc, tr, ms, (sx, sy), (ex, ey), aln_name,
                    layer_id, aln_style_id, aln_labelset_id)   # site defaults to Null
                aln = tr.GetObject(aln_id, OpenMode.ForRead)

                prof_id = al.create_eg_profile(
                    aln_id, surface_id, aln.LayerId,
                    prof_style_id, prof_labelset_id, f"EG - {aln_name}")

                data["Items"].append({
                    "pipe": handle, "alignment": aln_name,
                    "profile": (None if prof_id.IsNull else f"EG - {aln_name}"),
                })
            except Exception as e:
                data["Skipped"].append({"pipe": handle, "reason": str(e)})

        data["Counts"] = {"main_pipes": len(main_pipes),
                          "alignments": len(data["Items"])}
    except Exception as e:
        data["Warnings"].append(str(e))
        data["Warnings"].append(traceback.format_exc())
    return data