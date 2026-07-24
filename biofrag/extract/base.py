"""Base extractor class — all extractors inherit from this."""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Optional

import geopandas as gpd
import requests
from requests.adapters import HTTPAdapter
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)
from urllib3.util.retry import Retry

from biofrag.utils.logging import logger


class BaseExtractor(ABC):
    """
    Abstract base for all Bio-FRAG-ETL data extractors.

    Subclasses implement `extract()` and return a GeoDataFrame
    (for vector sources) or a file path (for raster sources).
    """

    name: str = "base"

    def __init__(self, cache_dir: Optional[Path] = None):
        self.cache_dir = cache_dir or Path("./data/raw")
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.session = self._build_session()

    # ── HTTP session with retry/backoff ───────────────────────────────────────

    def _build_session(self) -> requests.Session:
        session = requests.Session()
        retry_strategy = Retry(
            total=5,
            backoff_factor=1,
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=["GET", "POST"],
        )
        adapter = HTTPAdapter(max_retries=retry_strategy)
        session.mount("https://", adapter)
        session.mount("http://", adapter)
        return session

    def _get(self, url: str, **kwargs) -> requests.Response:
        """GET with automatic retry and rate-limit handling."""
        resp = self.session.get(url, timeout=30, **kwargs)
        if resp.status_code == 429:
            retry_after = int(resp.headers.get("Retry-After", 60))
            logger.warning(f"Rate limited by {url}. Waiting {retry_after}s...")
            time.sleep(retry_after)
            resp = self.session.get(url, timeout=30, **kwargs)
        resp.raise_for_status()
        return resp

    def _post(self, url: str, **kwargs) -> requests.Response:
        resp = self.session.post(url, timeout=30, **kwargs)
        resp.raise_for_status()
        return resp

    # ── Cache helpers ─────────────────────────────────────────────────────────

    def cache_path(self, filename: str) -> Path:
        return self.cache_dir / filename

    def is_cached(self, filename: str) -> bool:
        return self.cache_path(filename).exists()

    # ── Interface ─────────────────────────────────────────────────────────────

    @abstractmethod
    def extract(
        self,
        bbox: tuple[float, float, float, float],
        **kwargs: Any,
    ) -> gpd.GeoDataFrame | Path:
        """
        Extract data for the given bounding box (west, south, east, north).

        Returns:
            GeoDataFrame for vector data.
            Path to downloaded file for raster data.
        """

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}(cache_dir={self.cache_dir})"
