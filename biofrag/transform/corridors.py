"""
Wildlife Corridor Modelling.

Identifies viable wildlife movement corridors between habitat patches
using a cost-distance approach. The cost surface is built from:
  - Road network (primary barrier — inverse of HIGHWAY_RESISTANCE)
  - Land cover (permeability by class)
  - Protected area status (lower cost inside PAs)

This is a vector-based least-cost-path approximation. For a full
raster-based Circuitscape analysis, use the exported cost surface
GeoTIFF with the Circuitscape software directly.

Pipeline position:  habitat_patches + roads + wdpa → [THIS MODULE] → corridors GeoDataFrame
"""

from __future__ import annotations

import uuid
from typing import Optional

import geopandas as gpd
import numpy as np
import pandas as pd
from shapely.geometry import LineString
from shapely.ops import nearest_points

from biofrag.utils.logging import logger


# Permeability score by land cover class (0 = impermeable, 1 = fully permeable)
LC_PERMEABILITY = {
    10: 1.0,    # Tree cover — highest permeability
    20: 0.85,   # Shrubland
    30: 0.7,    # Grassland
    40: 0.4,    # Cropland
    50: 0.05,   # Built-up — near-impermeable
    60: 0.5,    # Bare
    80: 0.3,    # Water — barrier but crossable
    90: 0.75,   # Wetland
    95: 0.9,    # Mangroves
}

# Cost multiplier for road proximity (within distance in metres)
ROAD_COST_BUFFER_M = {
    500: 3.0,     # Within 500m of motorway/trunk: 3× cost
    200: 2.0,     # Within 200m of primary: 2× cost
    100: 1.5,     # Within 100m of secondary: 1.5× cost
}


class CorridorModeller:
    """
    Identify least-cost wildlife corridors between habitat patches.

    This implementation uses a simplified Euclidean-cost model suitable
    for regional-scale screening. For site-scale analysis, export the
    cost surface and use Circuitscape or Linkage Mapper.

    Example:
        modeller = CorridorModeller(max_distance_km=50, top_n_pairs=20)
        corridors_gdf = modeller.run(
            patches_gdf=patches,
            roads_gdf=roads,
            wdpa_gdf=protected_areas,
        )
    """

    def __init__(
        self,
        max_distance_km: float = 50.0,
        top_n_pairs: int = 50,
        min_patch_area_ha: float = 500.0,
    ):
        self.max_distance_km = max_distance_km
        self.top_n_pairs = top_n_pairs
        self.min_patch_area_ha = min_patch_area_ha
        self._max_dist_deg = max_distance_km / 111.0

    def run(
        self,
        patches_gdf: gpd.GeoDataFrame,
        roads_gdf: Optional[gpd.GeoDataFrame] = None,
        wdpa_gdf: Optional[gpd.GeoDataFrame] = None,
    ) -> gpd.GeoDataFrame:
        """
        Model corridors between the most significant habitat patches.

        Args:
            patches_gdf: Habitat patches (from HabitatPatchDelineator).
            roads_gdf:   Road network (from OSMExtractor) — used for cost.
            wdpa_gdf:    Protected areas (from WDPAExtractor) — reduces cost.

        Returns:
            GeoDataFrame of corridor LineStrings with viability scores.
        """
        logger.info(
            f"[Corridors] Starting corridor modelling | "
            f"max_distance={self.max_distance_km} km | top_n={self.top_n_pairs}"
        )

        # Filter to significant source patches
        source_patches = patches_gdf[
            patches_gdf["area_ha"] >= self.min_patch_area_ha
        ].copy()
        source_patches = source_patches.to_crs("EPSG:4326")
        logger.info(f"[Corridors] Source patches (>={self.min_patch_area_ha} ha): {len(source_patches):,}")

        if len(source_patches) < 2:
            logger.warning("[Corridors] Need at least 2 patches — returning empty GeoDataFrame")
            return gpd.GeoDataFrame()

        # Build candidate patch pairs within max distance
        pairs = self._candidate_pairs(source_patches)
        logger.info(f"[Corridors] Candidate pairs within {self.max_distance_km} km: {len(pairs):,}")

        if not pairs:
            logger.warning("[Corridors] No pairs within distance threshold.")
            return gpd.GeoDataFrame()

        # Build road resistance buffer (optional)
        road_buffer = None
        if roads_gdf is not None and not roads_gdf.empty:
            road_buffer = self._build_road_buffer(roads_gdf)

        # Build PA benefit layer (optional)
        pa_union = None
        if wdpa_gdf is not None and not wdpa_gdf.empty:
            pa_union = wdpa_gdf.geometry.unary_union

        # Score and model each pair
        corridor_rows = []
        for from_id, to_id, straight_dist_m in pairs:
            from_patch = source_patches[source_patches["patch_id"] == from_id].iloc[0]
            to_patch = source_patches[source_patches["patch_id"] == to_id].iloc[0]

            corridor_geom, cost, width_m = self._model_corridor(
                from_patch, to_patch, road_buffer, pa_union, straight_dist_m
            )

            viability = self._classify_viability(cost, straight_dist_m)

            corridor_rows.append({
                "corridor_id": str(uuid.uuid4()),
                "from_patch_id": from_id,
                "to_patch_id": to_id,
                "cost_distance": round(cost, 2),
                "width_m": round(width_m, 1),
                "length_m": round(straight_dist_m, 1),
                "viability": viability,
                "geom": corridor_geom,
            })

        corridors_gdf = gpd.GeoDataFrame(
            corridor_rows, crs="EPSG:4326", geometry="geom"
        )

        # Keep only top N by viability then cost
        viability_order = {"high": 0, "medium": 1, "low": 2, "critical": 3}
        corridors_gdf["_v_order"] = corridors_gdf["viability"].map(viability_order)
        corridors_gdf = (
            corridors_gdf.sort_values(["_v_order", "cost_distance"])
            .head(self.top_n_pairs)
            .drop(columns=["_v_order"])
            .reset_index(drop=True)
        )

        logger.info(
            f"[Corridors] Modelled {len(corridors_gdf):,} corridors | "
            f"viability: {corridors_gdf['viability'].value_counts().to_dict()}"
        )
        return corridors_gdf

    # ── Pair identification ───────────────────────────────────────────────────

    def _candidate_pairs(
        self, patches: gpd.GeoDataFrame
    ) -> list[tuple[str, str, float]]:
        """Find all patch pairs within max_distance, sorted by distance."""
        pairs = []
        patch_list = patches.reset_index(drop=True)
        n = len(patch_list)

        for i in range(n):
            for j in range(i + 1, n):
                p1 = patch_list.iloc[i]
                p2 = patch_list.iloc[j]
                # Approximate distance using centroid (degrees → metres)
                dist_deg = p1.geom.centroid.distance(p2.geom.centroid)
                dist_m = dist_deg * 111_000

                if dist_m <= self.max_distance_km * 1000:
                    pairs.append((
                        p1["patch_id"],
                        p2["patch_id"],
                        dist_m,
                    ))

        return sorted(pairs, key=lambda x: x[2])

    # ── Corridor geometry & cost ──────────────────────────────────────────────

    def _model_corridor(
        self,
        from_patch,
        to_patch,
        road_buffer,
        pa_union,
        straight_dist_m: float,
    ) -> tuple:
        """
        Compute a straight-line corridor with cost scoring.

        Returns: (LineString geometry, cost_distance, estimated_width_m)
        """
        # Use nearest boundary points for corridor endpoints
        p1_near, p2_near = nearest_points(from_patch.geom, to_patch.geom)
        corridor_line = LineString([p1_near, p2_near])

        # Base cost = Euclidean distance
        cost = straight_dist_m

        # Road penalty: if corridor crosses road buffer, increase cost
        if road_buffer is not None:
            intersection = corridor_line.intersection(road_buffer)
            road_crossing_m = intersection.length * 111_000  # rough degrees → m
            cost += road_crossing_m * 2.5  # roads increase cost by 2.5×

        # PA benefit: reduce cost for distance inside protected areas
        if pa_union is not None:
            inside_pa = corridor_line.intersection(pa_union)
            pa_length_m = inside_pa.length * 111_000
            cost -= pa_length_m * 0.4  # 40% cost reduction inside PAs
            cost = max(cost, straight_dist_m * 0.1)  # floor at 10% of straight dist

        # Estimate functional corridor width (simplified: based on patch sizes)
        min_area = min(from_patch["area_ha"], to_patch["area_ha"])
        width_m = min(np.sqrt(min_area * 10_000), 5_000)  # cap at 5 km

        return corridor_line, cost, width_m

    @staticmethod
    def _build_road_buffer(roads_gdf: gpd.GeoDataFrame) -> object:
        """Build a merged buffer around major roads."""
        # Only buffer motorways and primary roads (most impermeable)
        major = roads_gdf[roads_gdf["highway"].isin(
            ["motorway", "trunk", "primary", "motorway_link", "trunk_link"]
        )]
        if major.empty:
            major = roads_gdf

        # Buffer ~200m in degrees (~0.002°)
        buffered = major.geometry.buffer(0.002)
        return buffered.unary_union

    @staticmethod
    def _classify_viability(cost: float, straight_dist_m: float) -> str:
        """Classify corridor viability based on cost ratio."""
        if straight_dist_m <= 0:
            return "critical"
        ratio = cost / straight_dist_m
        if ratio < 1.5:
            return "high"
        elif ratio < 3.0:
            return "medium"
        elif ratio < 6.0:
            return "low"
        else:
            return "critical"
