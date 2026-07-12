# stage3_crossings.py  —  Stage-3 checkpoint (complete)
import clr
import traceback
from Autodesk.AutoCAD.DatabaseServices import OpenMode
from automations import helpers_network as net
from automations import duckdb_engine as duck

try:
    clr.AddReference("AeccPressurePipesMgd")
    from Autodesk.Civil.ApplicationServices import CivilDocumentPressurePipesExtension
    HAS_PRESSURE = True
except Exception:
    HAS_PRESSURE = False
    CivilDocumentPressurePipesExtension = None

def run(context):
    civdoc, tr, IN = context["civdoc"], context["tr"], context["IN"]
    data = {"Warnings": [], "Skipped": [], "Items": []}
    missing = set()
    try:
        main_network = IN[0] if (len(IN) > 0 and IN[0]) else None

        pipes, structs, conns = [], [], []

        # --- gravity networks: main + gravity_cross ---
        gravity_ids = list(civdoc.GetPipeNetworkIds())
        for nid in gravity_ids:
            n = tr.GetObject(nid, OpenMode.ForRead)
            nname = net.get_member(n, "Name", str, "", missing)
            role = "main" if (main_network and nname == main_network) else "gravity_cross"
            pr, cr = net.extract_pipes(tr, n, nname, role, missing, data["Skipped"])
            pipes += pr
            conns += cr
            structs += net.extract_structures(tr, n, nname, missing, data["Skipped"])

        # --- pressure networks: optional, via the extension (may be empty) ---
        pressure_ids = net.get_pressure_network_ids(civdoc, HAS_PRESSURE, CivilDocumentPressurePipesExtension, data["Warnings"])
        for nid in pressure_ids:
            pn = tr.GetObject(nid, OpenMode.ForRead)
            pname = net.get_member(pn, "Name", str, "", missing)
            pipes += net.extract_pressure_pipes(tr, pn, pname, missing, data["Skipped"])

        # --- DuckDB: load then build crossings ONCE ---
        duckdb_path = IN[1] if (len(IN) > 1 and IN[1]) else None
        con = duck.connect(duckdb_path)                       # None = in-memory ETL
        duck.load_networks(con, pipes, structs, conns)

        n_cross = duck.build_crossings(con, main_network,     # raises on wrong/empty name
                                       min_oblique=20.0, clearance=0.30, alongside_edge=0.05)

        if missing:
            data["Warnings"].append("Unresolved members (pin spellings): "
                                    + ", ".join(sorted(missing)))
        data["Counts"] = {
            "pipes": len(pipes), "structures": len(structs),
            "gravity_networks": len(gravity_ids),
            "pressure_networks": len(pressure_ids),
            "crossings": n_cross,                              # build_crossings returns an int
        }
        data["Crossings"] = con.execute("""
            SELECT main_name, cross_name, cross_net, cross_kind,
                   verdict, angle_class, runs_alongside,
                   round(cross_x, 3) AS cross_x, round(cross_y, 3) AS cross_y,
                   round(main_z, 3) AS main_z, round(cross_z, 3) AS cross_z,
                   round(dz, 3) AS dz, round(angle_deg, 1) AS deg
            FROM crossings
            ORDER BY runs_alongside, verdict, dz
        """).fetchall()
        # prove the loaded tables are queryable, per role
        data["Items"] = con.execute("""
            SELECT role, count(*) AS n FROM pipes GROUP BY role ORDER BY 2 DESC
        """).fetchall()
    except Exception as e:
        data["Warnings"].append(str(e))
        data["Warnings"].append(traceback.format_exc())
    return data