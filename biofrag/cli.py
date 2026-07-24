"""
Bio-FRAG-ETL Command Line Interface.

Usage:
    biofrag run               # full pipeline
    biofrag run --dry-run     # validate without writing
    biofrag extract gbif      # run one extractor
    biofrag extract wdpa
    biofrag extract osm
    biofrag db status         # check PostGIS connection
    biofrag publish           # push layers to GeoServer
"""

from __future__ import annotations

import click
from pathlib import Path

from biofrag.config import get_settings
from biofrag.utils.logging import setup_logging


@click.group()
@click.option("--log-level", default="INFO", show_default=True,
              type=click.Choice(["DEBUG", "INFO", "WARNING", "ERROR"]))
@click.pass_context
def main(ctx: click.Context, log_level: str) -> None:
    """Bio-FRAG-ETL — Biodiversity Fragmentation & Corridor Monitoring Pipeline."""
    ctx.ensure_object(dict)
    ctx.obj["log_level"] = log_level
    setup_logging(level=log_level)


# ── Run ───────────────────────────────────────────────────────────────────────

@main.command()
@click.option("--dry-run", is_flag=True, default=False,
              help="Run all steps but skip database writes.")
@click.option("--bbox", nargs=4, type=float, metavar="W S E N",
              help="Override study area bounding box (west south east north).")
@click.pass_context
def run(ctx: click.Context, dry_run: bool, bbox: tuple) -> None:
    """Run the full Extract → Transform → Load pipeline."""
    from biofrag.pipeline.runner import PipelineRunner

    settings = get_settings()
    if bbox:
        settings.study_area.bbox_west  = bbox[0]
        settings.study_area.bbox_south = bbox[1]
        settings.study_area.bbox_east  = bbox[2]
        settings.study_area.bbox_north = bbox[3]
        click.echo(f"Override bbox: W={bbox[0]} S={bbox[1]} E={bbox[2]} N={bbox[3]}")

    runner = PipelineRunner(settings, dry_run=dry_run)
    result = runner.run()
    click.echo(str(result))


# ── Extract sub-commands ──────────────────────────────────────────────────────

@main.group()
def extract() -> None:
    """Run individual data extractors."""


@extract.command("gbif")
@click.option("--taxon-key", type=int, default=1, show_default=True,
              help="GBIF taxon key (1=Animalia).")
@click.option("--max-records", type=int, default=10_000, show_default=True)
@click.option("--output", type=click.Path(), default="data/raw/gbif_occurrences.gpkg",
              show_default=True)
def extract_gbif(taxon_key: int, max_records: int, output: str) -> None:
    """Extract GBIF species occurrence records."""
    from biofrag.extract import GBIFExtractor

    cfg = get_settings()
    ex = GBIFExtractor(max_records=max_records, cache_dir=cfg.pipeline.raw_dir)
    gdf = ex.extract(bbox=cfg.study_area.bbox, taxon_key=taxon_key)

    out = Path(output)
    out.parent.mkdir(parents=True, exist_ok=True)
    gdf.to_file(out, driver="GPKG")
    click.echo(f"✓ Saved {len(gdf):,} records to {out}")


@extract.command("wdpa")
@click.option("--output", type=click.Path(), default="data/raw/wdpa.gpkg", show_default=True)
def extract_wdpa(output: str) -> None:
    """Extract WDPA protected area boundaries."""
    from biofrag.extract import WDPAExtractor

    cfg = get_settings()
    ex = WDPAExtractor(api_token=cfg.wdpa.api_token, cache_dir=cfg.pipeline.raw_dir)
    gdf = ex.extract(bbox=cfg.study_area.bbox)

    out = Path(output)
    out.parent.mkdir(parents=True, exist_ok=True)
    gdf.to_file(out, driver="GPKG")
    click.echo(f"✓ Saved {len(gdf):,} protected areas to {out}")


@extract.command("osm")
@click.option("--output", type=click.Path(), default="data/raw/roads.gpkg", show_default=True)
def extract_osm(output: str) -> None:
    """Extract OSM road network."""
    from biofrag.extract import OSMExtractor

    cfg = get_settings()
    ex = OSMExtractor(cache_dir=cfg.pipeline.raw_dir)
    gdf = ex.extract(bbox=cfg.study_area.bbox)

    out = Path(output)
    out.parent.mkdir(parents=True, exist_ok=True)
    gdf.to_file(out, driver="GPKG")
    click.echo(f"✓ Saved {len(gdf):,} road segments to {out}")


# ── DB status ─────────────────────────────────────────────────────────────────

@main.group()
def db() -> None:
    """Database management commands."""


@db.command("status")
def db_status() -> None:
    """Check PostGIS connection and list pipeline run history."""
    from biofrag.load import PostGISLoader
    from sqlalchemy import text

    cfg = get_settings()
    loader = PostGISLoader(cfg.db.url)

    if loader.test_connection():
        click.secho("✓ PostGIS connection OK", fg="green")
        with loader.engine.connect() as conn:
            rows = conn.execute(
                text("""
                    SELECT run_id, pipeline_name, started_at, status, records_out
                    FROM metadata.pipeline_runs
                    ORDER BY started_at DESC LIMIT 10
                """)
            ).fetchall()
        if rows:
            click.echo("\nLast 10 pipeline runs:")
            for r in rows:
                click.echo(f"  {r.started_at:%Y-%m-%d %H:%M}  {r.status:10}  {r.pipeline_name}  ({r.records_out} records)")
        else:
            click.echo("No pipeline runs recorded yet.")
    else:
        click.secho("✗ PostGIS connection FAILED", fg="red")
        raise click.Abort()


# ── Publish ───────────────────────────────────────────────────────────────────

@main.command()
def publish() -> None:
    """Publish all processed layers to GeoServer."""
    from biofrag.load import GeoServerPublisher

    cfg = get_settings()
    pub = GeoServerPublisher(
        url=cfg.geoserver.url,
        user=cfg.geoserver.user,
        password=cfg.geoserver.password,
        workspace=cfg.geoserver.workspace,
    )
    results = pub.publish_all_biofrag_layers(
        db_host=cfg.db.host,
        db_port=cfg.db.port,
        db_name=cfg.db.db,
        db_user=cfg.db.user,
        db_password=cfg.db.password,
    )
    for layer, url in results.items():
        status = "✓" if not str(url).startswith("ERROR") else "✗"
        click.echo(f"  {status} {layer}: {url}")


if __name__ == "__main__":
    main()
