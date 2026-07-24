"""Tests for the GBIF extractor — HTTP is mocked."""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import geopandas as gpd
import pytest

from biofrag.extract.gbif import GBIFExtractor


def _mock_gbif_page(offset: int = 0, total: int = 3, end: bool = True) -> dict:
    """Return a synthetic GBIF API response page."""
    results = [
        {
            "key": 100 + i + offset,
            "species": f"Panthera leo" if i == 0 else f"Loxodonta africana",
            "kingdom": "Animalia",
            "phylum": "Chordata",
            "class": "Mammalia",
            "order": "Carnivora",
            "family": "Felidae",
            "genus": "Panthera",
            "countryCode": "ZA",
            "datasetKey": "abc-123",
            "eventDate": "2023-06-15",
            "basisOfRecord": "HUMAN_OBSERVATION",
            "coordinateUncertaintyInMeters": 100,
            "decimalLatitude": -33.5 + i * 0.1,
            "decimalLongitude": 18.5 + i * 0.1,
        }
        for i in range(3)
    ]
    return {"count": total, "endOfRecords": end, "results": results}


def test_gbif_returns_geodataframe(tmp_path):
    extractor = GBIFExtractor(cache_dir=tmp_path)
    mock_resp = MagicMock()
    mock_resp.json.return_value = _mock_gbif_page()
    mock_resp.raise_for_status = MagicMock()

    with patch.object(extractor.session, "get", return_value=mock_resp):
        result = extractor.extract(bbox=(18.0, -34.5, 19.5, -33.0))

    assert isinstance(result, gpd.GeoDataFrame)
    assert len(result) == 3
    assert result.crs.to_epsg() == 4326


def test_gbif_geometry_is_point(tmp_path):
    extractor = GBIFExtractor(cache_dir=tmp_path)
    mock_resp = MagicMock()
    mock_resp.json.return_value = _mock_gbif_page()
    mock_resp.raise_for_status = MagicMock()

    with patch.object(extractor.session, "get", return_value=mock_resp):
        result = extractor.extract(bbox=(18.0, -34.5, 19.5, -33.0))

    from shapely.geometry import Point
    assert all(isinstance(g, Point) for g in result.geometry)


def test_gbif_required_columns(tmp_path):
    extractor = GBIFExtractor(cache_dir=tmp_path)
    mock_resp = MagicMock()
    mock_resp.json.return_value = _mock_gbif_page()
    mock_resp.raise_for_status = MagicMock()

    with patch.object(extractor.session, "get", return_value=mock_resp):
        result = extractor.extract(bbox=(18.0, -34.5, 19.5, -33.0))

    assert "species" in result.columns
    assert "gbif_key" in result.columns
    assert "country_code" in result.columns


def test_gbif_empty_response_returns_empty_gdf(tmp_path):
    extractor = GBIFExtractor(cache_dir=tmp_path)
    mock_resp = MagicMock()
    mock_resp.json.return_value = {"count": 0, "endOfRecords": True, "results": []}
    mock_resp.raise_for_status = MagicMock()

    with patch.object(extractor.session, "get", return_value=mock_resp):
        result = extractor.extract(bbox=(18.0, -34.5, 19.5, -33.0))

    assert isinstance(result, gpd.GeoDataFrame)
    assert len(result) == 0
