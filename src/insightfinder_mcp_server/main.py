import sys

# This import initializes the server and registers the tools.
# It's important that it comes before the server is run.
from insightfinder_mcp_server.server.server import mcp_server

# Expose the server object for MCP CLI auto-discovery
server = mcp_server

def run():
    """
    Runs the MCP server using the stdio transport.
    """
    # Print a startup message to stderr, which won't interfere with the MCP protocol on stdout
    print(f"Starting {mcp_server.name} v{mcp_server.version} with stdio transport...", file=sys.stderr)
    
    # FastMCP's run method with 'stdio' transport will block and handle the communication loop.
    # Note: Ensure your mcp-sdk version supports this direct transport argument.
    # If not, running via 'mcp dev' is the recommended alternative.
    try:
        mcp_server.run(transport="stdio")
    except Exception as e:
        print(f"Failed to run MCP server: {e}", file=sys.stderr)
        sys.exit(1)

if __name__ == "__main__":
    run()