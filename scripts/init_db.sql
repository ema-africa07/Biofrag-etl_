-- Bio-FRAG-ETL: PostGIS Schema Initialisation
-- Run once on first startup via docker-entrypoint-initdb.d

CREATE EXTENSION IF NOT EXISTS postgis;
CREATE EXTENSION IF NOT EXISTS postgis_topology;
CREATE EXTENSION IF NOT EXISTS fuzzystrmatch;
CREATE EXTENSION IF NOT EXISTS postgis_tiger_geocoder;

-- ─── SCHEMAS ──────────────────────────────────────────────────────────────────
CREATE SCHEMA IF NOT EXISTS raw;        -- ingested source data, unmodified
CREATE SCHEMA IF NOT EXISTS processed;  -- transformed, analysis-ready
CREATE SCHEMA IF NOT EXISTS outputs;    -- final products (maps, reports)
CREATE SCHEMA IF NOT EXISTS metadata;   -- pipeline run logs, data lineage

-- ─── METADATA ─────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS metadata.pipeline_runs (
    id              SERIAL PRIMARY KEY,
    run_id          UUID NOT NULL DEFAULT gen_random_uuid(),
    pipeline_name   TEXT NOT NULL,
    started_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at     TIMESTAMPTZ,
    status          TEXT CHECK (status IN ('running','success','failed')),
    records_in      INTEGER,
    records_out     INTEGER,
    error_message   TEXT,
    params          JSONB
);

CREATE TABLE IF NOT EXISTS metadata.data_sources (
    id              SERIAL PRIMARY KEY,
    source_name     TEXT UNIQUE NOT NULL,
    source_type     TEXT,              -- api | file | database
    last_fetched_at TIMESTAMPTZ,
    record_count    INTEGER,
    notes           TEXT
);

-- ─── RAW TABLES ───────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS raw.species_occurrences (
    gbif_key        BIGINT PRIMARY KEY,
    species         TEXT,
    kingdom         TEXT,
    phylum          TEXT,
    class           TEXT,
    order_name      TEXT,
    family          TEXT,
    genus           TEXT,
    country_code    CHAR(2),
    dataset_key     UUID,
    occurrence_date DATE,
    basis_of_record TEXT,
    coordinate_uncertainty_m NUMERIC,
    geom            GEOMETRY(Point, 4326),
    raw_json        JSONB,
    ingested_at     TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_species_occ_geom ON raw.species_occurrences USING GIST(geom);
CREATE INDEX IF NOT EXISTS idx_species_occ_species ON raw.species_occurrences(species);

CREATE TABLE IF NOT EXISTS raw.protected_areas (
    wdpa_id         INTEGER PRIMARY KEY,
    name            TEXT,
    orig_name       TEXT,
    desig           TEXT,           -- designation type
    desig_eng       TEXT,
    desig_type      TEXT,
    iucn_cat        TEXT,
    int_crit        TEXT,
    marine          SMALLINT,
    rep_m_area      NUMERIC,
    gis_m_area      NUMERIC,
    rep_area        NUMERIC,
    gis_area        NUMERIC,
    status          TEXT,
    status_yr       SMALLINT,
    gov_type        TEXT,
    own_type        TEXT,
    mang_auth       TEXT,
    mang_plan       TEXT,
    iso3            CHAR(3),
    parent_iso3     CHAR(3),
    country_name    TEXT,
    geom            GEOMETRY(MultiPolygon, 4326),
    ingested_at     TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_wdpa_geom ON raw.protected_areas USING GIST(geom);
CREATE INDEX IF NOT EXISTS idx_wdpa_iucn ON raw.protected_areas(iucn_cat);

CREATE TABLE IF NOT EXISTS raw.road_network (
    osm_id          BIGINT PRIMARY KEY,
    highway         TEXT,
    name            TEXT,
    surface         TEXT,
    lanes           SMALLINT,
    maxspeed        TEXT,
    country_iso3    CHAR(3),
    geom            GEOMETRY(LineString, 4326),
    ingested_at     TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_road_geom ON raw.road_network USING GIST(geom);

-- ─── PROCESSED TABLES ─────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS processed.habitat_patches (
    id              SERIAL PRIMARY KEY,
    patch_id        UUID DEFAULT gen_random_uuid(),
    landcover_class INTEGER,
    landcover_label TEXT,
    area_ha         NUMERIC,
    perimeter_m     NUMERIC,
    shape_index     NUMERIC,      -- perimeter / (2 * sqrt(pi * area))
    fractal_dim     NUMERIC,      -- fractal dimension
    geom            GEOMETRY(Polygon, 4326),
    created_at      TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_patches_geom ON processed.habitat_patches USING GIST(geom);

CREATE TABLE IF NOT EXISTS processed.fragmentation_metrics (
    id              SERIAL PRIMARY KEY,
    grid_cell_id    UUID DEFAULT gen_random_uuid(),
    grid_res_km     NUMERIC,      -- analysis grid resolution
    -- Patch-level
    num_patches     INTEGER,
    total_area_ha   NUMERIC,
    mean_patch_ha   NUMERIC,
    largest_patch_pct NUMERIC,    -- LPI
    -- Edge metrics
    total_edge_m    NUMERIC,
    edge_density    NUMERIC,      -- m/ha
    -- Connectivity
    cohesion        NUMERIC,      -- patch cohesion index 0-100
    aggregation_idx NUMERIC,      -- AI 0-100
    division_idx    NUMERIC,      -- DIVISION 0-1
    -- Spatial
    geom            GEOMETRY(Polygon, 4326),
    computed_at     TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_frag_geom ON processed.fragmentation_metrics USING GIST(geom);

CREATE TABLE IF NOT EXISTS processed.corridors (
    id              SERIAL PRIMARY KEY,
    corridor_id     UUID DEFAULT gen_random_uuid(),
    from_patch_id   UUID REFERENCES processed.habitat_patches(patch_id),
    to_patch_id     UUID REFERENCES processed.habitat_patches(patch_id),
    cost_distance   NUMERIC,
    width_m         NUMERIC,
    length_m        NUMERIC,
    viability       TEXT CHECK (viability IN ('high','medium','low','critical')),
    geom            GEOMETRY(LineString, 4326),
    computed_at     TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_corridors_geom ON processed.corridors USING GIST(geom);

-- ─── OUTPUT VIEWS ─────────────────────────────────────────────────────────────
CREATE OR REPLACE VIEW outputs.fragmentation_hotspots AS
SELECT
    f.*,
    CASE
        WHEN f.division_idx > 0.8 AND f.edge_density > 200 THEN 'critical'
        WHEN f.division_idx > 0.6 AND f.edge_density > 150 THEN 'high'
        WHEN f.division_idx > 0.4 THEN 'medium'
        ELSE 'low'
    END AS threat_level
FROM processed.fragmentation_metrics f;

COMMENT ON TABLE raw.species_occurrences IS 'GBIF occurrence records within study area';
COMMENT ON TABLE raw.protected_areas IS 'WDPA protected area polygons';
COMMENT ON TABLE processed.fragmentation_metrics IS 'Landscape fragmentation indices per grid cell';
COMMENT ON VIEW outputs.fragmentation_hotspots IS 'Fragmentation metrics with derived threat classification';
