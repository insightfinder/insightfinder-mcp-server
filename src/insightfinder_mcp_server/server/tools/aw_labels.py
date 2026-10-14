"""
Words and number formats of the AI WatchTower (AW) Workbench, so chat answers read like the Workbench page (AW-2390).

Source: the Workbench data quality page. Chart heading "Anomalous Data / Total Data", tooltip rows
"Missing Data  562 / 7,507  7.49%", sections Data Integrity / Data Consistency / Schema Check, and the schema
check options of the monitor settings. UIE names the types (AW's `subTypeMap`: name -> id); the display names below
only rename id 1 ("Consistency" is shown as "Inconsistency"), everything else is shown as UIE names it.
"""

from typing import Dict, Iterable, List, Optional

SECTION_INTEGRITY = "Data Integrity"
SECTION_CONSISTENCY = "Data Consistency"
SECTION_SCHEMA = "Schema Check"
SECTIONS = (SECTION_INTEGRITY, SECTION_CONSISTENCY, SECTION_SCHEMA)

# What the Workbench calls the types when UIE's name differs from the page.
_DISPLAY = {"Consistency": "Inconsistency"}

# Type ids (UIE's subTypeMap): 1 consistency, 2-6 integrity, 7-9 schema check.
RULE_IDS = {"CONSISTENCY": 1, "MISSING_DATA": 2, "DATA_OUTLIERS": 3, "FIELD_TYPE_MISMATCH": 4,
            "DISQUALIFIED_DATA_RANGE": 5, "CUSTOM": 6, "FIELD_ORDER_MISMATCH": 7, "UNEXPECTED_FIELD": 8,
            "LOST_FIELD": 9}

# issue_type input: groups and single types, every spelling a user or the model may use (old names included).
ISSUE_TYPES: Dict[str, List[int]] = {
    "integrity": [2, 3, 4, 5, 6], "data integrity": [2, 3, 4, 5, 6],
    "consistency": [1], "inconsistency": [1], "data consistency": [1],
    "schema": [7, 8, 9], "schema check": [7, 8, 9],
    "qualified data range": [5], "qualified_data_range": [5],
}
for _name, _id in RULE_IDS.items():
    ISSUE_TYPES[_name.lower().replace("_", " ")] = [_id]
    ISSUE_TYPES[_name.lower()] = [_id]
ISSUE_TYPE_HELP = ("integrity, consistency, schema, or one type: Missing Data, Data Outliers, Field Type Mismatch, "
                   "Disqualified Data Range, Custom, Inconsistency, Field Order Mismatch, Unexpected Field, Lost Field")

# Schema check options of a monitor (monitorSetting.schemaCheck keys) with the Workbench labels.
SCHEMA_OPTIONS = {"fieldOrder": "Field order (Misordered)", "unexpectedFields": "Unexpected fields (Added)",
                  "lostFields": "Lost fields (reduced)"}


def issue_type_ids(text: str) -> Optional[List[int]]:
    return ISSUE_TYPES.get(" ".join(str(text).strip().lower().replace("_", " ").split()))


def display_name(type_name: str) -> str:
    return _DISPLAY.get(type_name, type_name)


def section_of_id(type_id: Optional[int], name: str = "") -> str:
    if type_id is None:  # a type UIE did not list in subTypeMap: only "Consistency" is known to be special
        return SECTION_CONSISTENCY if name == "Consistency" else SECTION_INTEGRITY
    if type_id == 1:
        return SECTION_CONSISTENCY
    return SECTION_SCHEMA if type_id in (7, 8, 9) else SECTION_INTEGRITY


def sections_by_type(sub_type_map: Optional[Dict[str, int]], names: Iterable[str]) -> Dict[str, str]:
    """type name -> section, using the ids UIE returned."""
    m = sub_type_map or {}
    return {n: section_of_id(m.get(n), n) for n in names}


def ordered_types(names: Iterable[str], sub_type_map: Optional[Dict[str, int]]) -> List[str]:
    """Type names in the Workbench order: by section, then by id (unknown ids last, by name)."""
    m = sub_type_map or {}
    sec = sections_by_type(m, names)
    return sorted(set(names), key=lambda n: (SECTIONS.index(sec[n]), m.get(n, 99), n))


def num(n) -> str:
    return f"{int(round(n)):,}"


def pct(n, total) -> str:
    """Percent with two decimals like the Workbench tooltip (0.[00]%); "-" when nothing was checked."""
    if not total:
        return "-"
    text = f"{n / total * 100:.2f}".rstrip("0").rstrip(".")
    return f"{text}%"


def ratio(n, total) -> str:
    """"562 / 7,507 (7.49%)"."""
    return f"{num(n)} / {num(total)} ({pct(n, total)})"


def signed(n) -> str:
    return f"{'+' if n >= 0 else '-'}{num(abs(n))}"


def signed_pct(fraction: float) -> str:
    text = f"{abs(fraction) * 100:.1f}%"
    return text if fraction == 0 else f"{'+' if fraction > 0 else '-'}{text}"
