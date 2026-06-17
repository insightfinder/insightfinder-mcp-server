"""
Downdetector tools — check real-time service outage data from downdetector.com.

Uses a headless Chromium browser (Playwright) to bypass Cloudflare protection.
Install Playwright browsers with: playwright install chromium
"""
import json
import logging
import pathlib
import re
from typing import Any, Dict, Optional

from ..server import mcp_server

logger = logging.getLogger(__name__)

_SLUGS_FILE = pathlib.Path(__file__).parent / "downdetector_slugs.json"


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _load_slug_entries() -> list:
    try:
        return json.loads(_SLUGS_FILE.read_text())
    except Exception as e:
        logger.error("Failed to load downdetector_slugs.json: %s", e)
        return []


def _extract_chart_data(html: str) -> list:
    """Extract chartData.dataPoints from the Downdetector page HTML.

    Downdetector embeds its data as double-escaped JSON inside the Next.js RSC
    payload. We extract each data point with a regex sweep.
    """
    point_re = re.compile(
        r'\\"timestampUtc\\":\\"([^"\\]+)\\"'
        r'.*?'
        r'\\"reportsValue\\":(\d+)'
        r'.*?'
        r'\\"baselineValue\\":(\d+)',
        re.DOTALL,
    )
    return [
        {"date": m.group(1), "reports": int(m.group(2)), "baseline": int(m.group(3))}
        for m in point_re.finditer(html)
    ]


def _classify_status(current: float, baseline: Optional[float]) -> str:
    """Classify service status based on current reports vs baseline.

    | Status       | Condition                              |
    |--------------|----------------------------------------|
    | operational  | Reports at or near baseline (<2×)      |
    | degraded     | Reports 2×–10× above baseline          |
    | major_outage | Reports 10× or more above baseline     |
    | unknown      | No chart data (Cloudflare-blocked)     |
    """
    if baseline is None or baseline == 0:
        if current > 500:
            return "major_outage"
        if current > 50:
            return "degraded"
        return "operational"
    ratio = current / baseline
    if ratio >= 10.0:
        return "major_outage"
    if ratio >= 2.0:
        return "degraded"
    return "operational"


async def _fetch_with_browser(url: str) -> tuple:
    """Launch a headless Chromium browser and return (html, status_code).

    Uses stealth flags and waits for the Cloudflare JS challenge to resolve
    before reading the page content. Cloudflare serves a challenge page first
    (HTTP 200) from datacenter IPs; waiting for networkidle + chartData
    ensures we get the real content after the JS redirect completes.
    """
    from playwright.async_api import async_playwright

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-blink-features=AutomationControlled",
                "--disable-dev-shm-usage",
                "--disable-gpu",
            ],
        )
        context = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1280, "height": 800},
            # Mask common headless-browser fingerprints
            java_script_enabled=True,
            bypass_csp=True,
            extra_http_headers={
                "Accept-Language": "en-US,en;q=0.9",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            },
        )
        # Hide navigator.webdriver flag
        await context.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
        )
        page = await context.new_page()

        # Use 'load' so we wait past the Cloudflare JS challenge redirect
        response = await page.goto(url, wait_until="load", timeout=45_000)
        status = response.status if response else 0

        # Wait for the Next.js RSC payload (contains chartData) — up to 20s
        try:
            await page.wait_for_function(
                "() => document.body.innerHTML.includes('chartData')",
                timeout=20_000,
            )
        except Exception:
            # If chartData never appears, try waiting for networkidle as a last resort
            try:
                await page.wait_for_load_state("networkidle", timeout=10_000)
            except Exception:
                pass

        html = await page.content()
        await browser.close()
    return html, status


# ---------------------------------------------------------------------------
# MCP Tools
# ---------------------------------------------------------------------------

@mcp_server.tool()
async def downdetector(
    service_name: str,
    domain: str = "com",
) -> Dict[str, Any]:
    """Check current status and outage reports for a service from Downdetector.

    Returns current report count, baseline, status classification, and the last
    10 data points. Uses a real headless browser to bypass Cloudflare protection.

    Status is derived by comparing the current report count to the baseline:
      - operational  : reports < 2× baseline — no significant issues
      - degraded     : reports 2×–10× baseline — possible issues
      - major_outage : reports ≥ 10× baseline — significant outage
      - unknown      : page returned no chart data (likely Cloudflare-blocked)

    If you don't know the exact slug, call downdetector_search_slug first to find it.

    Args:
        service_name: The service slug as it appears on Downdetector
                      (e.g. 'steam', 'netflix', 'twitter', 'claude-ai', 'discord', 'github').
        domain: Country-code domain suffix (e.g. 'com', 'co.uk', 'fr', 'de', 'it').
                Defaults to 'com'. Try a country domain if .com is rate-limited.
    """
    service_name = service_name.strip().lower()
    domain = domain.strip().lower().lstrip(".")
    url = f"https://downdetector.{domain}/status/{service_name}/"
    logger.info("Downdetector fetch: %s", url)

    try:
        html, status_code = await _fetch_with_browser(url)
    except ImportError:
        return {
            "error": (
                "Playwright is not installed. Run: pip install playwright && playwright install chromium"
            )
        }
    except Exception as e:
        logger.error("downdetector fetch error: %s", e)
        return {"error": str(e)}

    if status_code in (403, 429):
        return {
            "error": (
                f"Blocked by Cloudflare for '{service_name}' on downdetector.{domain}. "
                "Try a country-specific domain (e.g. domain='co.uk')."
            )
        }

    if status_code == 404:
        return {
            "error": (
                f"Service slug '{service_name}' was not found on downdetector.{domain}. "
                "Call downdetector_search_slug to find the correct slug."
            )
        }

    if status_code not in (0, 200):
        return {"error": f"Unexpected HTTP {status_code} from downdetector.{domain}"}

    data_points = _extract_chart_data(html)

    if not data_points:
        return {
            "service": service_name,
            "domain": domain,
            "url": url,
            "status": "unknown",
            "current_reports": None,
            "baseline": None,
            "last_updated": None,
            "recent_reports": [],
            "summary": (
                f"No chart data found for '{service_name}'. "
                "The page may still be Cloudflare-protected or the service slug may be wrong."
            ),
        }

    latest = data_points[-1]
    current_count = latest["reports"]
    baseline_value = latest["baseline"]
    status = _classify_status(current_count, baseline_value)

    status_labels = {
        "operational": "Operational",
        "degraded": "Degraded / Possible Issues",
        "major_outage": "Major Outage",
    }
    summary_lines = [
        f"{service_name.upper()} — {status_labels[status]}",
        f"Current reports : {current_count}",
        f"Baseline        : {baseline_value if baseline_value is not None else 'N/A'}",
        f"Last updated    : {latest['date']}",
        f"Source          : {url}",
    ]

    return {
        "service": service_name,
        "domain": domain,
        "url": url,
        "status": status,
        "current_reports": current_count,
        "baseline": baseline_value,
        "last_updated": latest["date"],
        "recent_reports": [
            {"date": p["date"], "value": p["reports"]} for p in data_points[-10:]
        ],
        "summary": "\n".join(summary_lines),
    }


@mcp_server.tool()
async def downdetector_search_slug(
    query: str,
    max_results: int = 5,
) -> Dict[str, Any]:
    """Search the Downdetector service list to find the correct slug for a service.

    Use this when the downdetector tool returns a 404 (slug not found).
    Returns matching service names and their slugs.

    Args:
        query: Service name or partial slug to search for
               (e.g. 'aws', 'amazon', 'azure', 'google cloud').
        max_results: Maximum number of results to return (default 5).
    """
    entries = _load_slug_entries()
    if not entries:
        return {"error": "Slug list unavailable."}

    q = query.lower().strip()
    matches = []
    for entry in entries:
        if q in entry["name"].lower() or q in entry["slug"].lower():
            matches.append({"name": entry["name"], "slug": entry["slug"]})
        if len(matches) >= max_results:
            break

    if not matches:
        return {
            "query": query,
            "matches": [],
            "summary": f"No services found matching '{query}'. Try a different keyword.",
        }

    summary_lines = [f"Found {len(matches)} match(es) for '{query}':"]
    for m in matches:
        summary_lines.append(f"  {m['name']} → slug: \"{m['slug']}\"")

    return {
        "query": query,
        "matches": matches,
        "summary": "\n".join(summary_lines),
    }
