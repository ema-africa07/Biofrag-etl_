"""
GeoServer Publisher.

Publishes PostGIS layers to GeoServer as OGC-compliant WMS/WFS services
using the GeoServer REST API. Creates the workspace, PostGIS datastore,
and layer in one call.

GeoServer REST API docs: https://docs.geoserver.org/stable/en/user/rest/
"""

from __future__ import annotations

import json
from typing import Optional

import requests
from requests.auth import HTTPBasicAuth

from biofrag.utils.logging import logger


class GeoServerPublisher:
    """
    Publish Bio-FRAG-ETL layers to GeoServer via REST API.

    Example:
        publisher = GeoServerPublisher(
            url="http://localhost:8080/geoserver",
            user="admin",
            password="geoserver",
            workspace="biofrag",
        )
        publisher.ensure_workspace()
        publisher.ensure_datastore(db_host="localhost", db_name="biofrag", ...)
        publisher.publish_layer("fragmentation_hotspots", schema="outputs")
    """

    def __init__(
        self,
        url: str,
        user: str,
        password: str,
        workspace: str = "biofrag",
        datastore: str = "biofrag_postgis",
    ):
        self.rest = url.rstrip("/") + "/rest"
        self.workspace = workspace
        self.datastore = datastore
        self.auth = HTTPBasicAuth(user, password)
        self.headers_json = {"Content-Type": "application/json"}
        self.headers_xml = {"Content-Type": "application/xml"}

    # ── Workspace ─────────────────────────────────────────────────────────────

    def ensure_workspace(self) -> None:
        """Create the workspace if it doesn't exist."""
        url = f"{self.rest}/workspaces/{self.workspace}.json"
        resp = requests.get(url, auth=self.auth)
        if resp.status_code == 200:
            logger.info(f"[GeoServer] Workspace '{self.workspace}' already exists")
            return

        payload = {"workspace": {"name": self.workspace}}
        resp = requests.post(
            f"{self.rest}/workspaces",
            auth=self.auth,
            headers=self.headers_json,
            json=payload,
        )
        resp.raise_for_status()
        logger.info(f"[GeoServer] Created workspace '{self.workspace}'")

    # ── Datastore ─────────────────────────────────────────────────────────────

    def ensure_datastore(
        self,
        db_host: str,
        db_port: int,
        db_name: str,
        db_user: str,
        db_password: str,
        db_schema: str = "processed",
    ) -> None:
        """Create a PostGIS datastore in GeoServer if it doesn't exist."""
        url = f"{self.rest}/workspaces/{self.workspace}/datastores/{self.datastore}.json"
        resp = requests.get(url, auth=self.auth)
        if resp.status_code == 200:
            logger.info(f"[GeoServer] Datastore '{self.datastore}' already exists")
            return

        payload = {
            "dataStore": {
                "name": self.datastore,
                "type": "PostGIS",
                "enabled": True,
                "connectionParameters": {
                    "entry": [
                        {"@key": "host",     "$": db_host},
                        {"@key": "port",     "$": str(db_port)},
                        {"@key": "database", "$": db_name},
                        {"@key": "user",     "$": db_user},
                        {"@key": "passwd",   "$": db_password},
                        {"@key": "dbtype",   "$": "postgis"},
                        {"@key": "schema",   "$": db_schema},
                        {"@key": "Expose primary keys", "$": "true"},
                        {"@key": "validate connections", "$": "true"},
                    ]
                },
            }
        }

        resp = requests.post(
            f"{self.rest}/workspaces/{self.workspace}/datastores",
            auth=self.auth,
            headers=self.headers_json,
            json=payload,
        )
        resp.raise_for_status()
        logger.info(f"[GeoServer] Created datastore '{self.datastore}'")

    # ── Layer publishing ──────────────────────────────────────────────────────

    def publish_layer(
        self,
        table_name: str,
        title: Optional[str] = None,
        abstract: Optional[str] = None,
        srs: str = "EPSG:4326",
        recalculate_bounds: bool = True,
    ) -> str:
        """
        Publish a PostGIS table as a GeoServer WMS/WFS layer.

        Args:
            table_name: PostGIS table name to publish.
            title:      Human-readable layer title.
            abstract:   Layer description for GetCapabilities.
            srs:        Declared SRS (default EPSG:4326).
            recalculate_bounds: Auto-calculate native bounding box.

        Returns:
            WMS endpoint URL for the published layer.
        """
        layer_url = (
            f"{self.rest}/workspaces/{self.workspace}/datastores/"
            f"{self.datastore}/featuretypes"
        )

        payload = {
            "featureType": {
                "name": table_name,
                "nativeName": table_name,
                "title": title or table_name.replace("_", " ").title(),
                "abstract": abstract or f"Bio-FRAG-ETL layer: {table_name}",
                "srs": srs,
                "projectionPolicy": "FORCE_DECLARED",
                "enabled": True,
                "advertised": True,
            }
        }

        if recalculate_bounds:
            payload["featureType"]["recalculate"] = "nativebbox,latlonbbox"

        # Check if already published
        check = requests.get(
            f"{self.rest}/workspaces/{self.workspace}/datastores/"
            f"{self.datastore}/featuretypes/{table_name}.json",
            auth=self.auth,
        )

        if check.status_code == 200:
            logger.info(f"[GeoServer] Layer '{table_name}' already published — updating")
            resp = requests.put(
                f"{layer_url}/{table_name}",
                auth=self.auth,
                headers=self.headers_json,
                json=payload,
            )
        else:
            resp = requests.post(
                layer_url,
                auth=self.auth,
                headers=self.headers_json,
                json=payload,
            )

        resp.raise_for_status()

        wms_url = (
            f"{self.rest.replace('/rest', '')}/wms?"
            f"SERVICE=WMS&VERSION=1.1.1&REQUEST=GetMap"
            f"&LAYERS={self.workspace}:{table_name}"
        )
        logger.info(f"[GeoServer] ✓ Published layer: {self.workspace}:{table_name}")
        logger.info(f"[GeoServer] WMS endpoint: {wms_url}")
        return wms_url

    def publish_all_biofrag_layers(
        self,
        db_host: str,
        db_port: int,
        db_name: str,
        db_user: str,
        db_password: str,
    ) -> dict[str, str]:
        """Convenience method: publish all Bio-FRAG-ETL output layers at once."""
        self.ensure_workspace()
        self.ensure_datastore(db_host, db_port, db_name, db_user, db_password, "processed")

        layers = {
            "habitat_patches": ("Habitat Patches", "Delineated natural habitat patches"),
            "fragmentation_metrics": ("Fragmentation Metrics", "Landscape fragmentation indices per grid cell"),
            "corridors": ("Wildlife Corridors", "Modelled least-cost wildlife movement corridors"),
        }

        # Also publish output views
        self.ensure_datastore(db_host, db_port, db_name, db_user, db_password, "outputs")
        layers["fragmentation_hotspots"] = (
            "Fragmentation Hotspots",
            "Fragmentation metrics with threat classification",
        )

        results = {}
        for table, (title, abstract) in layers.items():
            try:
                wms_url = self.publish_layer(table, title=title, abstract=abstract)
                results[table] = wms_url
            except Exception as exc:
                logger.error(f"[GeoServer] Failed to publish {table}: {exc}")
                results[table] = f"ERROR: {exc}"

        return results
