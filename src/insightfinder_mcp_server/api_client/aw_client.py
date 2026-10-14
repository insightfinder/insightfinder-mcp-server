"""Thin client for the AI WatchTower (AW) external APIs.

AW takes the same user name + license key the MCP server already receives for InsightFinder:
/api/external/v1/* reads X-Api-Key (the license key is accepted), /if/* reads X-License-Key.
Both headers are sent so one client covers both prefixes.
"""
import json
from typing import Any, Dict, Optional
from urllib.parse import urlparse

import httpx

from ..config.settings import settings
from .client_factory import get_current_api_client


class AWAPIClient:
    def __init__(self, base_url: str, user_name: str, license_key: str):
        self.base_url = base_url.rstrip("/")
        self.user_name = user_name
        self.headers = {
            "X-User-Name": user_name,
            "X-Api-Key": license_key,
            "X-License-Key": license_key,
        }

    async def _get(self, path: str, params: Optional[Dict[str, Any]] = None,
                   timeout: float = 30.0) -> Dict[str, Any]:
        """GET -> {"status": "success", "data": ...} | {"status": "empty"} | {"status": "error", "message": ...}."""
        return await self._request("GET", path, timeout, params=params)

    async def _post(self, path: str, body: Dict[str, Any], timeout: float = 30.0) -> Dict[str, Any]:
        """POST a JSON body; same result contract as _get. Use timeout up to 120 for heavy calls."""
        return await self._request("POST", path, timeout, json=body)

    async def _request(self, method: str, path: str, timeout: float, **kw) -> Dict[str, Any]:
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.request(method, f"{self.base_url}{path}", headers=self.headers, **kw)
            resp.raise_for_status()  # before the empty check: a 502/504 with no body is an error, not "empty"
            if resp.status_code == 204 or not resp.content:
                return {"status": "empty"}
            data = resp.json()
            if isinstance(data, str):  # some AW endpoints return the JSON as a string
                data = json.loads(data)
            return {"status": "success", "data": data}
        except httpx.HTTPStatusError as e:
            out = {"status": "error", "http_status": e.response.status_code,
                   "message": f"AW API request failed: {e.response.status_code}"}
            try:  # AW's own errors are {"message": "..."}; keep it so tools can explain
                out["detail"] = str(e.response.json()["message"])[:300]
            except Exception:
                pass
            return out
        except httpx.TimeoutException:
            return {"status": "error", "message": "AW request timeout - API took too long to respond"}
        except httpx.RequestError as e:
            return {"status": "error", "message": f"AW network error: {e}"}
        except ValueError as e:  # body was not valid JSON
            return {"status": "error", "message": f"AW returned an unreadable response: {e}"}

    async def list_models(self):
        # Always the full list: AW's filtered /model endpoint only matches when name AND version are both
        # given (either alone returns []), so callers filter the list themselves.
        return await self._get("/api/external/v1/model/list", {"userName": self.user_name})

    async def list_monitors(self, monitor_name: Optional[str], monitor_type: Optional[str],
                            page: int, page_size: int):
        params = {"userName": self.user_name, "monitorName": monitor_name, "monitorType": monitor_type,
                  "pageNumber": page, "pageSize": page_size}
        return await self._get("/api/external/v1/monitor", {k: v for k, v in params.items() if v is not None})

    async def get_monitor(self, owner: str, monitor_name: str):
        # userName is the monitor's OWNER here (the caller is the X-User-Name header); 403 if the caller may not see it.
        return await self._get("/api/external/v1/monitor", {"userName": owner, "monitorName": monitor_name})

    async def get_summary(self, start_ms: int, end_ms: int):
        # start/end of 0 -> AW uses today (UTC). Slow: ~7 s for a day, ~30 s for a month.
        return await self._get("/if/mcp/summary",
                               {"userName": self.user_name, "startTime": start_ms, "endTime": end_ms},
                               timeout=120.0)

    # --- data quality (AW-2390). startTime/endTime are REAL UTC epoch ms; AW converts to the owner's timezone.
    DQ = "/api/external/v1/workbench/data-quality"

    @staticmethod
    def _dq_timeout(start_ms: int, end_ms: int) -> float:
        return 120.0 if end_ms - start_ms > 3 * 86_400_000 else 30.0  # long ranges are slow in AW/UIE

    async def get_data_quality(self, owner: str, monitor_name: str, start_ms: int, end_ms: int,
                               page_no: int = 1, page_size: int = 10, selected_date: Optional[int] = None,
                               fields=None, sub_types=None, groups=None, data_source: Optional[str] = None,
                               whole_days: bool = False):
        # Primitives are always explicit numbers (never null); pageNo is 1-based; type is unused by this endpoint.
        body: Dict[str, Any] = {"monitorName": monitor_name, "userName": owner, "startTime": int(start_ms),
                                "endTime": int(end_ms), "pageNo": int(page_no), "pageSize": int(page_size),
                                "type": 0, "fields": sorted(fields or []), "subTypes": sorted(sub_types or []),
                                "groups": sorted(groups or [])}
        if selected_date is not None:
            body["selectedDate"] = int(selected_date)
        if data_source:
            body["dataSource"] = data_source
        return await self._request("POST", self.DQ, self._dq_timeout(start_ms, end_ms), json=body,
                                   params={"wholeDays": "true"} if whole_days else None)

    async def get_data_quality_volume(self, owner: str, monitor_name: str, start_ms: int, end_ms: int,
                                      group: Optional[str] = None, data_source: Optional[str] = None,
                                      whole_days: bool = False):
        params = {"userName": owner, "monitorName": monitor_name, "startTime": int(start_ms),
                  "endTime": int(end_ms), "group": group, "dataSource": data_source,
                  "wholeDays": "true" if whole_days else None}
        return await self._get(f"{self.DQ}/volume", {k: v for k, v in params.items() if v},
                               self._dq_timeout(start_ms, end_ms))

    async def list_data_quality_monitors(self, scope: str = "organization"):
        # One big page; AW's order is random, so callers sort locally.
        return await self._get(f"{self.DQ}/monitors", {"scope": scope, "pageNumber": 0, "pageSize": 500})


# AW's public address for each InsightFinder host the MCP server can be pointed at. AW documents the
# same pairs in its own app.base-url setting (prod https://ai.insightfinder.com, stg https://ai-stg.insightfinder.com).
AW_URL_BY_UIE_HOST = {
    "app.insightfinder.com": "https://ai.insightfinder.com",
    "stg.insightfinder.com": "https://ai-stg.insightfinder.com",
}


def resolve_aw_url(uie_url: Optional[str]) -> str:
    """AW_API_URL when set (explicit override, e.g. local development), else AW's address for the
    InsightFinder host this request uses; empty when neither is known."""
    if settings.AW_API_URL:
        return settings.AW_API_URL
    return AW_URL_BY_UIE_HOST.get((urlparse(uie_url or "").hostname or "").lower(), "")


def get_current_aw_client() -> Optional[AWAPIClient]:
    """AW client for the current request, built from the InsightFinder credentials already in context."""
    api_client = get_current_api_client()
    if not api_client:
        return None
    aw_url = resolve_aw_url(api_client.base_url)
    return AWAPIClient(aw_url, api_client.user_name, api_client.license_key) if aw_url else None
