# Multi-LLM MCP Client

A clean, modern chatbot that supports multiple LLM providers with InsightFinder MCP Server integration via Server-Sent Events (SSE).
# Multi-LLM MCP Client: Quick Start

## 1. Configure Environment

Copy the example environment file to create your own `.env`:

```bash
cp env.example .env
```

Edit `.env` and provide your credentials:

- InsightFinder Credentials:
   - edit `if_envs.yaml` and add your InsightFinder accounts under `environments` (stg/prod) with `user_name` and `license_key`

- MCP Server:
   - `HTTP_API_KEY=your_mcp_server_api_key`

   - MCP Server URL:
      - By default, `MCP_SERVER_URL` is set to `https://mcp.insightfinder.com`.
      - You can obtain your `HTTP_API_KEY` from the DevOps team.

- LLM API Keys:
   - `OPENAI_API_KEY=your_openai_key` (for ChatGPT)
   - `ANTHROPIC_API_KEY=your_anthropic_key` (for Claude)
   - `GOOGLE_API_KEY=your_google_key` (for Gemini)
   - `DEEPSEEK_API_KEY=your_deepseek_key` (for DeepSeek)

## 2. Run the Client

First, make the script executable (only needed once):

```bash
chmod +x run_client.sh
```

Then run the client from the project root:

```bash
./run_client.sh
```

The script will set up a virtual environment, install dependencies (if needed), and launch the chat client.

LLM-specific packages are optional and only needed for the providers you want to use.
