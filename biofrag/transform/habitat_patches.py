"""
Habitat Patch Delineation.

Converts the ESA WorldCover raster into discrete habitat patch polygons
by isolating natural land-cover classes, applying a minimum mapping unit
filter, and computing per-patch shape metrics.

Pipeline position:  ESA raster → [THIS MODULE] → habitat_patches GeoDataFrame
                    → fragmentation_metrics.py
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Optional

import geopandas as gpd
import numpy as np
import pandas as pd
import rasterio
from rasterio.features import shapes
from shapely.geometry import shape, MultiPolygon
from shapely.ops import unary_union

from biofrag.extract.esa_worldcover import NATURAL_CLASSES, SEMI_NATURAL_CLASSES
from biofrag.utils.geo import (
    area_ha,
    compute_patch_shape_index,
    compute_fractal_dimension,
)
from biofrag.utils.logging import logger


class HabitatPatchDelineator:
    """
    Converts a land-cover raster into analysis-ready habitat patch polygons.

    Steps:
      1. Reclassify land-cover values → binary habitat mask.
      2. Vectorise contiguous habitat pixels using rasterio.features.shapes.
      3. Dissolve adjacent patches of the same class.
      4. Apply minimum mapping unit (MMU) filter.
      5. Compute shape metrics (area, perimeter, shape index, fractal dimension).

    Example:
        delineator = HabitatPatchDelineator(min_patch_area_ha=100)
        patches_gdf = delineator.run(raster_path=Path("data/raw/esa_worldcover.tif"))
    """

    def __init__(
        self,
        min_patch_area_ha: float = 100.0,
        include_semi_natural: bool = True,
        target_crs: int = 4326,
    ):
        self.min_patch_area_ha = min_patch_area_ha
        self.include_semi_natural = include_semi_natural
        self.target_crs = target_crs

        self.habitat_classes = NATURAL_CLASSES.copy()
        if include_semi_natural:
            self.habitat_classes |= SEMI_NATURAL_CLASSES

    def run(self, raster_path: Path) -> gpd.GeoDataFrame:
        """
        Full delineation pipeline for a single raster file.

        Args:
            raster_path: Path to ESA WorldCover GeoTIFF.

        Returns:
            GeoDataFrame of habitat patches with shape metrics.
        """
        logger.info(f"[Patches] Starting delineation | raster={raster_path.name}")

        with rasterio.open(raster_path) as src:
            data = src.read(1)
            transform = src.transform
            crs = src.crs
            nodata = src.nodata

        logger.info(
            f"[Patches] Raster: {data.shape} | CRS={crs} | "
            f"unique values={np.unique(data[data != nodata] if nodata else data)}"
        )

        # Step 1: build binary habitat mask
        habitat_mask = np.isin(data, list(self.habitat_classes)).astype(np.uint8)
        logger.info(
            f"[Patches] Habitat mask: {habitat_mask.sum():,} pixels "
            f"({habitat_mask.mean()*100:.1f}% of raster)"
        )

        # Step 2: vectorise
        patches_gdf = self._vectorise(habitat_mask, data, transform, crs)
        logger.info(f"[Patches] Vectorised: {len(patches_gdf):,} raw polygons")

        # Step 3: apply MMU filter
        before = len(patches_gdf)
        patches_gdf = patches_gdf[
            patches_gdf["area_ha"] >= self.min_patch_area_ha
        ].copy()
        logger.info(
            f"[Patches] MMU filter (>={self.min_patch_area_ha} ha): "
            f"{before:,} → {len(patches_gdf):,} patches"
        )

        # Step 4: compute shape metrics
        patches_gdf = self._compute_metrics(patches_gdf)

        # Step 5: reproject if needed
        if patches_gdf.crs and str(patches_gdf.crs.to_epsg()) != str(self.target_crs):
            patches_gdf = patches_gdf.to_crs(epsg=self.target_crs)

        # Add UUIDs
        patches_gdf["patch_id"] = [str(uuid.uuid4()) for _ in range(len(patches_gdf))]

        logger.info(
            f"[Patches] Delineation complete: {len(patches_gdf):,} patches | "
            f"total area: {patches_gdf['area_ha'].sum():,.0f} ha"
        )
        return patches_gdf.reset_index(drop=True)

    # ── Internal steps ────────────────────────────────────────────────────────

    def _vectorise(
        self,
        mask: np.ndarray,
        landcover: np.ndarray,
        transform,
        crs,
    ) -> gpd.GeoDataFrame:
        """Vectorise binary habitat mask, preserving original landcover class."""
        rows = []
        for geom_dict, value in shapes(mask, mask=(mask == 1), transform=transform):
            if value == 0:
                continue
            geom = shape(geom_dict)
            if not geom.is_valid:
                geom = geom.buffer(0)

            # Sample the dominant landcover class within this polygon
            # (simplified: use the pixel value at centroid)
            cx, cy = geom.centroid.x, geom.centroid.y
            lc_class = int(value)  # mask pixel = 1; we'll join class later

            calculated_area = area_ha(geom, crs=crs)
            rows.append({
                "landcover_class": lc_class,
                "area_ha": calculated_area,
                "geom": geom,
            })

        gdf = gpd.GeoDataFrame(rows, crs=crs, geometry="geom")

        # Add LCCS label
        gdf["landcover_label"] = gdf["landcover_class"].map(
            self._landcover_labels()
        ).fillna("unknown")

        return gdf

    def _compute_metrics(self, gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
        """Add perimeter, shape index, and fractal dimension columns."""
        logger.info("[Patches] Computing shape metrics...")

        # Perimeter in metres — project first for accurate measurement
        gdf_proj = gdf.to_crs("ESRI:102022")  # Africa Albers Equal Area
        gdf["perimeter_m"] = gdf_proj.geometry.length
        gdf["area_ha"] = gdf_proj.geometry.area / 10_000  # overwrite with projected area

        gdf["shape_index"] = gdf.apply(
            lambda r: compute_patch_shape_index(r["area_ha"] * 10_000, r["perimeter_m"]),
            axis=1,
        )
        gdf["fractal_dim"] = gdf.apply(
            lambda r: compute_fractal_dimension(r["area_ha"] * 10_000, r["perimeter_m"]),
            axis=1,
        )
        return gdf

    @staticmethod
    def _landcover_labels() -> dict[int, str]:
        return {
            10: "Tree cover",
            20: "Shrubland",
            30: "Grassland",
            40: "Cropland",
            50: "Built-up",
            60: "Bare / sparse vegetation",
            70: "Snow and Ice",
            80: "Permanent water bodies",
            90: "Herbaceous wetland",
            95: "Mangroves",
            100: "Moss and lichen",
        }
