import duckdb

#------------------------------------------------------------------------------
# Load portion of the DuckDB engine

_PIPE_COLS = [
    ("handle","TEXT"),("name","TEXT"),("network","TEXT"),("role","TEXT"),
    ("start_handle","TEXT"),("end_handle","TEXT"),
    ("start_x","DOUBLE"),("start_y","DOUBLE"),("start_z","DOUBLE"),
    ("end_x","DOUBLE"),("end_y","DOUBLE"),("end_z","DOUBLE"),
    ("diameter","DOUBLE"),("slope","DOUBLE"),("wkt","TEXT"),
]
_STRUCT_COLS = [
    ("handle","TEXT"),("name","TEXT"),("part_type","TEXT"),("network","TEXT"),
    ("x","DOUBLE"),("y","DOUBLE"),("rim_z","DOUBLE"),("sump_z","DOUBLE"),("wkt","TEXT"),
]
_CONN_COLS = [
    ("pipe_handle","TEXT"),("structure_handle","TEXT"), ("end_type","TEXT"),
]

def connect(db_path=None):
    con = duckdb.connect(db_path if db_path else ":memory:")   # file=EDA, None=ETL
    con.execute("INSTALL spatial;"); con.execute("LOAD spatial;")
    return con


def _arrow_type_map():
    import pyarrow as pa
    return {"TEXT": pa.string(), "DOUBLE": pa.float64(), "INTEGER": pa.int64()}


def create_and_insert(con, table, cols, rows):
    """Create `table` from `cols` and bulk-insert `rows` via a typed Arrow scan."""
    ddl = ", ".join(f'"{c}" {t}' for c, t in cols)
    con.execute(f'CREATE OR REPLACE TABLE "{table}" ({ddl});')
    if not rows:
        return
    import pyarrow as pa
    tmap = _arrow_type_map()
    names = [c for c, _ in cols]
    # typed per-column build so an all-None column keeps its DOUBLE/TEXT type
    # rather than collapsing to arrow 'null' (which breaks INSERT..SELECT).
    arrays = [pa.array([r.get(n) for r in rows], type=tmap.get(t, pa.string()))
              for n, t in cols]
    con.register("_ins", pa.table(arrays, names=names))
    try:
        con.execute(f'INSERT INTO "{table}" SELECT * FROM _ins')
    finally:
        con.unregister("_ins")


def load_networks(con, pipes, structures, connections):
    create_and_insert(con, "pipes_raw", _PIPE_COLS, pipes)
    create_and_insert(con, "structures_raw", _STRUCT_COLS, structures)
    create_and_insert(con, "connections", _CONN_COLS, connections)
    # build geometry columns once, straight off the raw tables
    con.execute("CREATE OR REPLACE TABLE pipes AS "
                "SELECT *, ST_GeomFromText(wkt) AS geom FROM pipes_raw WHERE wkt IS NOT NULL;")
    con.execute("CREATE OR REPLACE TABLE structures AS "
                "SELECT *, ST_GeomFromText(wkt) AS geom FROM structures_raw WHERE wkt IS NOT NULL;")

#------------------------------------------------------------------------------
# Crossings identification portion of the DuckDB engine

_CROSSINGS_SQL = """
WITH cand AS (
  SELECT m.handle AS main_handle, m.name AS main_name, m.network AS main_net,
         m.diameter AS main_dia,
         o.handle AS cross_handle, o.name AS cross_name, o.network AS cross_net,
         o.role   AS cross_kind,                 -- 'gravity_cross' | 'pressure_cross'
         o.diameter AS cross_dia,
         m.start_x AS mx1, m.start_y AS my1, m.start_z AS mz1,
         m.end_x   AS mx2, m.end_y   AS my2, m.end_z   AS mz2,
         o.start_x AS ox1, o.start_y AS oy1, o.start_z AS oz1,
         o.end_x   AS ox2, o.end_y   AS oy2, o.end_z   AS oz2,
         ST_Centroid(ST_Intersection(m.geom, o.geom)) AS ipt
  FROM pipes m
  JOIN pipes o
    ON o.network <> m.network                    -- other networks only (excl. main-vs-main)
   AND ST_Intersects(m.geom, o.geom)
  WHERE m.network = $main                         -- ANCHOR: main network (bound param)
    AND NOT EXISTS (                               -- shared-structure exclusion
        SELECT 1 FROM connections cm JOIN connections co
          ON cm.structure_handle = co.structure_handle
        WHERE cm.pipe_handle = m.handle AND co.pipe_handle = o.handle)
),
xy AS (SELECT *, ST_X(ipt) AS cross_x, ST_Y(ipt) AS cross_y FROM cand),
interp AS (                                        -- z on each pipe by 2D distance ratio
  SELECT *,
    CASE WHEN sqrt((mx2-mx1)*(mx2-mx1)+(my2-my1)*(my2-my1))=0 THEN 0
         ELSE sqrt((cross_x-mx1)*(cross_x-mx1)+(cross_y-my1)*(cross_y-my1))
            / sqrt((mx2-mx1)*(mx2-mx1)+(my2-my1)*(my2-my1)) END AS tm,
    CASE WHEN sqrt((ox2-ox1)*(ox2-ox1)+(oy2-oy1)*(oy2-oy1))=0 THEN 0
         ELSE sqrt((cross_x-ox1)*(cross_x-ox1)+(cross_y-oy1)*(cross_y-oy1))
            / sqrt((ox2-ox1)*(ox2-ox1)+(oy2-oy1)*(oy2-oy1)) END AS to_
  FROM xy
),
geo AS (                                           -- angle computed, not filtered
  SELECT *,
    mz1 + tm*(mz2-mz1) AS main_z,
    oz1 + to_*(oz2-oz1) AS cross_z,
    degrees(acos(least(1.0, greatest(-1.0, abs(
        ((mx2-mx1)*(ox2-ox1) + (my2-my1)*(oy2-oy1))
      / (sqrt((mx2-mx1)*(mx2-mx1)+(my2-my1)*(my2-my1))
       * sqrt((ox2-ox1)*(ox2-ox1)+(oy2-oy1)*(oy2-oy1))))
    )))) AS angle_deg
  FROM interp
)
SELECT main_handle, main_name, main_net, main_dia,
       cross_handle, cross_name, cross_net, cross_kind, cross_dia,
       cross_x, cross_y, main_z, cross_z,
       abs(main_z - cross_z) AS dz,
       angle_deg,
       CASE WHEN angle_deg >= 60 THEN 'PERPENDICULAR'
            WHEN angle_deg >= {min_oblique} THEN 'OBLIQUE'
            ELSE 'NEAR_PARALLEL' END AS angle_class,
       -- runs_alongside: NEAR_PARALLEL AND intersection mid-span on BOTH pipes
       -- (both distance-ratios strictly interior) => parallel neighbour, not a cross
       (angle_deg < {min_oblique}
         AND tm > {edge} AND tm < 1-{edge}
         AND to_ > {edge} AND to_ < 1-{edge}) AS runs_alongside,
       CASE WHEN abs(main_z - cross_z) - (main_dia + cross_dia)/2.0 <= 0 THEN 'CLASH'
            WHEN abs(main_z - cross_z) - (main_dia + cross_dia)/2.0 <  {clear} THEN 'TIGHT'
            ELSE 'CLEAR' END AS verdict
FROM geo
ORDER BY verdict, dz
"""


def main_network_exists(con, main_network):
    row = con.execute("SELECT count(*) FROM pipes WHERE network = ?",
                      [main_network]).fetchone()
    return row[0] > 0


def build_crossings(con, main_network, min_oblique=20.0, clearance=0.30,
                    alongside_edge=0.05):
    """Build the crossings table for ONE main gravity network. Raises if the
    named network has no gravity pipes -- the silent-empty guard."""
    if not main_network:
        raise ValueError("main_network name is required (IN[0]).")
    if not main_network_exists(con, main_network):
        raise ValueError(f"Main network {main_network!r} not found among gravity "
                         f"pipes -- check the name; a wrong name yields 0 crossings.")
    sql = _CROSSINGS_SQL.format(min_oblique=min_oblique, clear=clearance,
                                edge=alongside_edge)
    con.execute(f"CREATE OR REPLACE TABLE crossings AS ({sql})", {"main": main_network})
    return con.execute("SELECT count(*) FROM crossings").fetchone()[0]