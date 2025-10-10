from mcp.server.fastmcp import FastMCP
from ..config.settings import settings

class CustomFastMCP(FastMCP):
    def __init__(self, *args, version=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.version = version

# Create a singleton instance of the FastMCP server
mcp_server = CustomFastMCP(
    name=settings.SERVER_NAME,
    version=settings.SERVER_VERSION
)

# Import tool definitions to ensure they are registered with the server instance
from .tools import incident_tools, log_anomaly_tools, metric_anomaly_tools, deployment_tools, trace_tools, get_time, jira_tools

# Import resources
from .resources import time_tool_resources