"""
PostGIS Loader.

Loads GeoDataFrames into the Bio-FRAG-ETL PostGIS schema using
SQLAlchemy + GeoAlchemy2. Handles upserts, schema routing, and
pipeline run logging.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal, Optional

import geopandas as gpd
import pandas as pd
from geoalchemy2 import Geometry, WKTElement
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

from biofrag.utils.logging import logger


SchemaName = Literal["raw", "processed", "outputs", "metadata"]


class PostGISLoader:
    """
    Load GeoDataFrames and DataFrames into the Bio-FRAG-ETL PostGIS database.

    Example:
        loader = PostGISLoader(db_url="postgresql+psycopg2://user:pw@localhost/biofrag")
        loader.load_geodataframe(patches_gdf, table="habitat_patches", schema="processed")
    """

    def __init__(self, db_url: str):
        self.db_url = db_url
        self._engine: Optional[Engine] = None

    @property
    def engine(self) -> Engine:
        if self._engine is None:
            self._engine = create_engine(
                self.db_url,
                pool_size=5,
                max_overflow=10,
                pool_pre_ping=True,
            )
        return self._engine

    def test_connection(self) -> bool:
        """Verify database connectivity."""
        try:
            with self.engine.connect() as conn:
                conn.execute(text("SELECT PostGIS_Version()"))
            logger.info("[PostGIS] Connection OK")
            return True
        except Exception as exc:
            logger.error(f"[PostGIS] Connection failed: {exc}")
            return False

    # ── GeoDataFrame loading ──────────────────────────────────────────────────

    def load_geodataframe(
        self,
        gdf: gpd.GeoDataFrame,
        table: str,
        schema: SchemaName = "processed",
        if_exists: Literal["replace", "append", "fail"] = "append",
        geometry_col: str = "geom",
        epsg: int = 4326,
        chunksize: int = 1000,
    ) -> int:
        """
        Write a GeoDataFrame to PostGIS.

        Args:
            gdf:          GeoDataFrame to load.
            table:        Target table name.
            schema:       Target schema (raw / processed / outputs).
            if_exists:    'append' (default) | 'replace' | 'fail'.
            geometry_col: Name of the geometry column.
            epsg:         Target EPSG for stored geometries.
            chunksize:    Rows per batch insert.

        Returns:
            Number of rows written.
        """
        if gdf.empty:
            logger.warning(f"[PostGIS] Empty GeoDataFrame — skipping {schema}.{table}")
            return 0

        logger.info(
            f"[PostGIS] Loading {len(gdf):,} rows → {schema}.{table} "
            f"(if_exists={if_exists})"
        )

        # Normalise geometry column name
        if gdf.geometry.name != geometry_col:
            gdf = gdf.rename_geometry(geometry_col)

        # Ensure correct CRS
        if gdf.crs and gdf.crs.to_epsg() != epsg:
            gdf = gdf.to_crs(epsg=epsg)

        # Drop any non-serialisable columns (e.g. raw dicts stored as JSONB)
        gdf = self._prepare_dataframe(gdf)

        try:
            gdf.to_postgis(
                name=table,
                con=self.engine,
                schema=schema,
                if_exists=if_exists,
                index=False,
                chunksize=chunksize,
                dtype={geometry_col: Geometry("GEOMETRY", srid=epsg)},
            )
            logger.info(f"[PostGIS] ✓ Loaded {len(gdf):,} rows into {schema}.{table}")
            return len(gdf)
        except Exception as exc:
            logger.error(f"[PostGIS] Load failed for {schema}.{table}: {exc}")
            raise

    def load_dataframe(
        self,
        df: pd.DataFrame,
        table: str,
        schema: SchemaName = "metadata",
        if_exists: Literal["replace", "append", "fail"] = "append",
        chunksize: int = 5000,
    ) -> int:
        """Write a plain DataFrame (no geometry) to PostGIS."""
        if df.empty:
            logger.warning(f"[PostGIS] Empty DataFrame — skipping {schema}.{table}")
            return 0

        df.to_sql(
            name=table,
            con=self.engine,
            schema=schema,
            if_exists=if_exists,
            index=False,
            chunksize=chunksize,
            method="multi",
        )
        logger.info(f"[PostGIS] ✓ Loaded {len(df):,} rows into {schema}.{table}")
        return len(df)

    # ── Pipeline run logging ──────────────────────────────────────────────────

    def log_run_start(
        self,
        pipeline_name: str,
        params: Optional[dict] = None,
    ) -> str:
        """Insert a pipeline run record and return the run_id."""
        run_id = str(uuid.uuid4())
        with self.engine.begin() as conn:
            conn.execute(
                text("""
                    INSERT INTO metadata.pipeline_runs
                        (run_id, pipeline_name, started_at, status, params)
                    VALUES
                        (:run_id, :name, :started_at, 'running', :params)
                """),
                {
                    "run_id": run_id,
                    "name": pipeline_name,
                    "started_at": datetime.now(timezone.utc),
                    "params": json.dumps(params or {}),
                },
            )
        logger.info(f"[PostGIS] Pipeline run started: {run_id}")
        return run_id

    def log_run_end(
        self,
        run_id: str,
        status: Literal["success", "failed"],
        records_in: Optional[int] = None,
        records_out: Optional[int] = None,
        error_message: Optional[str] = None,
    ) -> None:
        """Update the pipeline run record with completion status."""
        with self.engine.begin() as conn:
            conn.execute(
                text("""
                    UPDATE metadata.pipeline_runs SET
                        finished_at   = :finished_at,
                        status        = :status,
                        records_in    = :records_in,
                        records_out   = :records_out,
                        error_message = :error_message
                    WHERE run_id = :run_id
                """),
                {
                    "run_id": run_id,
                    "finished_at": datetime.now(timezone.utc),
                    "status": status,
                    "records_in": records_in,
                    "records_out": records_out,
                    "error_message": error_message,
                },
            )
        logger.info(f"[PostGIS] Pipeline run {run_id} → {status}")

    # ── Helpers ───────────────────────────────────────────────────────────────

    @staticmethod
    def _prepare_dataframe(gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
        """
        Convert non-serialisable columns (dicts/lists) to JSON strings
        so they can be stored in TEXT or JSONB columns.
        """
        for col in gdf.columns:
            if gdf[col].dtype == object and col != gdf.geometry.name:
                sample = gdf[col].dropna().head(1)
                if not sample.empty and isinstance(sample.iloc[0], (dict, list)):
                    gdf[col] = gdf[col].apply(
                        lambda x: json.dumps(x) if isinstance(x, (dict, list)) else x
                    )
        return gdf
