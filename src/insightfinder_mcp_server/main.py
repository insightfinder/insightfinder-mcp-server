import sys
import logging

# This import initializes the server and registers the tools.
# It's important that it comes before the server is run.
from insightfinder_mcp_server.server.server import mcp_server
from insightfinder_mcp_server.config.settings import settings

# Configure logging to suppress INFO messages from MCP library unless debug is enabled
if not settings.ENABLE_DEBUG_MESSAGES:
    # Suppress INFO and DEBUG messages from MCP and related libraries
    logging.getLogger("mcp").setLevel(logging.WARNING)
    logging.getLogger("mcp.server").setLevel(logging.WARNING)
    logging.getLogger("mcp.server.fastmcp").setLevel(logging.WARNING)
    logging.getLogger("fastmcp").setLevel(logging.WARNING)
    logging.getLogger("fastapi").setLevel(logging.WARNING)
    logging.getLogger("uvicorn").setLevel(logging.WARNING)
    
    # Set root logger to WARNING level to suppress general INFO messages
    logging.basicConfig(level=logging.WARNING, format='%(levelname)s: %(message)s')
else:
    # Enable debug logging when debug messages are enabled
    logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')

# Expose the server object for MCP CLI auto-discovery
server = mcp_server

def run():
    """
    Runs the MCP server using the stdio transport.
    """
    # Print a startup message to stderr only if debug is enabled
    if settings.ENABLE_DEBUG_MESSAGES:
        print(f"Starting {mcp_server.name} v{mcp_server.version} with stdio transport...", file=sys.stderr)
    
    # FastMCP's run method with 'stdio' transport will block and handle the communication loop.
    # Note: Ensure your mcp-sdk version supports this direct transport argument.
    # If not, running via 'mcp dev' is the recommended alternative.
    try:
        mcp_server.run(transport="stdio")
    except Exception as e:
        if settings.ENABLE_DEBUG_MESSAGES:
            print(f"Failed to run MCP server: {e}", file=sys.stderr)
        sys.exit(1)

if __name__ == "__main__":
    run()