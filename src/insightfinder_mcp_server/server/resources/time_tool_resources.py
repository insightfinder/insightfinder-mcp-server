"""
Time Tools Resources for MCP Server.

Provides clear guidance to LLMs on how to correctly use time-related tools
for querying data with proper date/time handling.
"""

from ..server import mcp_server

@mcp_server.resource("time-tools://usage-guide")
def get_time_tools_usage_guide() -> str:
    """Essential guide for using time tools correctly - read this FIRST."""
    return """# TIME TOOLS USAGE GUIDE

## 🎯 CRITICAL RULE
**ALWAYS call get_current_datetime() FIRST** to establish date context, then calculate your time ranges.

## ⚠️ COMMON MISTAKES TO AVOID

### ❌ WRONG: "Show yesterday's incidents"
```
get_time_range(hours_back=24)  # Gets LAST 24 HOURS, not yesterday's FULL DAY
```

### ✅ CORRECT: "Show yesterday's incidents"
```
1. get_current_datetime() → Get current date (e.g., "2026-10-06")
2. Calculate: yesterday = 2026-10-05
3. get_date_range_utc("2026-10-05") → Returns full day in milliseconds
4. Use returned start_time_ms and end_time_ms for query
```

### ❌ WRONG: "Compare last month vs this month"
```
get_date_range_utc("2026-09-01")  # Only gets FIRST DAY of Sept
get_date_range_utc("2026-10-01")  # Only gets FIRST DAY of Oct
```

### ✅ CORRECT: "Compare last month vs this month"
```
1. get_current_datetime() → "2026-10-06"
2. Calculate ranges:
   - This month: Oct 1 00:00:00 to Oct 6 23:59:59 (or current time)
   - Last month: Sept 1 00:00:00 to Sept 30 23:59:59
3. Compute milliseconds (13 digits):
   - this_start_ms = timestamp of "Oct 1 00:00:00 UTC" * 1000
   - this_end_ms = current_time_ms (from step 1)
   - last_start_ms = timestamp of "Sept 1 00:00:00 UTC" * 1000  
   - last_end_ms = timestamp of "Sept 30 23:59:59 UTC" * 1000
4. Query 1: fetch_*(this_start_ms, this_end_ms)
5. Query 2: fetch_*(last_start_ms, last_end_ms)
```

## 📋 TOOL SELECTION

| Query Type | Correct Tool | Notes |
|------------|--------------|-------|
| "Yesterday" / "Last Monday" / Specific dates | get_date_range_utc(date) | After getting current date |
| "Last N hours" | get_time_range(N) | For rolling time windows only |
| "Today" | get_date_range_utc(today_date) | After getting current date |
| Month/week comparisons | Manual calculation | Get current date first, then compute full ranges |

## 🔧 TOOLS REFERENCE

### get_current_datetime()
- **No parameters**
- **Returns:** Current UTC time + relative timestamps in milliseconds
- **Use:** ALWAYS call this FIRST

### get_date_range_utc(date_input: str)  
- **Input:** "2026-10-05" or "Oct 5, 2026" or "10/05/2026"
- **Returns:** Full day (00:00:00 to 23:59:59.999) in milliseconds
- **Use:** For complete calendar days

### get_time_range(hours_back: int)
- **Input:** Number of hours (e.g., 6, 12, 24)
- **Returns:** Rolling window from N hours ago to now in milliseconds
- **Use:** ONLY for hour-based rolling windows

## ⚡ QUICK EXAMPLES

**"Show me today's incidents"**
```
1. get_current_datetime() → "2026-10-06", current_time_ms: 1791244800000
2. get_date_range_utc("2026-10-06") → {start_time_ms, end_time_ms}
3. fetch_incidents(start_time_ms, end_time_ms)
```

**"Last 6 hours of data"**
```
1. get_time_range(6) → {start_time_ms, end_time_ms}
2. fetch_*(start_time_ms, end_time_ms)
```

**"This week vs last week"**
```
1. get_current_datetime() → "2026-10-06"
2. This week: Oct 1 00:00 to Oct 6 23:59 (in ms)
3. Last week: Sept 24 00:00 to Sept 30 23:59 (in ms)
4. Two separate queries with respective ranges
```

## ✅ REQUIREMENTS
- Timestamps MUST be 13-digit milliseconds (not 10-digit seconds)
- All times are UTC
- For comparisons: query FULL periods, not just first/last day
- Month-to-date: from 1st of month to current date/time
"""

@mcp_server.resource("time-tools://examples")  
def get_time_tools_examples() -> str:
    """Detailed step-by-step examples for common time queries."""
    return """# TIME TOOLS - STEP-BY-STEP EXAMPLES

## Example 1: "Show me yesterday's incidents"

**Step 1:** Get current context
```
get_current_datetime()
```
Returns:
```json
{
  "date_only": "2026-10-06",
  "current_time_milliseconds": 1791244800000
}
```

**Step 2:** Calculate yesterday = 2026-10-05

**Step 3:** Get full day range
```
get_date_range_utc("2026-10-05")
```
Returns:
```json
{
  "start_time": {"milliseconds": 1791158400000},
  "end_time": {"milliseconds": 1791244799999}
}
```

**Step 4:** Query with full day range
```
fetch_incidents(startTime=1791158400000, endTime=1791244799999)
```

## Example 2: "Compare last month with this month"

**Step 1:** Get current context
```
get_current_datetime()
```
Returns:
```json
{
  "date_only": "2026-10-06",
  "current_time_milliseconds": 1791244800000
}
```

**Step 2:** Calculate time ranges
- This month (Oct): Oct 1 00:00:00 to Oct 6 23:59:59 (or current time)
- Last month (Sept): Sept 1 00:00:00 to Sept 30 23:59:59

**Step 3:** Convert to milliseconds (13 digits)
```python
# This month (October 2026)
this_start_ms = 1790812800000  # Oct 1, 2026 00:00:00 UTC
this_end_ms = 1791244800000    # Current time from step 1

# Last month (September 2026)  
last_start_ms = 1788220800000  # Sept 1, 2026 00:00:00 UTC
last_end_ms = 1790812799000    # Sept 30, 2026 23:59:59 UTC
```

**Step 4:** Query BOTH full periods
```
Query 1: fetch_incidents(startTime=1790812800000, endTime=1791244800000)
Query 2: fetch_incidents(startTime=1788220800000, endTime=1790812799000)
```

**Step 5:** Compare the results

## Example 3: "Show me today's incidents"

**Step 1:** Get current context
```
get_current_datetime()
```
Returns:
```json
{
  "date_only": "2026-10-06",
  "current_time_milliseconds": 1791244800000
}
```

**Step 2:** Get today's full day range
```
get_date_range_utc("2026-10-06")
```
Returns:
```json
{
  "start_time": {"milliseconds": 1791244800000},
  "end_time": {"milliseconds": 1791331199999}
}
```

**Step 3:** Query
```
fetch_incidents(startTime=1791244800000, endTime=1791331199999)
```

## Example 4: "Last 6 hours of metric anomalies"

**Step 1:** Get time range directly
```
get_time_range(hours_back=6)
```
Returns:
```json
{
  "start_time": {"milliseconds": 1791223200000},
  "end_time": {"milliseconds": 1791244800000}
}
```

**Step 2:** Query
```
fetch_metric_anomalies(startTime=1791223200000, endTime=1791244800000)
```

## 🔑 KEY REMINDERS
- ✅ Use get_date_range_utc() for calendar days
- ✅ Use get_time_range() for rolling hours
- ✅ Always query FULL periods for comparisons
- ✅ Timestamps are 13-digit milliseconds
- ❌ Never use get_time_range(24) for "yesterday"
"""

@mcp_server.resource("time-tools://calculation-helpers")
def get_time_calculation_helpers() -> str:
    """Python code helpers for time calculations."""
    return """# TIME CALCULATION HELPERS

## Convert to 13-Digit Milliseconds
```python
# Seconds to milliseconds
timestamp_ms = timestamp_seconds * 1000

# Python datetime to milliseconds
from datetime import datetime, timezone
dt = datetime(2026, 10, 6, 0, 0, 0, tzinfo=timezone.utc)
timestamp_ms = int(dt.timestamp()) * 1000
```

## Calculate Yesterday
```python
from datetime import datetime, timedelta, timezone

current = datetime.now(timezone.utc)
yesterday = current - timedelta(days=1)
yesterday_str = yesterday.strftime('%Y-%m-%d')  # "2026-10-05"
```

## Calculate Month Ranges
```python
from datetime import datetime, timezone
import calendar

current = datetime.now(timezone.utc)  # 2026-10-06

# This month: Oct 1 00:00 to Oct 6 (current)
this_month_start = current.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
this_month_end = current

# Last month: Sept 1 00:00 to Sept 30 23:59:59
last_month_end = this_month_start - timedelta(days=1)  # Sept 30 23:59:59
last_month_start = last_month_end.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
last_month_end = last_month_end.replace(hour=23, minute=59, second=59, microsecond=999000)

# Convert to milliseconds
this_start_ms = int(this_month_start.timestamp()) * 1000
this_end_ms = int(this_month_end.timestamp()) * 1000
last_start_ms = int(last_month_start.timestamp()) * 1000
last_end_ms = int(last_month_end.timestamp()) * 1000
```

## Date Formats Supported by get_date_range_utc()
- "2026-10-06" (ISO)
- "10/06/2026" (US format)
- "Oct 6, 2026" (Written)
- "October 6, 2026" (Full written)
"""