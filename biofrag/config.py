"""
Centralised configuration for Bio-FRAG-ETL.
All settings are loaded from environment variables or a .env file.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class DatabaseSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="POSTGRES_", env_file=".env", extra="ignore")

    host: str = "localhost"
    port: int = 5434
    db: str = "biofrag"
    user: str = "biofrag"
    password: str = "biofrag_secret"

    @property
    def url(self) -> str:
        return f"postgresql+psycopg2://{self.user}:{self.password}@{self.host}:{self.port}/{self.db}"

    @property
    def async_url(self) -> str:
        return f"postgresql+asyncpg://{self.user}:{self.password}@{self.host}:{self.port}/{self.db}"


class GeoServerSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="GEOSERVER_", env_file=".env", extra="ignore")

    url: str = "http://localhost:8080/geoserver"
    user: str = "admin"
    password: str = "geoserver"
    workspace: str = "biofrag"

    @property
    def rest_url(self) -> str:
        return f"{self.url}/rest"


class GBIFSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="GBIF_", env_file=".env", extra="ignore")

    username: Optional[str] = None
    password: Optional[str] = None
    email: Optional[str] = None

    # Pagination
    page_size: int = 300
    max_records: int = 100_000

    # API endpoint
    occurrence_url: str = "https://api.gbif.org/v1/occurrence/search"
    download_url: str = "https://api.gbif.org/v1/occurrence/download"


class WDPASettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="WDPA_", env_file=".env", extra="ignore")

    api_token: Optional[str] = None
    base_url: str = "https://api.protectedplanet.net/v3"
    page_size: int = 25


class StudyAreaSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="STUDY_", env_file=".env", extra="ignore")

    bbox_west: float = Field(12.0, description="Western boundary longitude")
    bbox_south: float = Field(-35.0, description="Southern boundary latitude")
    bbox_east: float = Field(40.5, description="Eastern boundary longitude")
    bbox_north: float = Field(-8.0, description="Northern boundary latitude")

    @property
    def bbox(self) -> tuple[float, float, float, float]:
        """Return (west, south, east, north) bounding box."""
        return (self.bbox_west, self.bbox_south, self.bbox_east, self.bbox_north)

    @property
    def bbox_wkt(self) -> str:
        """Return bounding box as WKT polygon."""
        w, s, e, n = self.bbox
        return f"POLYGON(({w} {s}, {e} {s}, {e} {n}, {w} {n}, {w} {s}))"


class PipelineSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    data_dir: Path = Path("./data")
    log_level: str = "INFO"
    pipeline_workers: int = 4
    target_crs: int = Field(4326, description="Target CRS EPSG code")

    # Analysis parameters
    grid_resolution_km: float = Field(10.0, description="Fragmentation analysis grid size in km")
    min_patch_area_ha: float = Field(100.0, description="Minimum habitat patch area to retain")
    corridor_max_cost: float = Field(10000.0, description="Max cost-distance for viable corridors")

    @field_validator("data_dir")
    @classmethod
    def ensure_data_dirs(cls, v: Path) -> Path:
        for sub in ("raw", "processed", "outputs"):
            (v / sub).mkdir(parents=True, exist_ok=True)
        return v

    @property
    def raw_dir(self) -> Path:
        return self.data_dir / "raw"

    @property
    def processed_dir(self) -> Path:
        return self.data_dir / "processed"

    @property
    def outputs_dir(self) -> Path:
        return self.data_dir / "outputs"


class Settings(BaseSettings):
    """Root settings object — instantiate once and inject everywhere."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    db: DatabaseSettings = DatabaseSettings()
    geoserver: GeoServerSettings = GeoServerSettings()
    gbif: GBIFSettings = GBIFSettings()
    wdpa: WDPASettings = WDPASettings()
    study_area: StudyAreaSettings = StudyAreaSettings()
    pipeline: PipelineSettings = PipelineSettings()

    @model_validator(mode="after")
    def validate_credentials(self) -> "Settings":
        missing = []
        if not self.wdpa.api_token:
            missing.append("WDPA_API_TOKEN")
        if missing:
            import warnings
            warnings.warn(
                f"Missing optional credentials: {', '.join(missing)}. "
                "Some extractors will be disabled.",
                stacklevel=2,
            )
        return self


# ── Module-level singleton ─────────────────────────────────────────────────────
_settings: Optional[Settings] = None


def get_settings() -> Settings:
    """Return the cached settings singleton."""
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings
