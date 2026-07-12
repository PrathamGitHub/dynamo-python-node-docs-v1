# =============================================================================
# exercises/_duckdb_engine.py
# The reusable DuckDB layer for network analysis. NO Civil 3D dependency:
# consumes flat primitive rows, runs spatial + attribute SQL, returns rows.
# This module is the universal piece -- every future network analysis (orphan,
# slope, trace, crossings) loads the SAME schema and adds an SQL template.
#
# EDA mode  -> persistent .duckdb file (audit/inspect later; Rill/DBeaver).
# ETL mode  -> in-memory (fast, disposable, production).
#
# spatial is 2D/planar: ST_Intersects finds PLAN crossings; vertical clash vs.
# clearance is resolved from z carried as attributes + linear interpolation.
# =============================================================================
from pathlib import Path
import sys
sys.path.append(f"{str(Path(__file__).resolve().parent)}/../Site-packages")
from _helpers import unload_package
unload_package("duckdb")
import duckdb

def connect(db_path=None):
    """None -> in-memory (ETL). A path -> persistent file (EDA).
    NOTE: INSTALL needs internet on first use; pre-stage the extension on
    air-gapped machines (see concerns [C4])."""
    con = duckdb.connect(db_path if db_path else ":memory:")
    con.execute("INSTALL spatial;")
    con.execute("LOAD spatial;")
    return con


# common schema â€” every analysis reads these tables ---------------------------
_PIPE_COLS = [
    ("handle", "TEXT"), ("name", "TEXT"),
    ("start_handle", "TEXT"), ("end_handle", "TEXT"),
    ("start_x", "DOUBLE"), ("start_y", "DOUBLE"), ("start_z", "DOUBLE"),
    ("end_x", "DOUBLE"), ("end_y", "DOUBLE"), ("end_z", "DOUBLE"),
    ("diameter", "DOUBLE"), ("slope", "DOUBLE"), ("length2d", "DOUBLE"),
    ("network", "TEXT"), ("wkt", "TEXT"),
]
_STRUCT_COLS = [
    ("handle", "TEXT"), ("name", "TEXT"), ("part_type", "TEXT"),
    ("x", "DOUBLE"), ("y", "DOUBLE"), ("rim_z", "DOUBLE"), ("sump_z", "DOUBLE"),
    ("network", "TEXT"), ("wkt", "TEXT"),
]
_CONN_COLS = [("pipe_handle", "TEXT"), ("structure_handle", "TEXT"), ("end_type", "TEXT")]


def _create(con, table, cols):
    ddl = ", ".join('"{}" {}'.format(c, t) for c, t in cols)
    con.execute('CREATE OR REPLACE TABLE "{}" ({});'.format(table, ddl))



# =============================================================================
# def _insert(con, table, cols, rows):
#     if not rows:
#         return
#     names = [c for c, _ in cols]
#     ph = ", ".join("?" for _ in names)
#     sql = 'INSERT INTO "{}" ({}) VALUES ({})'.format(
#         table, ", ".join('"{}"'.format(n) for n in names), ph)
#     con.executemany(sql, [[r.get(n) for n in names] for r in rows])
# =============================================================================
# FAST insert (replaces the executemany version)
# -----------------------------------------------------------------------------
# WHY: con.executemany(INSERT..., rows) uses DuckDB's row-by-row appender and
# pays per-statement overhead per record -> ~3 kb/s, unusable. DuckDB is
# columnar/vectorized: it wants ONE bulk scan. We build a COLUMNAR Arrow table
# and do a single `INSERT INTO t SELECT * FROM arrow` -> ~100-1000x faster.
#
# Two paths:
#   * PRIMARY  : PyArrow (present in this env). Schema-driven so all-None
#                columns keep their declared type (DOUBLE/TEXT), not arrow null.
#   * FALLBACK : one batched multi-row VALUES statement (no dependency), chunked.
# =============================================================================

# DDL type -> Arrow type. Extend if you add column types to the schema.
_ARROW_TYPES = None  # lazy import guard


def _arrow_type_map():
    import pyarrow as pa
    return {"TEXT": pa.string(), "DOUBLE": pa.float64(),
            "INTEGER": pa.int64(), "BIGINT": pa.int64(), "BOOLEAN": pa.bool_()}


def _insert(con, table, cols, rows):
    """Bulk-load list[dict] `rows` into `table`. Arrow fast-path, VALUES fallback."""
    if not rows:
        return
    try:
        import pyarrow as pa
        tmap = _arrow_type_map()
        names = [c for c, _ in cols]
        # Build each column as a typed Arrow array so an all-None column stays
        # DOUBLE/TEXT rather than collapsing to arrow 'null' (which breaks the
        # INSERT..SELECT into a typed table). See test in handoff notes.
        arrays = [pa.array([r.get(n) for r in rows], type=tmap.get(t, pa.string()))
                  for n, t in cols]
        arrow_tbl = pa.table(arrays, names=names)
        con.register("_ins_arrow", arrow_tbl)
        try:
            con.execute('INSERT INTO "{}" SELECT * FROM _ins_arrow'.format(table))
        finally:
            con.unregister("_ins_arrow")
    except ImportError:
        _insert_values(con, table, cols, rows)


def _insert_values(con, table, cols, rows, chunk=5000):
    """Dependency-free fallback: ONE multi-row VALUES statement per chunk.
    Still ~100x faster than executemany (single parse+execute per chunk).
    DuckDB casts params against the declared column types, so all-None is fine."""
    names = [c for c, _ in cols]
    collist = ", ".join('"{}"'.format(n) for n in names)
    row_ph = "(" + ", ".join("?" for _ in names) + ")"
    for i in range(0, len(rows), chunk):
        batch = rows[i:i + chunk]
        params = []
        for r in batch:
            params.extend(r.get(n) for n in names)
        sql = 'INSERT INTO "{}" ({}) VALUES {}'.format(
            table, collist, ",".join(row_ph for _ in batch))
        con.execute(sql, params)
# =============================================================================

def load(con, extract):
    """Load extract payload ({'pipes','structures','connections'}) + build geom.
    (temp table + executemany) so it runs inside the Dynamo node."""
    _create(con, "pipes_raw", _PIPE_COLS)
    _create(con, "structures_raw", _STRUCT_COLS)
    _create(con, "connections", _CONN_COLS)
    _insert(con, "pipes_raw", _PIPE_COLS, extract["pipes"])
    _insert(con, "structures_raw", _STRUCT_COLS, extract["structures"])
    _insert(con, "connections", _CONN_COLS, extract["connections"])
    con.execute("CREATE OR REPLACE TABLE pipes AS "
                "SELECT *, ST_GeomFromText(wkt) AS geom FROM pipes_raw WHERE wkt IS NOT NULL;")
    con.execute("CREATE OR REPLACE TABLE structures AS "
                "SELECT *, ST_GeomFromText(wkt) AS geom FROM structures_raw WHERE wkt IS NOT NULL;")


def _rows(cur):
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


# --- ANALYSIS TEMPLATE: inter-network crossings + z-classification -----------
# Excludes pairs that share a structure (legitimate shared node, not a crossing)
# via NOT EXISTS on the connections edge list. clearance is a BOUND parameter.
_CLASSIFY_SQL = """
WITH cand AS (
  SELECT a.handle AS pipe_a, a.network AS net_a,
         b.handle AS pipe_b, b.network AS net_b,
         ST_Centroid(ST_Intersection(a.geom, b.geom)) AS ipt,
         a.start_x AS ax1, a.start_y AS ay1, a.start_z AS az1,
         a.end_x   AS ax2, a.end_y   AS ay2, a.end_z   AS az2,
         b.start_x AS bx1, b.start_y AS by1, b.start_z AS bz1,
         b.end_x   AS bx2, b.end_y   AS by2, b.end_z   AS bz2
  FROM pipes a JOIN pipes b
    ON a.network <> b.network AND a.handle < b.handle
   AND ST_Intersects(a.geom, b.geom)
  WHERE NOT EXISTS (          -- exclude pipes meeting at a shared structure
      SELECT 1 FROM connections ca JOIN connections cb
        ON ca.structure_handle = cb.structure_handle
      WHERE ca.pipe_handle = a.handle AND cb.pipe_handle = b.handle)
),
xy AS (SELECT *, ST_X(ipt) AS cross_x, ST_Y(ipt) AS cross_y FROM cand),
interp AS (
  SELECT *,
    CASE WHEN sqrt((ax2-ax1)*(ax2-ax1)+(ay2-ay1)*(ay2-ay1))=0 THEN 0
         ELSE sqrt((cross_x-ax1)*(cross_x-ax1)+(cross_y-ay1)*(cross_y-ay1))
            / sqrt((ax2-ax1)*(ax2-ax1)+(ay2-ay1)*(ay2-ay1)) END AS ta,
    CASE WHEN sqrt((bx2-bx1)*(bx2-bx1)+(by2-by1)*(by2-by1))=0 THEN 0
         ELSE sqrt((cross_x-bx1)*(cross_x-bx1)+(cross_y-by1)*(cross_y-by1))
            / sqrt((bx2-bx1)*(bx2-bx1)+(by2-by1)*(by2-by1)) END AS tb
  FROM xy
)
SELECT pipe_a, net_a, pipe_b, net_b, cross_x, cross_y,
       ax1, ay1, ax2, ay2, bx1, by1, bx2, by2,
       az1 + ta*(az2-az1) AS z_a,
       bz1 + tb*(bz2-bz1) AS z_b,
       abs((az1 + ta*(az2-az1)) - (bz1 + tb*(bz2-bz1))) AS dz,
       CASE WHEN abs((az1 + ta*(az2-az1)) - (bz1 + tb*(bz2-bz1))) < ?
            THEN 'CLASH' ELSE 'CLEARANCE_OK' END AS verdict
FROM interp
ORDER BY dz
"""


def crossings(con, clearance=0.3):
    """Inter-network crossings with clash/clearance verdict (list[dict]).
    Angle/endpoint guards are applied by the caller (see _helpers) so the geom
    columns ax1.. are returned for that post-filter."""
    con.execute(f"CREATE OR REPLACE TABLE crossings AS ({_CLASSIFY_SQL})", [clearance])
    return _rows(con.execute(f"SELECT * FROM crossings"))