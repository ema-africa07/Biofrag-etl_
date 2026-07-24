"""
OpenStreetMap Road Network Extractor.

Uses the Overpass API to fetch road and infrastructure features
within a bounding box. Roads are a key driver of habitat fragmentation
and are used in the corridor cost-surface model.

Overpass API: https://overpass-api.de
Turbo UI for query testing: https://overpass-turbo.eu
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Optional

import geopandas as gpd
import pandas as pd
from shapely.geometry import LineString, MultiLineString, mapping

from biofrag.extract.base import BaseExtractor
from biofrag.utils.logging import logger


# OSM highway values → fragmentation resistance score
# Higher = more impermeable barrier for wildlife
HIGHWAY_RESISTANCE = {
    "motorway": 1.0,
    "motorway_link": 0.9,
    "trunk": 0.85,
    "trunk_link": 0.8,
    "primary": 0.7,
    "primary_link": 0.65,
    "secondary": 0.5,
    "secondary_link": 0.45,
    "tertiary": 0.3,
    "tertiary_link": 0.25,
    "unclassified": 0.15,
    "residential": 0.1,
    "track": 0.05,
    "path": 0.02,
}

# Overpass query template
_OVERPASS_QUERY = """
[out:json][timeout:120];
(
  way["highway"~"^(motorway|trunk|primary|secondary|tertiary|unclassified|residential|track)$"]
  ({south},{west},{north},{east});
);
out body geom;
"""

OVERPASS_ENDPOINTS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.openstreetmap.ru/api/interpreter",
]


class OSMExtractor(BaseExtractor):
    """
    Extract road network from OpenStreetMap using the Overpass API.

    Example:
        extractor = OSMExtractor()
        gdf = extractor.extract(bbox=(12.0, -35.0, 40.5, -8.0))
    """

    name = "osm"

    def __init__(
        self,
        highway_types: Optional[list[str]] = None,
        cache_dir: Optional[Path] = None,
    ):
        super().__init__(cache_dir=cache_dir)
        self.highway_types = highway_types or list(HIGHWAY_RESISTANCE.keys())

    def extract(
        self,
        bbox: tuple[float, float, float, float],
        use_cache: bool = True,
        **kwargs: Any,
    ) -> gpd.GeoDataFrame:
        """
        Fetch road network within bounding box from Overpass API.

        Args:
            bbox: (west, south, east, north) in EPSG:4326.
            use_cache: If True, skip API call if cached file exists.

        Returns:
            GeoDataFrame with LineString geometries in EPSG:4326.
        """
        west, south, east, north = bbox
        cache_file = self.cache_path(
            f"osm_roads_{south:.2f}_{west:.2f}_{north:.2f}_{east:.2f}.geojson"
        )

        if use_cache and cache_file.exists():
            logger.info(f"[OSM] Loading from cache: {cache_file}")
            return gpd.read_file(cache_file)

        query = _OVERPASS_QUERY.format(
            south=south, west=west, north=north, east=east
        )

        logger.info(f"[OSM] Querying Overpass API | bbox={bbox}")
        data = self._query_overpass(query)
        gdf = self._parse_overpass_response(data)

        # Cache the result
        if not gdf.empty:
            gdf.to_file(cache_file, driver="GeoJSON")
            logger.info(f"[OSM] Cached {len(gdf):,} road segments to {cache_file}")

        return gdf

    def _query_overpass(self, query: str) -> dict:
        """Try each Overpass mirror in turn."""
        last_error = None
        for endpoint in OVERPASS_ENDPOINTS:
            try:
                logger.debug(f"[OSM] Trying Overpass endpoint: {endpoint}")
                resp = self._post(endpoint, data={"data": query})
                return resp.json()
            except Exception as exc:
                logger.warning(f"[OSM] Endpoint {endpoint} failed: {exc}")
                last_error = exc
                time.sleep(2)

        raise RuntimeError(f"All Overpass endpoints failed. Last error: {last_error}")

    def _parse_overpass_response(self, data: dict) -> gpd.GeoDataFrame:
        """Convert Overpass JSON elements to a GeoDataFrame."""
        elements = data.get("elements", [])
        logger.info(f"[OSM] Parsing {len(elements):,} OSM way elements")

        rows = []
        geometries = []

        for el in elements:
            if el.get("type") != "way":
                continue

            nodes = el.get("geometry", [])
            if len(nodes) < 2:
                continue

            coords = [(n["lon"], n["lat"]) for n in nodes]
            try:
                line = LineString(coords)
            except Exception:
                continue

            tags = el.get("tags", {})
            highway = tags.get("highway", "unclassified")

            rows.append({
                "osm_id": el["id"],
                "highway": highway,
                "name": tags.get("name"),
                "surface": tags.get("surface"),
                "lanes": self._safe_int(tags.get("lanes")),
                "maxspeed": tags.get("maxspeed"),
                "resistance": HIGHWAY_RESISTANCE.get(highway, 0.1),
            })
            geometries.append(line)

        if not rows:
            logger.warning("[OSM] No road geometries parsed — returning empty GeoDataFrame")
            return gpd.GeoDataFrame()

        gdf = gpd.GeoDataFrame(rows, geometry=geometries, crs="EPSG:4326")
        gdf = gdf.rename(columns={"geometry": "geom"}).set_geometry("geom")

        logger.info(
            f"[OSM] Built GeoDataFrame: {len(gdf):,} road segments | "
            f"highway types: {gdf['highway'].value_counts().to_dict()}"
        )
        return gdf

    @staticmethod
    def _safe_int(value: Any) -> Optional[int]:
        try:
            return int(value)
        except (TypeError, ValueError):
            return None
