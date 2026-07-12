import traceback
from Autodesk.AutoCAD.DatabaseServices import OpenMode
from automations import helpers_network as net
from automations import duckdb_engine as duck


def run(context):
    civdoc, tr, IN = context["civdoc"], context["tr"], context["IN"]
    data = {"Warnings": [], "Skipped": [], "Items": []}
    missing = set()
    try:
        main_name = IN[0] if len(IN) > 0 and IN[0] else None
        pipes, structs, conns = [], [], []
        for nid in civdoc.GetPipeNetworkIds():
            n = tr.GetObject(nid, OpenMode.ForRead)
            nname = net.get_member(n, "Name", str, "", missing)
            role = "main" if (main_name and nname == main_name) else "gravity_cross"
            pr, cr = net.extract_pipes(tr, n, nname, role, missing, data["Skipped"])
            pipes += pr; conns += cr
            structs += net.extract_structures(tr, n, nname, missing, data["Skipped"])

        con = duck.connect(None)                 # in-memory for the checkpoint
        duck.load_networks(con, pipes, structs)

        if missing:
            data["Warnings"].append("Unresolved members (pin spellings): "
                                    + ", ".join(sorted(missing)))
        data["Counts"] = {"pipes": len(pipes), "structures": len(structs),
                          "networks": len(list(civdoc.GetPipeNetworkIds()))}
        # prove the tables are queryable
        data["Items"] = con.execute(
            "SELECT network, count(*) n FROM pipes GROUP BY network ORDER BY 2 DESC"
        ).fetchall()
    except Exception as e:
        data["Warnings"].append(str(e)); data["Warnings"].append(traceback.format_exc())
    return data