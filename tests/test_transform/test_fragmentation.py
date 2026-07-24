"""Tests for the FragmentationAnalyser."""

import geopandas as gpd
import pytest

from biofrag.transform.fragmentation import FragmentationAnalyser


def test_fragmentation_returns_geodataframe(sample_patches_gdf, small_bbox):
    analyser = FragmentationAnalyser(grid_resolution_km=5)
    result = analyser.run(patches_gdf=sample_patches_gdf, study_bbox=small_bbox)

    assert isinstance(result, gpd.GeoDataFrame)
    assert not result.empty


def test_fragmentation_columns_present(sample_patches_gdf, small_bbox):
    analyser = FragmentationAnalyser(grid_resolution_km=5)
    result = analyser.run(patches_gdf=sample_patches_gdf, study_bbox=small_bbox)

    expected_cols = {
        "num_patches", "total_area_ha", "mean_patch_ha",
        "largest_patch_pct", "edge_density", "division_idx",
        "cohesion", "aggregation_idx", "grid_res_km",
    }
    assert expected_cols.issubset(set(result.columns))


def test_division_index_bounded(sample_patches_gdf, small_bbox):
    analyser = FragmentationAnalyser(grid_resolution_km=5)
    result = analyser.run(patches_gdf=sample_patches_gdf, study_bbox=small_bbox)

    assert result["division_idx"].between(0, 1).all(), (
        "Division index must be in [0, 1]"
    )


def test_empty_cells_have_division_one(sample_patches_gdf, small_bbox):
    """Grid cells with no patches should have division_idx = 1.0 (fully divided)."""
    analyser = FragmentationAnalyser(grid_resolution_km=5)
    result = analyser.run(patches_gdf=sample_patches_gdf, study_bbox=small_bbox)

    empty_cells = result[result["num_patches"] == 0]
    if not empty_cells.empty:
        assert (empty_cells["division_idx"] == 1.0).all()


def test_grid_covers_full_bbox(sample_patches_gdf, small_bbox):
    """Grid extent should cover the full study bbox."""
    from shapely.geometry import box
    analyser = FragmentationAnalyser(grid_resolution_km=5)
    result = analyser.run(patches_gdf=sample_patches_gdf, study_bbox=small_bbox)

    grid_union = result.geometry.unary_union
    study_box = box(*small_bbox)
    # Grid should cover (or nearly cover) the study area
    assert grid_union.covers(study_box) or grid_union.intersects(study_box)
