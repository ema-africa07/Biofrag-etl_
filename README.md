# 🌍 Bio-FRAG-ETL

**Biodiversity Fragmentation & Corridor Monitoring ETL Pipeline**

*Built by [Earth-Metrics Africa](https://earthmetricsafrica.com) — Enabling Smarter Conservation through Spatial Intelligence*

---

[![Python](https://img.shields.io/badge/Python-3.10%2B-blue?logo=python)](https://www.python.org/)
[![PostGIS](https://img.shields.io/badge/PostGIS-16--3.4-336791?logo=postgresql)](https://postgis.net/)
[![GeoServer](https://img.shields.io/badge/GeoServer-2.24-orange)](https://geoserver.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Code style: black](https://img.shields.io/badge/code%20style-black-000000.svg)](https://github.com/psf/black)

---

## Overview

Bio-FRAG-ETL is a production-grade geospatial data pipeline that automates the **extraction, transformation, and loading** of multi-source biodiversity and land-cover data to produce analysis-ready **habitat fragmentation metrics** and **wildlife corridor models** for SADC Transfrontier Conservation Areas (TFCAs).

The pipeline addresses a critical gap in regional conservation monitoring: species occurrence data (GBIF), land cover (ESA WorldCover), protected areas (WDPA), and road networks (OpenStreetMap) all exist in isolation — with no automated system connecting them into actionable spatial intelligence.

```
┌─────────────────────────────────────────────────────────────────────┐
│                      Bio-FRAG-ETL Pipeline                          │
├──────────────┬──────────────────────────────┬───────────────────────┤
│   EXTRACT    │         TRANSFORM            │        LOAD           │
│              │                              │                       │
│  GBIF API ──►│  Habitat Patch               │  PostGIS              │
│  ESA Tiles──►│  Delineation             ───►│  (raw / processed /   │
│  WDPA API ──►│                              │   outputs schemas)    │
│  OSM      ──►│  Fragmentation Metrics   ───►│                       │
│              │  (NP, LPI, ED, DIVISION)     │  GeoServer WMS/WFS    │
│              │                              │                       │
│              │  Corridor Modelling      ───►│  Dashboards & Reports │
│              │  (least-cost paths)          │                       │
└──────────────┴──────────────────────────────┴───────────────────────┘
```

---

## Features

- **Automated multi-source ingestion** — GBIF, ESA WorldCover, WDPA, OpenStreetMap, CHIRPS
- **Habitat patch delineation** from 10m satellite land cover with shape metrics (LPI, ED, fractal dimension)
- **FRAGSTATS-equivalent landscape metrics** computed on a configurable analysis grid
- **Least-cost corridor modelling** between major habitat patches, accounting for road barriers and PA permeability
- **PostGIS storage** across raw / processed / outputs schemas with full data lineage logging
- **GeoServer publishing** — all layers served as OGC-compliant WMS/WFS automatically
- **CLI interface** — run the full pipeline or individual steps from the terminal
- **Docker infrastructure** — PostGIS + GeoServer spun up in one command

---

## Data Sources

| Source | Type | Provider | Licence |
|--------|------|----------|---------|
| [ESA WorldCover 10m](https://esa-worldcover.org) | Raster | ESA / AWS S3 | CC-BY 4.0 |
| [GBIF Occurrences](https://www.gbif.org) | Vector (API) | GBIF | CC-BY 4.0 |
| [WDPA Protected Areas](https://www.protectedplanet.net) | Vector (file) | UNEP-WCMC | CC-BY 3.0 |
| [OpenStreetMap Roads](https://www.openstreetmap.org) | Vector (Overpass) | OSM contributors | ODbL |
| [CHIRPS Rainfall](https://www.chc.ucsb.edu/data/chirps) | Raster | CHC/UCSB | Public Domain |

---

## Fragmentation Metrics

| Metric | Description |
|--------|-------------|
| **NP** — Number of Patches | Total patch count per grid cell |
| **LPI** — Largest Patch Index | % of cell area in the single largest patch |
| **ED** — Edge Density | Total edge length per hectare (m/ha) |
| **DIVISION** — Division Index | Probability two random points are in different patches (0→1) |
| **COHESION** — Patch Cohesion | Physical connectedness of habitat (0→100) |
| **AI** — Aggregation Index | Spatial aggregation of habitat (0→100) |

---

## Quick Start

### Prerequisites

- Python 3.10+
- [Docker Desktop](https://www.docker.com/products/docker-desktop/)
- Git

### 1. Clone the repository

```bash
git clone https://github.com/earthmetricsafrica/biofrag-etl.git
cd biofrag-etl
```

### 2. Configure environment

```bash
cp .env.example .env
```

Open `.env` and fill in your credentials:

```env
GBIF_USERNAME=your_gbif_username        # free at gbif.org
GBIF_PASSWORD=your_gbif_password
WDPA_API_TOKEN=your_wdpa_token          # or use local file — see below
```

> **No WDPA token?** Download the WDPA GeoPackage directly from
> [protectedplanet.net](https://www.protectedplanet.net/en/thematic-areas/wdpa)
> and set `local_file=Path("data/raw/WDPA_Mar2025.gpkg")` in the extractor.

### 3. Start the infrastructure

```bash
docker compose up -d
```

This starts:
- **PostGIS** at `localhost:5432`
- **GeoServer** at `http://localhost:8080/geoserver`

Wait ~30 seconds for GeoServer to fully initialise, then verify:

```bash
docker compose ps
```

### 4. Install the Python package

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate

pip install -e ".[dev]"
```

### 5. Run the pipeline

```bash
# Full pipeline — default SADC study area
biofrag run

# Dry run — validate without writing to database
biofrag run --dry-run

# Override bounding box (West South East North)
biofrag run --bbox 18.0 -34.5 19.5 -33.0

# Check database status and run history
biofrag db status
```

---

## CLI Reference

```bash
biofrag run                          # Full ETL pipeline
biofrag run --dry-run                # Validate without DB writes
biofrag run --bbox W S E N          # Custom bounding box

biofrag extract gbif                 # GBIF occurrences only
biofrag extract wdpa                 # WDPA protected areas only
biofrag extract osm                  # OSM road network only

biofrag db status                    # PostGIS connection + run history
biofrag publish                      # Push layers to GeoServer
```

---

## Project Structure

```
biofrag-etl/
├── biofrag/
│   ├── config.py              # Pydantic settings — all config from .env
│   ├── cli.py                 # Click CLI entry point
│   ├── extract/
│   │   ├── base.py            # BaseExtractor (retry, caching, sessions)
│   │   ├── gbif.py            # GBIF Occurrence Search API
│   │   ├── wdpa.py            # Protected Planet API + local file mode
│   │   ├── osm.py             # OpenStreetMap Overpass API
│   │   └── esa_worldcover.py  # ESA WorldCover S3 tile downloader
│   ├── transform/
│   │   ├── habitat_patches.py # Raster → vector patch delineation
│   │   ├── fragmentation.py   # Landscape fragmentation metrics per grid
│   │   └── corridors.py       # Least-cost corridor modelling
│   ├── load/
│   │   ├── postgis.py         # PostGIS loader with run logging
│   │   └── geoserver.py       # GeoServer REST API publisher
│   ├── pipeline/
│   │   └── runner.py          # Full ETL orchestrator
│   └── utils/
│       ├── geo.py             # Spatial helper functions
│       └── logging.py         # Loguru configuration
├── tests/
│   ├── conftest.py            # Shared pytest fixtures
│   ├── test_extract/
│   │   └── test_gbif.py       # GBIF extractor tests (HTTP mocked)
│   └── test_transform/
│       ├── test_fragmentation.py
│       └── test_corridors.py
├── scripts/
│   └── init_db.sql            # PostGIS schema initialisation
├── data/                      # gitignored — created at runtime
│   ├── raw/                   # Downloaded source files & raster cache
│   ├── processed/             # Intermediate outputs
│   └── outputs/               # Final deliverables
├── notebooks/                 # Jupyter exploration notebooks
├── docker-compose.yml         # PostGIS + GeoServer + pgAdmin
├── pyproject.toml
├── .env.example               # Environment variable template
└── README.md
```

---

## Database Schema

Data is stored across four PostGIS schemas:

```sql
raw.species_occurrences       -- GBIF occurrence points
raw.protected_areas           -- WDPA polygons
raw.road_network              -- OSM road linestrings

processed.habitat_patches     -- Delineated natural habitat polygons
processed.fragmentation_metrics -- Grid-cell fragmentation indices
processed.corridors           -- Modelled wildlife corridor linestrings

outputs.fragmentation_hotspots -- View: metrics + threat classification

metadata.pipeline_runs        -- Full audit trail of every pipeline run
metadata.data_sources         -- Source dataset registry
```

---

## Running Tests

```bash
pytest                              # All tests with coverage report
pytest tests/test_transform/        # Transform unit tests only
pytest tests/test_extract/          # Extractor tests (HTTP mocked)
pytest -k "fragmentation"           # Filter by name
pytest -v --tb=short                # Verbose with short tracebacks
```

---

## GeoServer Access

After running the pipeline, layers are available at:

| Service | URL |
|---------|-----|
| WMS | `http://localhost:8080/geoserver/biofrag/wms` |
| WFS | `http://localhost:8080/geoserver/biofrag/wfs` |
| Admin UI | `http://localhost:8080/geoserver` |

Connect directly in **QGIS**: Layer → Add Layer → Add WMS/WMTS Layer → paste WMS URL.

---

## Configuration Reference

All settings are loaded from `.env`. Key variables:

| Variable | Default | Description |
|----------|---------|-------------|
| `POSTGRES_HOST` | `localhost` | PostGIS host |
| `POSTGRES_DB` | `biofrag` | Database name |
| `GBIF_USERNAME` | — | GBIF account username |
| `WDPA_API_TOKEN` | — | Protected Planet API token |
| `STUDY_BBOX_WEST/SOUTH/EAST/NORTH` | SADC region | Study area bounds |
| `PIPELINE_WORKERS` | `4` | Parallel processing workers |
| `GRID_RESOLUTION_KM` | `10` | Analysis grid cell size |
| `MIN_PATCH_AREA_HA` | `100` | Minimum habitat patch to retain |

---

## Alignment with Conservation Frameworks

| Framework | Alignment |
|-----------|-----------|
| **Kunming-Montreal 30×30** | Generates spatial evidence for 30% protection targets |
| **SDG 15 — Life on Land** | Quantifies anthropogenic fragmentation pressure |
| **SADC TFCA Programme** | Corridor health metrics and cross-border data harmonisation |
| **AU Biodiversity Strategy 2035** | Standardised, open-format fragmentation datasets |

---

## Contributing

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/your-feature`)
3. Commit your changes (`git commit -m 'Add your feature'`)
4. Push to the branch (`git push origin feature/your-feature`)
5. Open a Pull Request

Please run `pytest` and `ruff check biofrag/` before submitting.

---

## Licence

MIT © [Earth-Metrics Africa](https://earthmetricsafrica.com)

---

## Contact

**Earth-Metrics Africa**
📧 data@earthmetricsafrica.com
🌐 [earthmetricsafrica.com](https://earthmetricsafrica.com)
🔗 [LinkedIn](https://linkedin.com/company/earthmetricsafrica)
