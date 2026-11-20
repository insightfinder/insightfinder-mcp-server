import pytest

from insightfinder_mcp_server.server.tools import aw_tools


@pytest.fixture(autouse=True)
def _fresh_monitor_cache():
    aw_tools.clear_monitor_cache()  # the monitor list is cached for a minute; every test starts without it
    yield
