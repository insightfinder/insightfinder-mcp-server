# Multi-LLM MCP Client

A clean, modern chatbot that supports multiple LLM providers with InsightFinder MCP Server integration via Server-Sent Events (SSE).

## 🚀 Supported LLM Providers

- **OpenAI (ChatGPT)**: GPT-4o, GPT-4-turbo, GPT-3.5-turbo
- **Anthropic (Claude)**: Claude-3.5-Sonnet, Claude-3-Opus, Claude-3-Haiku
- **Google (Gemini)**: Gemini-2.0-Flash, Gemini-1.5-Pro, Gemini-1.5-Flash
- **Ollama (Llama)**: Local Llama models
- **DeepSeek**: DeepSeek-Chat, DeepSeek-Coder

## 🔧 Quick Setup

1. **Copy environment file**:
   ```bash
   cp client/.env.example client/.env
   ```

2. **Edit `.env` and add your API keys**:
   ```bash
   # Required: At least one LLM API key
   OPENAI_API_KEY=your_openai_key
   ANTHROPIC_API_KEY=your_anthropic_key  
   GOOGLE_API_KEY=your_google_key
   DEEPSEEK_API_KEY=your_deepseek_key
   
   # Optional: MCP Server settings
   MCP_SERVER_URL=http://127.0.0.1:8000
   HTTP_API_KEY=your_mcp_server_key
   
   # Optional: InsightFinder credentials
   INSIGHTFINDER_LICENSE_KEY=your_if_key
   INSIGHTFINDER_USER_NAME=your_username
   ```

3. **Install dependencies**:
   ```bash
   # Core dependencies
   pip install -r client/requirements-clean.txt
   
   # Optional: Install specific LLM providers you want to use
   pip install langchain-anthropic    # For Claude
   pip install langchain-google-genai # For Gemini
   pip install langchain-ollama       # For Llama
   ```

4. **For Ollama (optional)**:
   ```bash
   # Install and start Ollama
   ollama serve
   ollama pull llama3.1
   ```

5. **Start MCP Server**:
   ```bash
   TRANSPORT_TYPE=http SSE_ENABLED=true python -m insightfinder_mcp_server.main
   ```

6. **Run the client**:
   ```bash
   python client/sse_main_clean.py
   ```

## 💬 Usage

The client provides an interactive chat interface:

1. **Select LLM**: Choose your preferred LLM provider and model
2. **Chat**: Natural language conversation with MCP tool integration
3. **Commands**:
   - `help` - Show available commands
   - `tools` - List MCP tools
   - `clear` - Clear chat history
   - `progress` - Toggle progress updates
   - `exit` - Quit

## 🎯 Features

- **Multi-LLM Support**: Switch between different AI providers
- **SSE Streaming**: Real-time tool execution with progress updates
- **MCP Integration**: Access to InsightFinder tools (incidents, anomalies, traces, etc.)
- **Clean Interface**: Intuitive command-line chat experience
- **Environment-based Config**: Easy setup with `.env` files

## 🔍 Example

```
🚀 Multi-LLM MCP Streaming Chatbot
==================================================
🤖 Available LLM Providers:
  1. Chatgpt: gpt-4o, gpt-4-turbo (+1 more)
  2. Claude: claude-3-5-sonnet-20241022, claude-3-opus-20240229 (+1 more)

Select LLM provider (1-5 or name): 1
✓ Selected: Chatgpt (gpt-4o)

🔧 Initializing agent...
✅ Server supports SSE streaming
✓ Loaded 8 MCP tools
✓ Agent ready!

💬 Chat ready! Type 'help' for commands, 'exit' to quit.
🔧 Tools: 8 available
📊 Progress updates: OFF

You > Show me recent incidents
🤔 Processing...
🔧 Streaming fetch_incidents...
Bot > I found 5 recent incidents in your system. Here's a summary:

1. **Database Connection Timeout** (Critical)
   - Time: 2025-01-19 14:30 UTC
   - Duration: 15 minutes
   - Affected: Payment processing system

[... detailed response with tool results ...]
```

## 🛠️ Configuration

All configuration is done via the `.env` file. See `.env.example` for all available options.

### Required
- At least one LLM API key

### Optional  
- MCP server URL and authentication
- InsightFinder credentials
- SSL settings
- Chat behavior (history limit, progress updates)

## 📦 Dependencies

Core dependencies are minimal and modern:
- `langchain-core` - LangChain framework
- `langgraph` - Agent orchestration
- `httpx` - HTTP client with SSE support
- `python-dotenv` - Environment file loading
- `pydantic` - Data validation

LLM-specific packages are optional and only needed for the providers you want to use.
