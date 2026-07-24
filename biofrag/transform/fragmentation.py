"""
Landscape Fragmentation Metrics.

Computes FRAGSTATS-equivalent landscape ecology metrics across a
regular analysis grid. Each grid cell gets a full suite of patch-level
and landscape-level fragmentation statistics.

Metrics computed per grid cell:
  - Number of patches (NP)
  - Total habitat area (HA)
  - Mean patch area (AREA_MN)
  - Largest Patch Index (LPI)
  - Total Edge (TE) and Edge Density (ED)
  - Landscape Division Index (DIVISION)
  - Patch Cohesion Index (COHESION)
  - Aggregation Index (AI) — approximated

Pipeline position:  habitat_patches.py → [THIS MODULE] → fragmentation_metrics GeoDataFrame
"""

from __future__ import annotations

import uuid
from typing import Optional

import geopandas as gpd
import numpy as np
import pandas as pd
from shapely.geometry import box

from biofrag.utils.geo import (
    edge_density,
    landscape_division_index,
    largest_patch_index,
)
from biofrag.utils.logging import logger


class FragmentationAnalyser:
    """
    Compute landscape fragmentation metrics on a regular grid.

    Example:
        analyser = FragmentationAnalyser(grid_resolution_km=10)
        metrics_gdf = analyser.run(
            patches_gdf=patches,
            study_bbox=(12.0, -35.0, 40.5, -8.0),
        )
    """

    def __init__(
        self,
        grid_resolution_km: float = 10.0,
        min_patches_per_cell: int = 1,
    ):
        self.grid_resolution_km = grid_resolution_km
        self.min_patches_per_cell = min_patches_per_cell
        # Convert km to degrees (approximate, valid for Africa)
        self._grid_deg = grid_resolution_km / 111.0

    def run(
        self,
        patches_gdf: gpd.GeoDataFrame,
        study_bbox: tuple[float, float, float, float],
    ) -> gpd.GeoDataFrame:
        """
        Compute fragmentation metrics across a regular grid.

        Args:
            patches_gdf: Habitat patches from HabitatPatchDelineator.
            study_bbox: (west, south, east, north) for grid extent.

        Returns:
            GeoDataFrame with one row per grid cell, full metric suite.
        """
        logger.info(
            f"[Fragmentation] Building {self.grid_resolution_km} km grid | "
            f"bbox={study_bbox}"
        )

        grid = self._build_grid(study_bbox)
        logger.info(f"[Fragmentation] Grid cells: {len(grid):,}")

        # Spatial join: patches → grid cells
        patches_proj = patches_gdf.to_crs("EPSG:4326") if patches_gdf.crs.to_epsg() != 4326 else patches_gdf
        joined = gpd.sjoin(patches_proj, grid[["cell_id", "geom"]], how="left", predicate="intersects")

        logger.info(f"[Fragmentation] Computing metrics per cell...")
        metrics_rows = []

        for cell_id, group in joined.groupby("cell_id"):
            cell_geom = grid.loc[grid["cell_id"] == cell_id, "geom"].iloc[0]
            row = self._compute_cell_metrics(group, cell_id, cell_geom)
            metrics_rows.append(row)

        # Also add empty cells (no patches)
        assigned_cells = set(joined["cell_id"].dropna().unique())
        for _, cell_row in grid.iterrows():
            if cell_row["cell_id"] not in assigned_cells:
                metrics_rows.append(self._empty_cell_metrics(cell_row["cell_id"], cell_row["geom"]))

        metrics_gdf = gpd.GeoDataFrame(metrics_rows, crs="EPSG:4326", geometry="geom")
        metrics_gdf["grid_cell_id"] = [str(uuid.uuid4()) for _ in range(len(metrics_gdf))]
        metrics_gdf["grid_res_km"] = self.grid_resolution_km

        logger.info(
            f"[Fragmentation] Complete: {len(metrics_gdf):,} cells | "
            f"mean division index: {metrics_gdf['division_idx'].mean():.3f}"
        )
        return metrics_gdf.reset_index(drop=True)

    # ── Grid construction ─────────────────────────────────────────────────────

    def _build_grid(
        self, bbox: tuple[float, float, float, float]
    ) -> gpd.GeoDataFrame:
        """Build a regular rectangular grid over the bounding box."""
        west, south, east, north = bbox
        step = self._grid_deg

        cells = []
        cell_id = 0
        y = south
        while y < north:
            x = west
            while x < east:
                geom = box(x, y, min(x + step, east), min(y + step, north))
                cells.append({"cell_id": cell_id, "geom": geom})
                cell_id += 1
                x += step
            y += step

        return gpd.GeoDataFrame(cells, crs="EPSG:4326", geometry="geom")

    # ── Per-cell metric computation ───────────────────────────────────────────

    def _compute_cell_metrics(
        self, patches: pd.DataFrame, cell_id: int, cell_geom
    ) -> dict:
        """Compute the full fragmentation metric suite for one grid cell."""
        valid = patches.dropna(subset=["area_ha"])
        if valid.empty:
            return self._empty_cell_metrics(cell_id, cell_geom)

        areas = valid["area_ha"].tolist()
        perimeters = valid["perimeter_m"].tolist() if "perimeter_m" in valid.columns else []

        total_area = sum(areas)
        total_edge = sum(perimeters)
        cell_area_ha = (cell_geom.area / (111_000 ** 2)) * 1e4  # rough ha conversion

        ed = edge_density(total_edge, total_area) if total_area > 0 else 0
        lpi = largest_patch_index(areas, total_area)
        division = landscape_division_index(areas, total_area)

        # Patch Cohesion Index (simplified: 1 - sum(perimeter/area) based)
        if total_area > 0 and total_edge > 0:
            cohesion = (
                1 - (total_edge / (total_edge * np.sqrt(total_area)))
            ) / (1 - 1 / np.sqrt(cell_area_ha)) * 100
            cohesion = float(np.clip(cohesion, 0, 100))
        else:
            cohesion = 0.0

        return {
            "cell_id": cell_id,
            "num_patches": len(areas),
            "total_area_ha": round(total_area, 2),
            "mean_patch_ha": round(total_area / len(areas), 2) if areas else 0,
            "largest_patch_pct": round(lpi, 3),
            "total_edge_m": round(total_edge, 1),
            "edge_density": round(ed, 3),
            "cohesion": round(cohesion, 3),
            "aggregation_idx": round(self._aggregation_index(areas, total_area), 3),
            "division_idx": round(division, 4),
            "geom": cell_geom,
        }

    def _empty_cell_metrics(self, cell_id: int, cell_geom) -> dict:
        return {
            "cell_id": cell_id,
            "num_patches": 0,
            "total_area_ha": 0.0,
            "mean_patch_ha": 0.0,
            "largest_patch_pct": 0.0,
            "total_edge_m": 0.0,
            "edge_density": 0.0,
            "cohesion": 0.0,
            "aggregation_idx": 0.0,
            "division_idx": 1.0,  # fully divided if no habitat
            "geom": cell_geom,
        }

    @staticmethod
    def _aggregation_index(areas: list[float], total_area: float) -> float:
        """
        Approximated Aggregation Index (0-100).
        100 = maximally aggregated (one large patch).
        0   = maximally disaggregated.
        """
        if not areas or total_area <= 0:
            return 0.0
        # Simplified: based on proportion of area in the largest patch
        max_prop = max(areas) / total_area
        return max_prop * 100
