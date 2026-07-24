"""
ESA WorldCover Land Cover Extractor.

Downloads ESA WorldCover 10m tiles (2020 or 2021) for a given bounding
box from the ESA S3 bucket. These are the primary habitat classification
layers used to delineate habitat patches and compute fragmentation metrics.

Map classes (LCCS):
  10 = Tree cover
  20 = Shrubland
  30 = Grassland
  40 = Cropland
  50 = Built-up
  60 = Bare / sparse vegetation
  70 = Snow and Ice
  80 = Permanent water bodies
  90 = Herbaceous wetland
  95 = Mangroves
  100 = Moss and lichen

ESA WorldCover: https://esa-worldcover.org
AWS bucket: s3://esa-worldcover/v200/2021/map/
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urljoin

import numpy as np
import rasterio
from rasterio.merge import merge
from rasterio.mask import mask as rio_mask
from rasterio.warp import calculate_default_transform, reproject, Resampling
from shapely.geometry import box, mapping

from biofrag.extract.base import BaseExtractor
from biofrag.utils.logging import logger


# Habitat classes we consider "natural" for fragmentation analysis
NATURAL_CLASSES = {10, 20, 30, 90, 95}   # Tree, Shrub, Grass, Wetland, Mangrove
SEMI_NATURAL_CLASSES = {40}               # Cropland (partial permeability)
BARRIER_CLASSES = {50}                    # Built-up (hard barrier)

# Base URL pattern for ESA WorldCover tiles
# Tiles are named by the SW corner: ESA_WorldCover_10m_2021_v200_N00E012_Map.tif
ESA_BASE_URL = "https://esa-worldcover.s3.eu-central-1.amazonaws.com/v200/2021/map/"


class ESAWorldCoverExtractor(BaseExtractor):
    """
    Download and mosaic ESA WorldCover tiles for a bounding box.

    Example:
        extractor = ESAWorldCoverExtractor(year=2021)
        raster_path = extractor.extract(bbox=(12.0, -35.0, 40.5, -8.0))
        # Returns Path to a merged, clipped GeoTIFF
    """

    name = "esa_worldcover"

    def __init__(
        self,
        year: int = 2021,
        cache_dir: Optional[Path] = None,
    ):
        super().__init__(cache_dir=cache_dir)
        self.year = year

    def extract(
        self,
        bbox: tuple[float, float, float, float],
        target_crs: Optional[int] = None,
        use_cache: bool = True,
        **kwargs: Any,
    ) -> Path:
        """
        Download ESA WorldCover tiles covering the bounding box and merge them.

        Args:
            bbox: (west, south, east, north) in EPSG:4326.
            target_crs: Reproject output to this EPSG (e.g. 102022 for Africa Albers).
            use_cache: Skip download if tile already cached.

        Returns:
            Path to the merged GeoTIFF covering the full bbox.
        """
        west, south, east, north = bbox
        output_name = (
            f"esa_worldcover_{self.year}_{south:.1f}_{west:.1f}_{north:.1f}_{east:.1f}.tif"
        )
        output_path = self.cache_path(output_name)

        if use_cache and output_path.exists():
            logger.info(f"[ESA] Using cached mosaic: {output_path}")
            return output_path

        # Identify required tile names
        tile_names = self._tiles_for_bbox(bbox)
        logger.info(f"[ESA] Requires {len(tile_names)} tiles: {tile_names}")

        tile_paths: list[Path] = []
        for tile in tile_names:
            tile_path = self._download_tile(tile, use_cache=use_cache)
            if tile_path is not None:
                tile_paths.append(tile_path)

        if not tile_paths:
            raise RuntimeError("[ESA] No tiles downloaded — check bbox and network access.")

        # Merge tiles
        logger.info(f"[ESA] Merging {len(tile_paths)} tiles...")
        merged_path = self._merge_and_clip(tile_paths, bbox, output_path, target_crs)
        logger.info(f"[ESA] Mosaic saved: {merged_path}")
        return merged_path

    # ── Tile identification ────────────────────────────────────────────────────

    def _tiles_for_bbox(
        self, bbox: tuple[float, float, float, float]
    ) -> list[str]:
        """
        ESA WorldCover uses 3°×3° tiles named by SW corner.
        e.g. N00E012, S03E030 — snapped to multiples of 3.
        """
        west, south, east, north = bbox
        tiles = []

        lat = self._snap(south, 3)
        while lat < north:
            lon = self._snap(west, 3)
            while lon < east:
                lat_str = f"N{abs(lat):02d}" if lat >= 0 else f"S{abs(lat):02d}"
                lon_str = f"E{abs(lon):03d}" if lon >= 0 else f"W{abs(lon):03d}"
                tiles.append(f"{lat_str}{lon_str}")
                lon += 3
            lat += 3

        return tiles

    @staticmethod
    def _snap(value: float, step: int) -> int:
        """Snap value down to nearest multiple of step."""
        import math
        return math.floor(value / step) * step

    # ── Download ──────────────────────────────────────────────────────────────

    def _download_tile(self, tile_name: str, use_cache: bool) -> Optional[Path]:
        """Download a single ESA WorldCover tile."""
        filename = f"ESA_WorldCover_10m_{self.year}_v200_{tile_name}_Map.tif"
        tile_path = self.cache_path(filename)

        if use_cache and tile_path.exists():
            logger.debug(f"[ESA] Tile cached: {filename}")
            return tile_path

        url = ESA_BASE_URL + filename
        logger.info(f"[ESA] Downloading tile: {filename}")

        try:
            resp = self._get(url, stream=True)
            with open(tile_path, "wb") as f:
                for chunk in resp.iter_content(chunk_size=8192):
                    f.write(chunk)
            logger.info(f"[ESA] Downloaded: {filename} ({tile_path.stat().st_size / 1e6:.1f} MB)")
            return tile_path
        except Exception as exc:
            logger.warning(f"[ESA] Could not download {filename}: {exc}")
            return None

    # ── Merge & clip ──────────────────────────────────────────────────────────

    def _merge_and_clip(
        self,
        tile_paths: list[Path],
        bbox: tuple[float, float, float, float],
        output_path: Path,
        target_crs: Optional[int],
    ) -> Path:
        """Merge tiles and clip to bbox, optionally reprojecting."""
        west, south, east, north = bbox
        clip_geom = [mapping(box(west, south, east, north))]

        datasets = [rasterio.open(p) for p in tile_paths]
        try:
            mosaic, transform = merge(datasets)
            meta = datasets[0].meta.copy()
        finally:
            for ds in datasets:
                ds.close()

        meta.update({
            "driver": "GTiff",
            "height": mosaic.shape[1],
            "width": mosaic.shape[2],
            "transform": transform,
            "compress": "lzw",
            "tiled": True,
            "blockxsize": 512,
            "blockysize": 512,
        })

        # Write merged raster temporarily
        tmp_path = output_path.with_suffix(".tmp.tif")
        with rasterio.open(tmp_path, "w", **meta) as dst:
            dst.write(mosaic)

        # Clip to exact bbox
        with rasterio.open(tmp_path) as src:
            clipped, clip_transform = rio_mask(src, clip_geom, crop=True)
            clip_meta = src.meta.copy()
            clip_meta.update({
                "height": clipped.shape[1],
                "width": clipped.shape[2],
                "transform": clip_transform,
            })

        if target_crs:
            output_path = self._reproject_raster(
                clipped, clip_meta, output_path, target_crs
            )
        else:
            with rasterio.open(output_path, "w", **clip_meta) as dst:
                dst.write(clipped)

        tmp_path.unlink(missing_ok=True)
        return output_path

    def _reproject_raster(
        self,
        data: np.ndarray,
        src_meta: dict,
        output_path: Path,
        target_epsg: int,
    ) -> Path:
        """Reproject a raster array to a target CRS."""
        src_crs = src_meta["crs"]
        dst_crs = rasterio.crs.CRS.from_epsg(target_epsg)

        transform, width, height = calculate_default_transform(
            src_crs, dst_crs,
            src_meta["width"], src_meta["height"],
            *rasterio.transform.array_bounds(
                src_meta["height"], src_meta["width"], src_meta["transform"]
            ),
        )

        dst_meta = src_meta.copy()
        dst_meta.update({
            "crs": dst_crs,
            "transform": transform,
            "width": width,
            "height": height,
        })

        reprojected_path = output_path.with_stem(output_path.stem + f"_epsg{target_epsg}")
        with rasterio.open(reprojected_path, "w", **dst_meta) as dst:
            for i in range(1, data.shape[0] + 1):
                reproject(
                    source=data[i - 1],
                    destination=rasterio.band(dst, i),
                    src_transform=src_meta["transform"],
                    src_crs=src_crs,
                    dst_transform=transform,
                    dst_crs=dst_crs,
                    resampling=Resampling.nearest,  # categorical data — nearest neighbour
                )

        logger.info(f"[ESA] Reprojected to EPSG:{target_epsg}: {reprojected_path}")
        return reprojected_path
