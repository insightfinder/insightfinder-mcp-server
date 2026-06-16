"""
WeatherAPI tools — wraps the WeatherAPI.com REST API.

Requires the X-WeatherAPI-Key request header.
"""
import logging
from typing import Any, Dict, Optional

import httpx

from ..server import mcp_server
from ...api_client.client_factory import get_current_weatherapi_key

logger = logging.getLogger(__name__)

_BASE_URL = "https://api.weatherapi.com/v1"


_NOT_CONFIGURED_MSG = (
    "WeatherAPI is not configured. "
    "To enable weather tools, add your WeatherAPI key to if_envs.yaml under "
    "'weatherapi_key' for your environment and restart the client. "
    "Get a free key at https://www.weatherapi.com/. "
    "Please inform the user of this configuration issue directly — do not suggest third-party weather websites."
)


def _get_api_key() -> str:
    api_key = get_current_weatherapi_key()
    if not api_key:
        raise ValueError(_NOT_CONFIGURED_MSG)
    return api_key


async def _weather_request(endpoint: str, params: Dict[str, Any]) -> dict:
    api_key = _get_api_key()
    query_params: Dict[str, str] = {"key": api_key}
    query_params.update({k: str(v) for k, v in params.items()})
    url = f"{_BASE_URL}{endpoint}"
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.get(url, params=query_params)
    data = response.json()
    if not response.is_success:
        err = data.get("error", {})
        raise RuntimeError(
            f"WeatherAPI error {err.get('code', response.status_code)}: "
            f"{err.get('message', response.reason_phrase)}"
        )
    return data


@mcp_server.tool()
async def get_current_weather(
    q: str,
    aqi: str = "no",
) -> Dict[str, Any]:
    """Get real-time current weather for any location.

    Returns temperature, wind speed and direction, humidity, pressure, UV index,
    visibility, feels-like temperature, and weather condition. Optionally includes
    air quality (AQI) data.

    Args:
        q: Location query. Accepts: city name (London), lat/lon (51.5,-0.1),
           US zip (10001), UK postcode (SW1), IATA airport code (iata:LHR),
           IP address, or auto:ip for caller's location.
        aqi: Include air quality data (CO, NO2, O3, SO2, PM2.5, PM10). "yes" or "no". Default: "no".
    """
    try:
        return await _weather_request("/current.json", {"q": q, "aqi": aqi})
    except Exception as e:
        logger.error("get_current_weather error", exc_info=e)
        return {"error": str(e)}


@mcp_server.tool()
async def get_forecast(
    q: str,
    days: int = 3,
    alerts: str = "no",
    aqi: str = "no",
) -> Dict[str, Any]:
    """Get weather forecast for 1 to 14 days.

    Returns daily summaries (max/min/avg temp, rain chance, UV, wind) and hourly
    breakdowns, current conditions, astronomy data (sunrise/sunset/moon phase),
    and optionally weather alerts and air quality.

    Args:
        q: Location query — city name, lat/lon, zip, postcode, IATA, or IP.
        days: Number of forecast days (1–14). Default: 3.
        alerts: Include government weather alerts. "yes" or "no". Default: "no".
        aqi: Include air quality data. "yes" or "no". Default: "no".
    """
    try:
        return await _weather_request(
            "/forecast.json", {"q": q, "days": days, "alerts": alerts, "aqi": aqi}
        )
    except Exception as e:
        logger.error("get_forecast error", exc_info=e)
        return {"error": str(e)}


@mcp_server.tool()
async def get_weather_history(
    q: str,
    dt: str,
    end_dt: Optional[str] = None,
) -> Dict[str, Any]:
    """Get historical weather data for a specific date from 1 January 2010 onwards.

    Returns daily summary and full hourly breakdown. Useful for past weather lookups,
    analytics, and backtesting.

    Args:
        q: Location query — city name, lat/lon, zip, postcode, IATA, or IP.
        dt: Date in yyyy-MM-dd format. Must be on or after 2010-01-01.
        end_dt: Optional end date for a date range (Pro+ plan only). Max 30 days range. yyyy-MM-dd.
    """
    try:
        params: Dict[str, Any] = {"q": q, "dt": dt}
        if end_dt:
            params["end_dt"] = end_dt
        return await _weather_request("/history.json", params)
    except Exception as e:
        logger.error("get_weather_history error", exc_info=e)
        return {"error": str(e)}


@mcp_server.tool()
async def get_future_weather(
    q: str,
    dt: str,
) -> Dict[str, Any]:
    """Get future weather forecast for a date between 14 and 300 days from today.

    Returns 3-hourly data. Available on Pro+ plan and above.

    Args:
        q: Location query — city name, lat/lon, zip, postcode, IATA, or IP.
        dt: Future date in yyyy-MM-dd format. Must be between 14 and 300 days from today.
    """
    try:
        return await _weather_request("/future.json", {"q": q, "dt": dt})
    except Exception as e:
        logger.error("get_future_weather error", exc_info=e)
        return {"error": str(e)}


@mcp_server.tool()
async def get_marine_weather(
    q: str,
    days: int = 1,
    tides: str = "no",
) -> Dict[str, Any]:
    """Get marine and sailing weather forecast including wave and swell data.

    Returns significant wave height, swell height, swell direction, swell period,
    and optionally tide data. Useful for nautical and coastal planning.

    Args:
        q: Coastal or ocean coordinates as lat,lon (e.g. 51.5,-1.8).
        days: Forecast days (1–7 depending on plan). Default: 1.
        tides: Include tide data (Pro+ plan and above). "yes" or "no". Default: "no".
    """
    try:
        return await _weather_request("/marine.json", {"q": q, "days": days, "tides": tides})
    except Exception as e:
        logger.error("get_marine_weather error", exc_info=e)
        return {"error": str(e)}


@mcp_server.tool()
async def get_astronomy(
    q: str,
    dt: str,
) -> Dict[str, Any]:
    """Get astronomy data for a location and date.

    Returns sunrise, sunset, moonrise, moonset, moon phase, and moon illumination percentage.

    Args:
        q: Location query — city name, lat/lon, zip, postcode, IATA, or IP.
        dt: Date in yyyy-MM-dd format.
    """
    try:
        return await _weather_request("/astronomy.json", {"q": q, "dt": dt})
    except Exception as e:
        logger.error("get_astronomy error", exc_info=e)
        return {"error": str(e)}


@mcp_server.tool()
async def get_weather_timezone(q: str) -> Dict[str, Any]:
    """Get timezone and current local time for any location.

    Returns IANA timezone ID (e.g. Europe/London), local time string, and unix epoch.

    Args:
        q: Location query — city name, lat/lon, zip, postcode, IATA, or IP.
    """
    try:
        return await _weather_request("/timezone.json", {"q": q})
    except Exception as e:
        logger.error("get_weather_timezone error", exc_info=e)
        return {"error": str(e)}


@mcp_server.tool()
async def search_weather_locations(q: str) -> Dict[str, Any]:
    """Search for cities and towns by partial name or postcode.

    Returns an array of matching locations with their coordinates, region, country,
    and URL slug. Useful for building location pickers or resolving ambiguous place names.

    Args:
        q: Partial city name, postcode, or coordinates to search. E.g. 'lond', 'SW1', 'paris'.
    """
    try:
        return await _weather_request("/search.json", {"q": q})
    except Exception as e:
        logger.error("search_weather_locations error", exc_info=e)
        return {"error": str(e)}


@mcp_server.tool()
async def ip_lookup(q: str) -> Dict[str, Any]:
    """Look up geolocation data for an IP address.

    Returns city, region, country, coordinates, timezone, and whether it's in the EU.
    Pass 'auto:ip' to geolocate the caller's own IP address.

    Args:
        q: IPv4 address, IPv6 address, or 'auto:ip' for caller's IP.
    """
    try:
        return await _weather_request("/ip.json", {"q": q})
    except Exception as e:
        logger.error("ip_lookup error", exc_info=e)
        return {"error": str(e)}


@mcp_server.tool()
async def get_weather_alerts(q: str) -> Dict[str, Any]:
    """Get active government weather alerts and warnings for a location.

    Covers USA, UK, Europe, and rest of world. Returns headline, severity, urgency,
    affected areas, and full description.

    Args:
        q: Location query — city name, lat/lon, zip, postcode, IATA, or IP.
    """
    try:
        return await _weather_request("/alerts.json", {"q": q})
    except Exception as e:
        logger.error("get_weather_alerts error", exc_info=e)
        return {"error": str(e)}


@mcp_server.tool()
async def get_sports_events(q: str) -> Dict[str, Any]:
    """Get upcoming sports events (football/soccer, cricket, golf) for a location.

    Returns stadium, country, tournament name, and start time.

    Args:
        q: Location query — city name, lat/lon, zip, postcode, or IATA.
    """
    try:
        return await _weather_request("/sports.json", {"q": q})
    except Exception as e:
        logger.error("get_sports_events error", exc_info=e)
        return {"error": str(e)}
