"""On-call ARI investigation fields on incident timeline records.

The incident timeline (/api/v2/timeline with includeAriReport=true) gives each incident that ARI
investigated an `actionReportStatus` ("In Progress", "Awaiting Approval", "Completed", "Failed",
"Denied", "Skipped", "Not Configured") and, once Completed, an `actionReportDigest`:
{"overview", "confidence", "next_action", "actions": [{"type", "title", "url"}]}. Incidents ARI
never looked at carry neither. The full report text comes from get_incident_details.
"""
from typing import Any, Dict, Iterable, List, Optional

ARI_STATUS_FIELD = "actionReportStatus"
ARI_DIGEST_FIELD = "actionReportDigest"
COMPLETED = "Completed"


def ari_fields(incident: Dict[str, Any]) -> Dict[str, Any]:
    """{"ari_status", "ari_digest"} for one raw timeline record; {} when ARI never looked at it."""
    status = incident.get(ARI_STATUS_FIELD)
    if not status:
        return {}
    out: Dict[str, Any] = {"ari_status": status}
    digest = incident.get(ARI_DIGEST_FIELD)
    if isinstance(digest, dict) and digest.get("overview"):
        out["ari_digest"] = {k: digest[k] for k in ("overview", "confidence", "next_action", "actions")
                             if digest.get(k)}
    return out


def strip_ari_fields(record: Dict[str, Any]) -> None:
    """Remove the raw backend fields once they have been normalised by ari_fields."""
    record.pop(ARI_STATUS_FIELD, None)
    record.pop(ARI_DIGEST_FIELD, None)


def ari_status_counts(incidents: Iterable[Dict[str, Any]]) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for inc in incidents:
        status = inc.get(ARI_STATUS_FIELD)
        if status:
            counts[status] = counts.get(status, 0) + 1
    return counts


def _action_links(actions: Optional[List[Dict[str, Any]]]) -> str:
    links = []
    for a in actions or []:
        url, title = a.get("url"), a.get("title") or a.get("type") or "link"
        links.append(f"[{title}]({url})" if url else title)
    return ", ".join(links)


def render_ari_digest(digest: Dict[str, Any], indent: str = "  ") -> List[str]:
    """Markdown sub-bullets for a Completed investigation's digest (same wording as the UIE
    daily summary)."""
    conf = digest.get("confidence")
    head = f"**ARI investigation (Completed{', ' + conf + ' confidence' if conf else ''}):**"
    lines = [f"{indent}- {head} {digest.get('overview', '').strip()}"]
    links = _action_links(digest.get("actions"))
    if links:
        lines.append(f"{indent}- **Actions taken:** {links}")
    if digest.get("next_action"):
        lines.append(f"{indent}- **Next action:** {digest['next_action'].strip()}")
    return lines
