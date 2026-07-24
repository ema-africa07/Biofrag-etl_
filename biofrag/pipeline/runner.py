"""
Bio-FRAG-ETL Pipeline Runner.

Orchestrates the full Extract → Transform → Load pipeline:

  1. EXTRACT
     a. ESA WorldCover raster tiles  (habitat baseline)
     b. GBIF species occurrences     (biodiversity pressure)
     c. WDPA protected areas         (conservation context)
     d. OSM road network             (fragmentation driver)

  2. TRANSFORM
     a. Delineate habitat patches from raster
     b. Compute fragmentation metrics per grid cell
     c. Model wildlife corridors between major patches

  3. LOAD
     a. Write all layers to PostGIS
     b. Publish WMS/WFS services to GeoServer
     c. Log pipeline run to metadata schema

Usage:
    from biofrag.pipeline.runner import PipelineRunner
    from biofrag.config import get_settings

    settings = get_settings()
    runner = PipelineRunner(settings)
    runner.run()
"""

from __future__ import annotations

import traceback
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

import geopandas as gpd

from biofrag.config import Settings
from biofrag.extract import (
    ESAWorldCoverExtractor,
    GBIFExtractor,
    OSMExtractor,
    WDPAExtractor,
)
from biofrag.load import GeoServerPublisher, PostGISLoader
from biofrag.transform import (
    CorridorModeller,
    FragmentationAnalyser,
    HabitatPatchDelineator,
)
from biofrag.utils.logging import logger, setup_logging


@dataclass
class PipelineResult:
    """Summary of a completed pipeline run."""
    run_id: Optional[str] = None
    status: str = "unknown"
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    records: dict = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)

    @property
    def duration_seconds(self) -> Optional[float]:
        if self.started_at and self.finished_at:
            return (self.finished_at - self.started_at).total_seconds()
        return None

    def __str__(self) -> str:
        lines = [
            f"Pipeline Run: {self.run_id}",
            f"Status:       {self.status}",
            f"Duration:     {self.duration_seconds:.1f}s" if self.duration_seconds else "",
            f"Records:      {self.records}",
        ]
        if self.errors:
            lines.append(f"Errors:       {self.errors}")
        return "\n".join(l for l in lines if l)


class PipelineRunner:
    """
    Full Bio-FRAG-ETL pipeline orchestrator.

    Args:
        settings: Loaded Settings object (from config.get_settings()).
        dry_run:  If True, run all steps but skip DB writes and GeoServer publish.
    """

    def __init__(self, settings: Settings, dry_run: bool = False):
        self.cfg = settings
        self.dry_run = dry_run

        setup_logging(
            level=settings.pipeline.log_level,
            log_file=settings.pipeline.data_dir / "logs" / "pipeline.log",
        )

        # Initialise components
        self.loader = PostGISLoader(settings.db.url)
        self.publisher = GeoServerPublisher(
            url=settings.geoserver.url,
            user=settings.geoserver.user,
            password=settings.geoserver.password,
            workspace=settings.geoserver.workspace,
        )

    # ── Public API ────────────────────────────────────────────────────────────

    def run(self) -> PipelineResult:
        """Execute the full ETL pipeline."""
        result = PipelineResult(started_at=datetime.utcnow())

        if not self.dry_run:
            result.run_id = self.loader.log_run_start(
                "bio_frag_etl_full",
                params={
                    "bbox": self.cfg.study_area.bbox,
                    "grid_km": self.cfg.pipeline.grid_resolution_km,
                    "min_patch_ha": self.cfg.pipeline.min_patch_area_ha,
                },
            )

        logger.info("=" * 60)
        logger.info("Bio-FRAG-ETL Pipeline Starting")
        logger.info(f"Study area: {self.cfg.study_area.bbox}")
        logger.info(f"Dry run:    {self.dry_run}")
        logger.info("=" * 60)

        try:
            # ── 1. EXTRACT ────────────────────────────────────────────────
            raster_path, gbif_gdf, wdpa_gdf, roads_gdf = self._extract()

            # ── 2. TRANSFORM ──────────────────────────────────────────────
            patches_gdf, metrics_gdf, corridors_gdf = self._transform(
                raster_path, wdpa_gdf, roads_gdf
            )

            # ── 3. LOAD ───────────────────────────────────────────────────
            records = self._load(gbif_gdf, wdpa_gdf, roads_gdf, patches_gdf, metrics_gdf, corridors_gdf)

            result.records = records
            result.status = "success"
            logger.info("=" * 60)
            logger.info(f"Pipeline complete ✓  |  {result.records}")
            logger.info("=" * 60)

        except Exception as exc:
            result.status = "failed"
            result.errors.append(str(exc))
            logger.error(f"Pipeline FAILED: {exc}")
            logger.debug(traceback.format_exc())

            if not self.dry_run and result.run_id:
                self.loader.log_run_end(
                    result.run_id, "failed", error_message=str(exc)
                )
            raise

        finally:
            result.finished_at = datetime.utcnow()

        if not self.dry_run and result.run_id:
            self.loader.log_run_end(
                result.run_id,
                "success",
                records_in=sum(v for v in result.records.values() if isinstance(v, int)),
                records_out=result.records.get("corridors", 0),
            )

        return result

    # ── Stage methods ─────────────────────────────────────────────────────────

    def _extract(self):
        """Run all extractors and return raw data."""
        bbox = self.cfg.study_area.bbox
        cache_dir = self.cfg.pipeline.raw_dir

        logger.info("[1/3] EXTRACT")

        # ESA WorldCover raster
        logger.info("[Extract] ESA WorldCover...")
        esa = ESAWorldCoverExtractor(year=2021, cache_dir=cache_dir)
        raster_path = esa.extract(bbox=bbox)

        # GBIF species occurrences
        logger.info("[Extract] GBIF species occurrences...")
        gbif = GBIFExtractor(
            page_size=self.cfg.gbif.page_size,
            max_records=self.cfg.gbif.max_records,
            cache_dir=cache_dir,
        )
        gbif_gdf = gbif.extract(
            bbox=bbox,
            taxon_key=1,   # Animalia
            has_coordinate=True,
            has_geospatial_issue=False,
        )
        logger.info(f"[Extract] GBIF: {len(gbif_gdf):,} records")

        # WDPA protected areas
        logger.info("[Extract] WDPA protected areas...")
        wdpa = WDPAExtractor(
            api_token=self.cfg.wdpa.api_token,
            cache_dir=cache_dir,
        )
        wdpa_gdf = wdpa.extract(
            bbox=bbox,
            iucn_categories=["Ia", "Ib", "II", "III", "IV", "V", "VI"],
            exclude_marine=True,
        )
        logger.info(f"[Extract] WDPA: {len(wdpa_gdf):,} protected areas")

        # OSM road network
        logger.info("[Extract] OSM road network...")
        osm = OSMExtractor(cache_dir=cache_dir)
        roads_gdf = osm.extract(bbox=bbox)
        logger.info(f"[Extract] OSM: {len(roads_gdf):,} road segments")

        return raster_path, gbif_gdf, wdpa_gdf, roads_gdf

    def _transform(self, raster_path: Path, wdpa_gdf, roads_gdf):
        """Run all transform steps."""
        bbox = self.cfg.study_area.bbox
        pipeline_cfg = self.cfg.pipeline

        logger.info("[2/3] TRANSFORM")

        # Habitat patch delineation
        logger.info("[Transform] Delineating habitat patches...")
        delineator = HabitatPatchDelineator(
            min_patch_area_ha=pipeline_cfg.min_patch_area_ha,
            include_semi_natural=True,
        )
        patches_gdf = delineator.run(raster_path)
        logger.info(f"[Transform] Patches: {len(patches_gdf):,}")

        # Fragmentation metrics
        logger.info("[Transform] Computing fragmentation metrics...")
        analyser = FragmentationAnalyser(
            grid_resolution_km=pipeline_cfg.grid_resolution_km
        )
        metrics_gdf = analyser.run(patches_gdf=patches_gdf, study_bbox=bbox)
        logger.info(f"[Transform] Grid cells: {len(metrics_gdf):,}")

        # Corridor modelling
        logger.info("[Transform] Modelling wildlife corridors...")
        modeller = CorridorModeller(
            max_distance_km=50.0,
            top_n_pairs=100,
            min_patch_area_ha=pipeline_cfg.min_patch_area_ha * 5,
        )
        corridors_gdf = modeller.run(
            patches_gdf=patches_gdf,
            roads_gdf=roads_gdf,
            wdpa_gdf=wdpa_gdf,
        )
        logger.info(f"[Transform] Corridors: {len(corridors_gdf):,}")

        return patches_gdf, metrics_gdf, corridors_gdf

    def _load(
        self,
        gbif_gdf,
        wdpa_gdf,
        roads_gdf,
        patches_gdf,
        metrics_gdf,
        corridors_gdf,
    ) -> dict:
        """Write all data to PostGIS and publish to GeoServer."""
        logger.info("[3/3] LOAD")

        if self.dry_run:
            logger.info("[Load] DRY RUN — skipping all writes")
            return {
                "gbif_records": len(gbif_gdf),
                "wdpa_areas": len(wdpa_gdf),
                "road_segments": len(roads_gdf),
                "habitat_patches": len(patches_gdf),
                "fragmentation_cells": len(metrics_gdf),
                "corridors": len(corridors_gdf),
            }

        records = {}

        # Raw layer ingestion
        records["gbif_records"] = self.loader.load_geodataframe(
            gbif_gdf, "species_occurrences", schema="raw", if_exists="append"
        )
        records["wdpa_areas"] = self.loader.load_geodataframe(
            wdpa_gdf, "protected_areas", schema="raw", if_exists="replace"
        )
        records["road_segments"] = self.loader.load_geodataframe(
            roads_gdf, "road_network", schema="raw", if_exists="replace"
        )

        # Processed layers
        records["habitat_patches"] = self.loader.load_geodataframe(
            patches_gdf, "habitat_patches", schema="processed", if_exists="replace"
        )
        records["fragmentation_cells"] = self.loader.load_geodataframe(
            metrics_gdf, "fragmentation_metrics", schema="processed", if_exists="replace"
        )
        records["corridors"] = self.loader.load_geodataframe(
            corridors_gdf, "corridors", schema="processed", if_exists="replace"
        )

        # Publish to GeoServer
        logger.info("[Load] Publishing layers to GeoServer...")
        try:
            self.publisher.publish_all_biofrag_layers(
                db_host=self.cfg.db.host,
                db_port=self.cfg.db.port,
                db_name=self.cfg.db.db,
                db_user=self.cfg.db.user,
                db_password=self.cfg.db.password,
            )
        except Exception as exc:
            logger.warning(f"[Load] GeoServer publishing failed (non-fatal): {exc}")

        return records
