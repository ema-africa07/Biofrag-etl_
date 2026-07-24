"""Shared geospatial utility functions."""

from __future__ import annotations

import math
from typing import Optional

import geopandas as gpd
import numpy as np
from pyproj import Transformer
from shapely.geometry import box, mapping
from shapely.ops import transform as shapely_transform


def bbox_to_polygon(west: float, south: float, east: float, north: float):
    """Return a Shapely box polygon from WSEN bounds."""
    return box(west, south, east, north)


def reproject_gdf(
    gdf: gpd.GeoDataFrame,
    target_crs: int | str,
    source_crs: Optional[int | str] = None,
) -> gpd.GeoDataFrame:
    """Reproject a GeoDataFrame to target_crs, inferring source if not given."""
    if source_crs is not None and gdf.crs is None:
        gdf = gdf.set_crs(source_crs)
    return gdf.to_crs(target_crs)


def clip_to_bbox(
    gdf: gpd.GeoDataFrame,
    west: float,
    south: float,
    east: float,
    north: float,
) -> gpd.GeoDataFrame:
    """Clip a GeoDataFrame to a bounding box (in the GDF's current CRS)."""
    mask = bbox_to_polygon(west, south, east, north)
    return gpd.clip(gdf, mask)


def compute_patch_shape_index(area_m2: float, perimeter_m: float) -> float:
    """
    Shape index: ratio of patch perimeter to perimeter of a circle with same area.
    SI = 1 for a perfect circle; increases with shape complexity.
    """
    if area_m2 <= 0:
        return float("nan")
    return perimeter_m / (2 * math.sqrt(math.pi * area_m2))


def compute_fractal_dimension(area_m2: float, perimeter_m: float) -> float:
    """
    Fractal dimension approximation:
    FD = 2 * log(perimeter) / log(area)
    FD = 1 for simple shapes, approaches 2 for complex/convoluted shapes.
    """
    if area_m2 <= 0 or perimeter_m <= 0:
        return float("nan")
    return (2 * math.log(perimeter_m)) / math.log(area_m2)


def area_ha(geom, crs: int | str = 4326) -> float:
    """
    Return the area of a Shapely geometry in hectares.
    Reprojects to an equal-area CRS if the input is geographic (4326).
    """
    if str(crs) in ("4326", "EPSG:4326"):
        # Project to Africa Albers Equal Area for accurate area
        transformer = Transformer.from_crs("EPSG:4326", "ESRI:102022", always_xy=True)
        geom = shapely_transform(transformer.transform, geom)
    return geom.area / 10_000  # m² → ha


def largest_patch_index(patch_areas_ha: list[float], total_area_ha: float) -> float:
    """
    Largest Patch Index (LPI): percentage of landscape occupied by the largest patch.
    LPI = 0 → very fragmented; LPI → 100 → dominated by a single patch.
    """
    if not patch_areas_ha or total_area_ha <= 0:
        return 0.0
    return (max(patch_areas_ha) / total_area_ha) * 100


def edge_density(total_edge_m: float, total_area_ha: float) -> float:
    """
    Edge density: total edge length per unit area (m/ha).
    Higher values indicate more fragmented landscapes.
    """
    if total_area_ha <= 0:
        return 0.0
    return total_edge_m / total_area_ha


def landscape_division_index(patch_areas_ha: list[float], total_area_ha: float) -> float:
    """
    Division index (DIVISION): probability that two randomly chosen points
    are NOT in the same patch. Ranges 0 (undivided) → 1 (fully divided).
    """
    if not patch_areas_ha or total_area_ha <= 0:
        return 0.0
    return 1 - sum((a / total_area_ha) ** 2 for a in patch_areas_ha)


def degrees_to_metres(degrees: float, latitude: float = 0.0) -> float:
    """Approximate conversion of degrees to metres at a given latitude."""
    lat_m = degrees * 111_320
    lon_m = degrees * 111_320 * math.cos(math.radians(latitude))
    return (lat_m + lon_m) / 2


__all__ = [
    "bbox_to_polygon",
    "reproject_gdf",
    "clip_to_bbox",
    "compute_patch_shape_index",
    "compute_fractal_dimension",
    "area_ha",
    "largest_patch_index",
    "edge_density",
    "landscape_division_index",
    "degrees_to_metres",
]
