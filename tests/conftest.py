"""Shared pytest fixtures for Bio-FRAG-ETL tests."""

import uuid
from pathlib import Path

import geopandas as gpd
import numpy as np
import pytest
from shapely.geometry import LineString, Point, Polygon, box


# ── Study area ────────────────────────────────────────────────────────────────

@pytest.fixture
def small_bbox() -> tuple:
    """A small bounding box over South Africa (fast tests)."""
    return (18.0, -34.5, 19.5, -33.0)  # W, S, E, N — Cape Peninsula area


@pytest.fixture
def sadc_bbox() -> tuple:
    """Full SADC bounding box."""
    return (12.0, -35.0, 40.5, -8.0)


# ── Sample GeoDataFrames ──────────────────────────────────────────────────────

@pytest.fixture
def sample_patches_gdf() -> gpd.GeoDataFrame:
    """Five habitat patches of varying sizes."""
    patches = [
        {"patch_id": str(uuid.uuid4()), "area_ha": 5000.0, "perimeter_m": 90000,
         "landcover_class": 10, "landcover_label": "Tree cover",
         "shape_index": 1.2, "fractal_dim": 1.05,
         "geom": box(18.0, -34.0, 18.3, -33.7)},
        {"patch_id": str(uuid.uuid4()), "area_ha": 2500.0, "perimeter_m": 60000,
         "landcover_class": 10, "landcover_label": "Tree cover",
         "shape_index": 1.4, "fractal_dim": 1.08,
         "geom": box(18.5, -34.2, 18.7, -34.0)},
        {"patch_id": str(uuid.uuid4()), "area_ha": 800.0,  "perimeter_m": 35000,
         "landcover_class": 20, "landcover_label": "Shrubland",
         "shape_index": 1.6, "fractal_dim": 1.12,
         "geom": box(19.0, -33.8, 19.2, -33.6)},
        {"patch_id": str(uuid.uuid4()), "area_ha": 1200.0, "perimeter_m": 42000,
         "landcover_class": 30, "landcover_label": "Grassland",
         "shape_index": 1.3, "fractal_dim": 1.07,
         "geom": box(18.2, -33.5, 18.4, -33.3)},
        {"patch_id": str(uuid.uuid4()), "area_ha": 300.0,  "perimeter_m": 20000,
         "landcover_class": 10, "landcover_label": "Tree cover",
         "shape_index": 1.8, "fractal_dim": 1.15,
         "geom": box(18.7, -33.4, 18.85, -33.25)},
    ]
    return gpd.GeoDataFrame(patches, crs="EPSG:4326", geometry="geom")


@pytest.fixture
def sample_roads_gdf() -> gpd.GeoDataFrame:
    """A few road segments crossing the study patches."""
    roads = [
        {"osm_id": 1001, "highway": "primary",   "name": "N2",    "resistance": 0.7,
         "geom": LineString([(18.0, -34.1), (19.5, -34.1)])},
        {"osm_id": 1002, "highway": "secondary",  "name": "R44",   "resistance": 0.5,
         "geom": LineString([(18.5, -34.5), (18.5, -33.0)])},
        {"osm_id": 1003, "highway": "motorway",   "name": "N1",    "resistance": 1.0,
         "geom": LineString([(18.0, -33.9), (19.0, -33.9)])},
    ]
    return gpd.GeoDataFrame(roads, crs="EPSG:4326", geometry="geom")


@pytest.fixture
def sample_wdpa_gdf() -> gpd.GeoDataFrame:
    """Two protected areas."""
    pas = [
        {"wdpa_id": 555001, "name": "Test National Park",
         "iucn_cat": "II", "status": "Designated", "country_name": "South Africa",
         "geom": box(18.0, -34.2, 18.4, -33.8)},
        {"wdpa_id": 555002, "name": "Test Nature Reserve",
         "iucn_cat": "IV", "status": "Designated", "country_name": "South Africa",
         "geom": box(18.6, -34.0, 19.0, -33.6)},
    ]
    return gpd.GeoDataFrame(pas, crs="EPSG:4326", geometry="geom")


@pytest.fixture
def sample_gbif_gdf() -> gpd.GeoDataFrame:
    """Ten species occurrence records."""
    np.random.seed(42)
    n = 10
    lons = np.random.uniform(18.0, 19.5, n)
    lats = np.random.uniform(-34.5, -33.0, n)
    records = [
        {"gbif_key": 900000 + i, "species": f"Species {'AB'[i%2]}",
         "country_code": "ZA", "basis_of_record": "HUMAN_OBSERVATION",
         "geom": Point(lons[i], lats[i])}
        for i in range(n)
    ]
    return gpd.GeoDataFrame(records, crs="EPSG:4326", geometry="geom")
