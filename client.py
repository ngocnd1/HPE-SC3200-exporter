from __future__ import annotations

import logging
from urllib.parse import urljoin, urlparse

import requests
import urllib3
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from .config import Target

LOG = logging.getLogger(__name__)
_EXPAND_FALLBACK_STATUS = {400, 404, 405, 500, 501}
_ODATA_METADATA_KEYS = {"@odata.id", "@odata.type", "@odata.etag"}


class RedfishClient:
    """Read-only SC3200 client using one authenticated HTTP session.

    SC3200 advertises Redfish ExpandAll with MaxLevels=1. Collection member
    representations are requested with ``$expand=.`` first. This turns
    hundreds of per-sensor GETs into a few collection GETs. A conservative
    sequential fallback keeps the client usable if firmware ignores or rejects
    expansion.
    """

    def __init__(self, target: Target, timeout: float):
        self.target = target
        self.timeout = timeout
        self.session = requests.Session()
        self.session.auth = (target.username, target.password)
        self.session.headers.update({
            "Accept": "application/json",
            "OData-Version": "4.0",
            "User-Agent": "hpe-sc3200-redfish-exporter/0.3.1",
        })
        retry = Retry(
            total=2,
            connect=2,
            read=2,
            status=0,
            other=2,
            backoff_factor=0.25,
            allowed_methods=frozenset({"GET"}),
            raise_on_status=False,
        )
        adapter = HTTPAdapter(max_retries=retry)
        self.session.mount("https://", adapter)
        self.session.mount("http://", adapter)
        if target.verify is False:
            urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

    def get(self, path: str) -> dict:
        base = self.target.address + "/"
        url = urljoin(base, path.lstrip("/"))
        if urlparse(url).netloc != urlparse(base).netloc:
            raise ValueError("Redfish link points outside configured target")
        response = self.session.get(
            url, timeout=self.timeout, verify=self.target.verify, allow_redirects=False
        )
        response.raise_for_status()
        data = response.json()
        if not isinstance(data, dict):
            raise ValueError(f"expected JSON object from {path}")
        return data

    @staticmethod
    def _expanded(members: list[dict]) -> bool:
        """True when every member contains data beyond OData link metadata."""
        return all(
            isinstance(member, dict) and bool(set(member) - _ODATA_METADATA_KEYS)
            for member in members
        )

    def members(self, collection_uri: str) -> list[dict]:
        """Return collection members, preferring SC3200 ``$expand=.``.

        Authentication errors are never hidden. Only statuses indicating an
        unsupported/broken expand query fall back to the ordinary collection.
        If firmware silently ignores the query, member links are followed
        sequentially using the same authenticated Session as v0.2.0.
        """
        separator = "&" if "?" in collection_uri else "?"
        expand_uri = f"{collection_uri}{separator}$expand=."
        try:
            collection = self.get(expand_uri)
        except requests.HTTPError as exc:
            status = exc.response.status_code if exc.response is not None else None
            if status not in _EXPAND_FALLBACK_STATUS:
                raise
            LOG.warning(
                "Redfish expand rejected uri=%s status=%s; using sequential fallback",
                collection_uri,
                status,
            )
            collection = self.get(collection_uri)

        members = [member for member in collection.get("Members", []) if isinstance(member, dict)]
        if self._expanded(members):
            return members

        uris = [member.get("@odata.id") for member in members if member.get("@odata.id")]
        if uris:
            LOG.warning("Redfish expand was ignored uri=%s; following member links", collection_uri)
        return [self.get(uri) for uri in uris]
