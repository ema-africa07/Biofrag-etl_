"""Tests for the CorridorModeller."""

import geopandas as gpd
import pytest
from shapely.geometry import LineString

from biofrag.transform.corridors import CorridorModeller


def test_corridors_returns_geodataframe(sample_patches_gdf):
    modeller = CorridorModeller(max_distance_km=200, top_n_pairs=10, min_patch_area_ha=100)
    result = modeller.run(patches_gdf=sample_patches_gdf)
    assert isinstance(result, gpd.GeoDataFrame)


def test_corridors_have_required_columns(sample_patches_gdf):
    modeller = CorridorModeller(max_distance_km=200, top_n_pairs=10, min_patch_area_ha=100)
    result = modeller.run(patches_gdf=sample_patches_gdf)

    if result.empty:
        pytest.skip("No corridors found — adjust distance threshold")

    expected = {"corridor_id", "from_patch_id", "to_patch_id", "cost_distance", "viability"}
    assert expected.issubset(set(result.columns))


def test_viability_values_valid(sample_patches_gdf):
    modeller = CorridorModeller(max_distance_km=200, top_n_pairs=10, min_patch_area_ha=100)
    result = modeller.run(patches_gdf=sample_patches_gdf)

    if result.empty:
        pytest.skip("No corridors found")

    valid = {"high", "medium", "low", "critical"}
    assert set(result["viability"].unique()).issubset(valid)


def test_corridors_are_linestrings(sample_patches_gdf):
    modeller = CorridorModeller(max_distance_km=200, top_n_pairs=10, min_patch_area_ha=100)
    result = modeller.run(patches_gdf=sample_patches_gdf)

    if result.empty:
        pytest.skip("No corridors found")

    assert all(isinstance(g, LineString) for g in result.geometry)


def test_corridors_with_roads_increases_cost(sample_patches_gdf, sample_roads_gdf):
    """Presence of roads should increase mean cost distance."""
    modeller = CorridorModeller(max_distance_km=200, top_n_pairs=10, min_patch_area_ha=100)

    without_roads = modeller.run(patches_gdf=sample_patches_gdf, roads_gdf=None)
    with_roads = modeller.run(patches_gdf=sample_patches_gdf, roads_gdf=sample_roads_gdf)

    if without_roads.empty or with_roads.empty:
        pytest.skip("No corridors found")

    # Match on same patch pairs for fair comparison
    merged = without_roads.merge(
        with_roads[["from_patch_id", "to_patch_id", "cost_distance"]],
        on=["from_patch_id", "to_patch_id"],
        suffixes=("_no_road", "_with_road"),
    )
    if not merged.empty:
        assert merged["cost_distance_with_road"].mean() >= merged["cost_distance_no_road"].mean()
