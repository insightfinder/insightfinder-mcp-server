#!/bin/bash

# Ensure the script is run from the project root
cd "$(dirname "$0")/.."

# Activate virtual environment if it exists
if [ -d ".venv" ]; then
  source .venv/bin/activate
fi

# Set the Python path
export PYTHONPATH=./src

# Run the server using the mcp development tool
# This provides live reloading and connects to the MCP Inspector
echo "Starting InsightFinder MCP Server with 'mcp dev'..."
mcp dev src/insightfinder_mcp_server/main.py
