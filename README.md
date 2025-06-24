# InsightFinder MCP Server

This project provides a Model Context Protocol (MCP) server that allows Large Language Models (LLMs) to fetch incident data from the InsightFinder platform by acting as a tool.

## Features

-   **`fetch_incidents` Tool**: Enables LLMs to retrieve incident data based on parameters like `systemName`, `startTime`, etc.
-   **`stdio` Transport**: Communicates with MCP clients via standard I/O.
-   **Configuration via Environment Variables**: Securely manage API credentials.

## Setup

1.  **Clone the repository:**
    ```bash
    git clone <your-repo-url>
    cd insightfinder-mcp-server
    ```

2.  **Create and activate a virtual environment:**
    ```bash
    python -m venv .venv
    source .venv/bin/activate
    ```

3.  **Install dependencies:**
    ```bash
    pip install -e .
    ```

4.  **Configure environment variables:**
    Create a `.env` file in the project root and add your InsightFinder credentials:
    ```
    INSIGHTFINDER_API_URL="[https://app.insightfinder.com](https://app.insightfinder.com)"
    INSIGHTFINDER_JWT_TOKEN="your_jwt_token_here"
    INSIGHTFINDER_SYSTEM_NAME="system_name"
    INSIGHTFINDER_USER_NAME="user"
    ```

## Running the Server

### Using Python (Local Development)

Use the provided script to run the server:

```bash
./scripts/run_server.sh
```

### Using Docker

You can run the MCP server using Docker without needing to install Python or dependencies locally:

```bash
docker run -i --rm \
  -e INSIGHTFINDER_API_URL=your_api_url \
  -e INSIGHTFINDER_JWT_TOKEN=your_jwt_token \
  -e INSIGHTFINDER_SYSTEM_NAME=your_system_name \
  -e INSIGHTFINDER_USER_NAME=your_user_name \
  docker.io/insightfinder/insightfinder-mcp-server:latest
```

**Environment Variables:**
- `INSIGHTFINDER_API_URL`: Your InsightFinder API endpoint (e.g., `https://app.insightfinder.com`)
- `INSIGHTFINDER_JWT_TOKEN`: Your JWT authentication token
- `INSIGHTFINDER_SYSTEM_NAME`: The system name to query incidents for
- `INSIGHTFINDER_USER_NAME`: Your InsightFinder username

**MCP Client Configuration:**
When configuring your MCP client (like Claude Desktop), use the following configuration:

```json
{
  "mcpServers": {
    "insightfinder": {
      "command": "docker",
      "args": [
        "run",
        "-i",
        "--rm",
        "-e", "INSIGHTFINDER_API_URL=your_api_url",
        "-e", "INSIGHTFINDER_JWT_TOKEN=your_jwt_token",
        "-e", "INSIGHTFINDER_SYSTEM_NAME=your_system_name",
        "-e", "INSIGHTFINDER_USER_NAME=your_user_name",
        "docker.io/insightfinder/insightfinder-mcp-server:latest"
      ]
    }
  }
}
```

Replace the environment variable values with your actual InsightFinder credentials and configuration.