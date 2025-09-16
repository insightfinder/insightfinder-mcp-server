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

## 3. (Optional) Trace Server Configuration

You can enable distributed tracing of the client (and downstream tool calls) directly to an InsightFinder Trace Server (Traceloop based) without an OpenTelemetry Collector.

Add / adjust the following variables in your `.env` (they are already present in `env.example`):

```bash
# Direct Trace Server endpoint (default local dev port)
TRACE_SERVER_URL=http://127.0.0.1:4617

# Logical environment tag used in spans (e.g. dev | stg | prod)
ENVIRONMENT=prod

# InsightFinder auth for tracing (only needed if different from primary IF credentials)
TRACE_INSIGHTFINDER_USER_NAME=if_username
TRACE_INSIGHTFINDER_LICENSE_KEY=if_licensekey

# Observability metadata (helps organize traces in InsightFinder)
TRACE_INSIGHTFINDER_PROJECT=llm-chatbot-traces
TRACE_INSIGHTFINDER_SYSTEM_NAME=llm-client-system
```

Guidelines:
- Keep `TRACE_INSIGHTFINDER_PROJECT` unique per logical application or major feature.
- Use a stable `TRACE_INSIGHTFINDER_SYSTEM_NAME` so you can filter consistently.
- Set `ENVIRONMENT` to distinguish dev/stg/prod traces.
- If the Trace Server uses TLS with a self-signed cert, also set `VERIFY_SSL=true` and point `SSL_CERT_PATH` to the CA bundle.

After updating `.env`, restart the client. On successful configuration you should see trace export confirmation messages in debug mode (enable by setting `ENABLE_DEBUG_MESSAGES=true`).

