"""
WDPA (World Database on Protected Areas) Extractor.

Uses the Protected Planet API v3 to fetch protected area polygons
within a bounding box. Falls back to a locally downloaded WDPA
GeoPackage if no API token is available.

API docs: https://api.protectedplanet.net/documentation
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Optional

import geopandas as gpd
import pandas as pd
from shapely.geometry import shape

from biofrag.extract.base import BaseExtractor
from biofrag.utils.logging import logger


class WDPAExtractor(BaseExtractor):
    """
    Extract protected area boundaries from the WDPA.

    Two modes:
      1. API mode  — requires WDPA_API_TOKEN (recommended for small areas)
      2. File mode — reads a locally downloaded WDPA GeoPackage or shapefile
                     (recommended for full-region analysis)

    Example (API mode):
        extractor = WDPAExtractor(api_token="your_token")
        gdf = extractor.extract(bbox=(12.0, -35.0, 40.5, -8.0))

    Example (file mode):
        extractor = WDPAExtractor(local_file=Path("data/raw/WDPA_Mar2024.gpkg"))
        gdf = extractor.extract(bbox=(12.0, -35.0, 40.5, -8.0))
    """

    name = "wdpa"

    def __init__(
        self,
        api_token: Optional[str] = None,
        local_file: Optional[Path] = None,
        page_size: int = 25,
        cache_dir: Optional[Path] = None,
    ):
        super().__init__(cache_dir=cache_dir)
        self.api_token = api_token
        self.local_file = local_file
        self.page_size = page_size
        self.base_url = "https://api.protectedplanet.net/v3"

        if not api_token and not local_file:
            logger.warning(
                "[WDPA] No API token or local file provided. "
                "Download WDPA data from https://www.protectedplanet.net/en/thematic-areas/wdpa"
            )

    def extract(
        self,
        bbox: tuple[float, float, float, float],
        iucn_categories: Optional[list[str]] = None,
        exclude_marine: bool = True,
        **kwargs: Any,
    ) -> gpd.GeoDataFrame:
        """
        Fetch protected areas intersecting the bounding box.

        Args:
            bbox: (west, south, east, north) in EPSG:4326.
            iucn_categories: Filter by IUCN category, e.g. ["Ia","Ib","II","III","IV"].
            exclude_marine: If True, exclude marine protected areas.

        Returns:
            GeoDataFrame with Polygon geometries in EPSG:4326.
        """
        if self.local_file and self.local_file.exists():
            return self._extract_from_file(bbox, iucn_categories, exclude_marine)
        elif self.api_token:
            return self._extract_from_api(bbox, iucn_categories, exclude_marine)
        else:
            raise RuntimeError(
                "WDPA extractor needs either an API token (WDPA_API_TOKEN) "
                "or a local WDPA file path."
            )

    def _extract_from_api(
        self,
        bbox: tuple[float, float, float, float],
        iucn_categories: Optional[list[str]],
        exclude_marine: bool,
    ) -> gpd.GeoDataFrame:
        west, south, east, north = bbox
        all_areas: list[dict] = []
        page = 1

        logger.info(f"[WDPA] Starting API extraction | bbox={bbox}")

        while True:
            params = {
                "token": self.api_token,
                "with_geometry": "true",
                "page": page,
                "per_page": self.page_size,
                # Spatial filter via bounding box (API uses lat/lon bbox)
                "latitude": (south + north) / 2,
                "longitude": (west + east) / 2,
            }
            if iucn_categories:
                params["iucn_categories"] = ",".join(iucn_categories)

            resp = self._get(f"{self.base_url}/protected_areas", params=params)
            data = resp.json()
            areas = data.get("protected_areas", [])

            if not areas:
                break

            all_areas.extend(areas)
            logger.debug(f"[WDPA] Page {page}: fetched {len(areas)} areas (total: {len(all_areas)})")

            # Check if more pages exist
            meta = data.get("meta", {})
            if page >= meta.get("total_pages", 1):
                break

            page += 1
            time.sleep(0.5)  # respect rate limits

        logger.info(f"[WDPA] API extraction complete — {len(all_areas)} protected areas")
        return self._api_records_to_gdf(all_areas, exclude_marine)

    def _extract_from_file(
        self,
        bbox: tuple[float, float, float, float],
        iucn_categories: Optional[list[str]],
        exclude_marine: bool,
    ) -> gpd.GeoDataFrame:
        """Load from a local WDPA GeoPackage or Shapefile and clip to bbox."""
        logger.info(f"[WDPA] Loading from local file: {self.local_file}")

        west, south, east, north = bbox

        # Read with bbox filter (much faster than reading all then clipping)
        gdf = gpd.read_file(
            self.local_file,
            bbox=(west, south, east, north),
            layer="WDPA_WDOECM_poly",  # WDPA GeoPackage layer name
        )

        logger.info(f"[WDPA] Loaded {len(gdf):,} protected areas from file")

        # Apply filters
        if exclude_marine and "MARINE" in gdf.columns:
            gdf = gdf[gdf["MARINE"] != "2"]  # 0=terrestrial, 1=coastal, 2=marine
        if iucn_categories and "IUCN_CAT" in gdf.columns:
            gdf = gdf[gdf["IUCN_CAT"].isin(iucn_categories)]

        return self._normalise_file_columns(gdf)

    def _api_records_to_gdf(
        self, records: list[dict], exclude_marine: bool
    ) -> gpd.GeoDataFrame:
        """Convert WDPA API JSON records to GeoDataFrame."""
        rows = []
        geometries = []

        for r in records:
            if exclude_marine and r.get("marine") == "2":
                continue
            geom_json = r.get("geojson", {}).get("geometry")
            if not geom_json:
                continue
            try:
                geom = shape(geom_json)
            except Exception:
                continue

            rows.append({
                "wdpa_id": r.get("id"),
                "name": r.get("name"),
                "desig_eng": r.get("designation", {}).get("name"),
                "iucn_cat": r.get("iucn_category", {}).get("name"),
                "marine": r.get("marine"),
                "status": r.get("status"),
                "status_yr": r.get("status_year"),
                "country_name": r.get("country", {}).get("name"),
                "iso3": r.get("country", {}).get("iso_3"),
                "gis_area": r.get("reported_area"),
            })
            geometries.append(geom)

        gdf = gpd.GeoDataFrame(rows, geometry=geometries, crs="EPSG:4326")
        gdf = gdf.rename(columns={"geometry": "geom"}).set_geometry("geom")
        logger.info(f"[WDPA] Built GeoDataFrame: {len(gdf):,} protected areas")
        return gdf

    def _normalise_file_columns(self, gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
        """Normalise column names from WDPA GeoPackage to match our schema."""
        col_map = {
            "WDPAID": "wdpa_id",
            "NAME": "name",
            "ORIG_NAME": "orig_name",
            "DESIG": "desig",
            "DESIG_ENG": "desig_eng",
            "DESIG_TYPE": "desig_type",
            "IUCN_CAT": "iucn_cat",
            "INT_CRIT": "int_crit",
            "MARINE": "marine",
            "REP_M_AREA": "rep_m_area",
            "GIS_M_AREA": "gis_m_area",
            "REP_AREA": "rep_area",
            "GIS_AREA": "gis_area",
            "STATUS": "status",
            "STATUS_YR": "status_yr",
            "GOV_TYPE": "gov_type",
            "OWN_TYPE": "own_type",
            "MANG_AUTH": "mang_auth",
            "ISO3": "iso3",
            "PARENT_ISO3": "parent_iso3",
            "COUNTRY_NA": "country_name",
            "geometry": "geom",
        }
        gdf = gdf.rename(columns={k: v for k, v in col_map.items() if k in gdf.columns})
        if gdf.geometry.name != "geom":
            gdf = gdf.set_geometry("geom")
        return gdf
