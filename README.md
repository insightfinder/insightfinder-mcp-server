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

Use the provided script to run the server:

```bash
./scripts/run_server.sh
```