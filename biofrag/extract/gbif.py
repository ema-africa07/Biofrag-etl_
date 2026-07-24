"""
GBIF Species Occurrence Extractor.

Uses the GBIF Occurrence Search API (v1) to fetch species observations
within a bounding box. Handles pagination automatically and returns a
clean GeoDataFrame ready for PostGIS ingestion.

API docs: https://www.gbif.org/developer/occurrence
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Optional

import geopandas as gpd
import pandas as pd
from shapely.geometry import Point

from biofrag.extract.base import BaseExtractor
from biofrag.utils.logging import logger


# Columns we care about from the GBIF response
_GBIF_COLUMNS = [
    "key",
    "species",
    "kingdom",
    "phylum",
    "class",
    "order",
    "family",
    "genus",
    "countryCode",
    "datasetKey",
    "eventDate",
    "basisOfRecord",
    "coordinateUncertaintyInMeters",
    "decimalLatitude",
    "decimalLongitude",
]

# Map GBIF field names → our DB column names
_COLUMN_MAP = {
    "key": "gbif_key",
    "class": "class_name",
    "order": "order_name",
    "countryCode": "country_code",
    "datasetKey": "dataset_key",
    "eventDate": "occurrence_date",
    "basisOfRecord": "basis_of_record",
    "coordinateUncertaintyInMeters": "coordinate_uncertainty_m",
    "decimalLatitude": "latitude",
    "decimalLongitude": "longitude",
}


class GBIFExtractor(BaseExtractor):
    """
    Extract species occurrence records from GBIF within a bounding box.

    Example:
        extractor = GBIFExtractor(page_size=300)
        gdf = extractor.extract(
            bbox=(-35.0, 12.0, -8.0, 40.5),   # (south, west, north, east) — GBIF convention
            taxon_key=1,                        # Kingdom Animalia
            has_coordinate=True,
            has_geospatial_issue=False,
        )
    """

    name = "gbif"

    BASE_URL = "https://api.gbif.org/v1/occurrence/search"

    def __init__(
        self,
        page_size: int = 300,
        max_records: int = 100_000,
        cache_dir: Optional[Path] = None,
    ):
        super().__init__(cache_dir=cache_dir)
        self.page_size = min(page_size, 300)  # GBIF hard limit
        self.max_records = max_records

    def extract(
        self,
        bbox: tuple[float, float, float, float],  # (west, south, east, north)
        taxon_key: Optional[int] = None,
        kingdom: Optional[str] = None,
        has_coordinate: bool = True,
        has_geospatial_issue: bool = False,
        basis_of_record: Optional[list[str]] = None,
        year_range: Optional[tuple[int, int]] = None,
        **kwargs: Any,
    ) -> gpd.GeoDataFrame:
        """
        Fetch occurrence records from GBIF.

        Args:
            bbox: (west, south, east, north) in EPSG:4326.
            taxon_key: GBIF taxon key to filter (e.g. 1 = Animalia).
            kingdom: Kingdom name filter (e.g. "Animalia").
            has_coordinate: Only return records with coordinates.
            has_geospatial_issue: Whether to include records with geospatial issues.
            basis_of_record: e.g. ["HUMAN_OBSERVATION", "MACHINE_OBSERVATION"].
            year_range: (start_year, end_year) tuple.

        Returns:
            GeoDataFrame with Point geometries in EPSG:4326.
        """
        west, south, east, north = bbox

        params: dict[str, Any] = {
            "decimalLatitude": f"{south},{north}",
            "decimalLongitude": f"{west},{east}",
            "hasCoordinate": str(has_coordinate).lower(),
            "hasGeospatialIssue": str(has_geospatial_issue).lower(),
            "limit": self.page_size,
            "offset": 0,
        }

        if taxon_key:
            params["taxonKey"] = taxon_key
        if kingdom:
            params["kingdom"] = kingdom
        if basis_of_record:
            params["basisOfRecord"] = basis_of_record
        if year_range:
            params["year"] = f"{year_range[0]},{year_range[1]}"

        logger.info(
            f"[GBIF] Starting extraction | bbox={bbox} | "
            f"taxon_key={taxon_key} | max_records={self.max_records}"
        )

        records: list[dict] = []
        total_available = None

        while True:
            resp = self._get(self.BASE_URL, params=params)
            data = resp.json()

            if total_available is None:
                total_available = data.get("count", 0)
                logger.info(f"[GBIF] Total available records: {total_available:,}")

            batch = data.get("results", [])
            if not batch:
                break

            records.extend(batch)
            logger.debug(f"[GBIF] Fetched {len(records):,} / {min(total_available, self.max_records):,}")

            if data.get("endOfRecords", True):
                break
            if len(records) >= self.max_records:
                logger.warning(
                    f"[GBIF] Reached max_records cap ({self.max_records:,}). "
                    "Increase max_records to fetch more."
                )
                break

            params["offset"] += self.page_size
            time.sleep(0.1)  # polite delay

        logger.info(f"[GBIF] Extraction complete — {len(records):,} records fetched")
        return self._to_geodataframe(records)

    def _to_geodataframe(self, records: list[dict]) -> gpd.GeoDataFrame:
        """Convert raw GBIF JSON records to a clean GeoDataFrame."""
        if not records:
            logger.warning("[GBIF] No records returned — returning empty GeoDataFrame")
            return gpd.GeoDataFrame(columns=list(_COLUMN_MAP.values()) + ["geom"])

        rows = []
        for r in records:
            lat = r.get("decimalLatitude")
            lon = r.get("decimalLongitude")
            if lat is None or lon is None:
                continue
            row = {col: r.get(col) for col in _GBIF_COLUMNS}
            row["_lat"] = lat
            row["_lon"] = lon
            row["_raw"] = r  # keep full record for JSONB storage
            rows.append(row)

        df = pd.DataFrame(rows)
        df = df.rename(columns=_COLUMN_MAP)

        # Build geometries
        geometry = [Point(row["_lon"], row["_lat"]) for _, row in df.iterrows()]
        df = df.drop(columns=["_lat", "_lon"])

        gdf = gpd.GeoDataFrame(df, geometry=geometry, crs="EPSG:4326")
        gdf = gdf.rename(columns={"geometry": "geom"}).set_geometry("geom")

        # Tidy types
        if "occurrence_date" in gdf.columns:
            gdf["occurrence_date"] = pd.to_datetime(
                gdf["occurrence_date"], errors="coerce"
            ).dt.date

        logger.info(
            f"[GBIF] Built GeoDataFrame: {len(gdf):,} rows | "
            f"species count: {gdf['species'].nunique():,}"
        )
        return gdf
