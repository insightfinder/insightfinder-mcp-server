import asyncio
import sys
import time
from typing import Optional, AsyncGenerator, Dict, Any
import uvicorn
from fastapi import FastAPI, Request, Response, Depends
from fastapi.responses import StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from sse_starlette.sse import EventSourceResponse
import json
import logging
from ..config.settings import settings
from ..security import security_manager, AuthenticationError, AuthorizationError, RateLimitError
from ..api_client.client_factory import (
    create_api_client_from_request, 
    set_request_context, 
    clear_request_context
)
from .server import mcp_server

logger = logging.getLogger(__name__)

class HTTPMCPServer:
    """
    HTTP MCP Server that provides streaming JSON-RPC over HTTP with SSE support.
    """
    
    def __init__(self):
        self.app = FastAPI(
            title=settings.SERVER_NAME,
            version=settings.SERVER_VERSION,
            description="InsightFinder MCP Server - HTTP Transport with SSE Streaming"
        )
        
        # SSE connection tracking
        self.sse_connections: Dict[str, Dict[str, Any]] = {}
        self.connection_counter = 0
        
        # Add security middleware
        self.setup_middleware()
        
        # Debug: Check what's available in mcp_server
        if settings.ENABLE_DEBUG_MESSAGES:
            print(f"mcp_server type: {type(mcp_server)}", file=sys.stderr)
            attrs = [attr for attr in dir(mcp_server) if not attr.startswith('__')]
            tool_attrs = [attr for attr in attrs if 'tool' in attr.lower()]
            print(f"Tool-related attributes: {tool_attrs}", file=sys.stderr)
            
            # Try to access different tool storage mechanisms
            for attr_name in ['_tools', 'tools', '_tool_handlers', 'tool_handlers', '_registered_tools']:
                if hasattr(mcp_server, attr_name):
                    tools = getattr(mcp_server, attr_name)
                    print(f"{attr_name}: {type(tools)} - {len(tools) if tools and hasattr(tools, '__len__') else 'N/A'}", file=sys.stderr)
        
        self.setup_routes()
    
    def setup_middleware(self):
        """Setup FastAPI middleware for security."""
        
        # Add trusted host middleware when behind proxy
        if settings.BEHIND_PROXY:
            allowed_hosts = [host.strip() for host in settings.ALLOWED_HOSTS.split(",")]
            self.app.add_middleware(
                TrustedHostMiddleware,
                allowed_hosts=allowed_hosts + ["*"]  # Allow all hosts if behind proxy
            )
        
        # Add CORS middleware if enabled
        if settings.HTTP_CORS_ENABLED or settings.SSE_ENABLED:
            origins = [origin.strip() for origin in settings.HTTP_CORS_ORIGINS.split(",")]
            sse_headers = [header.strip() for header in settings.SSE_CORS_HEADERS.split(",")]
            
            self.app.add_middleware(
                CORSMiddleware,
                allow_origins=origins,
                allow_credentials=True,
                allow_methods=["GET", "POST", "OPTIONS"],
                allow_headers=["*"] + sse_headers,
                expose_headers=sse_headers,
            )
        
        # Add request size limiting
        @self.app.middleware("http")
        async def limit_request_size(request: Request, call_next):
            if request.method == "POST":
                content_length = request.headers.get("content-length")
                if content_length and int(content_length) > settings.MAX_PAYLOAD_SIZE:
                    return Response(
                        content=json.dumps({"error": "Request payload too large"}),
                        status_code=413,
                        media_type="application/json"
                    )
            return await call_next(request)
        
        # Add authentication middleware
        @self.app.middleware("http")
        async def authenticate_request(request: Request, call_next):
            try:
                # Skip authentication for health check and root endpoints
                if request.url.path in ["/", "/health", "/docs", "/openapi.json"]:
                    return await call_next(request)
                
                # Handle proxy headers for HTTPS detection
                if settings.BEHIND_PROXY and settings.TRUST_PROXY_HEADERS:
                    # Check if request came through HTTPS proxy
                    forwarded_proto = request.headers.get("X-Forwarded-Proto", "").lower()
                    if forwarded_proto == "https":
                        request.scope["scheme"] = "https"
                    
                    # Update host if forwarded
                    forwarded_host = request.headers.get("X-Forwarded-Host")
                    if forwarded_host:
                        request.scope["headers"] = [
                            (name, value) if name != b"host" else (b"host", forwarded_host.encode())
                            for name, value in request.scope.get("headers", [])
                        ]
                
                # Authenticate the request
                await security_manager.authenticate(request)
                return await call_next(request)
                
            except (AuthenticationError, AuthorizationError, RateLimitError) as e:
                return Response(
                    content=json.dumps({"error": str(e.detail)}),
                    status_code=e.status_code,
                    media_type="application/json"
                )
            except Exception as e:
                logger.error(f"Authentication error: {e}")
                return Response(
                    content=json.dumps({"error": "Authentication failed"}),
                    status_code=500,
                    media_type="application/json"
                )
    
    def setup_routes(self):
        """Setup FastAPI routes for MCP protocol."""
        
        @self.app.get("/")
        async def root():
            return {
                "name": settings.SERVER_NAME,
                "version": settings.SERVER_VERSION,
                "protocol": "mcp",
                "transport": "http",
                "authentication": {
                    "enabled": settings.HTTP_AUTH_ENABLED,
                    "method": settings.HTTP_AUTH_METHOD if settings.HTTP_AUTH_ENABLED else None
                },
                "capabilities": await self.get_capabilities()
            }
        
        @self.app.get("/health")
        async def health_check():
            return {"status": "healthy", "service": settings.SERVER_NAME}
        
        # SSE endpoints for streaming
        if settings.SSE_ENABLED:
            @self.app.get("/mcp/events")
            async def mcp_sse_events(request: Request):
                """SSE endpoint for MCP streaming protocol."""
                connection_id = self._add_sse_connection(request)
                return EventSourceResponse(
                    self._generate_sse_events(request, connection_id),
                    headers={
                        "Cache-Control": "no-cache",
                        "Connection": "keep-alive",
                        "X-Connection-ID": connection_id
                    }
                )
            
            @self.app.post("/mcp/stream")
            async def mcp_stream_request(request: Request):
                """Handle streaming MCP requests via SSE."""
                connection_id = self._add_sse_connection(request)
                
                try:
                    body = await request.body()
                    if not body:
                        raise ValueError("Empty request body")
                    
                    mcp_request = json.loads(body.decode('utf-8'))
                    
                    return EventSourceResponse(
                        self._handle_streaming_mcp_request(request, connection_id, mcp_request),
                        headers={
                            "Cache-Control": "no-cache", 
                            "Connection": "keep-alive",
                            "X-Connection-ID": connection_id
                        }
                    )
                    
                except Exception as e:
                    self._remove_sse_connection(connection_id)
                    return Response(
                        content=json.dumps({"error": str(e)}),
                        status_code=400,
                        media_type="application/json"
                    )
            
            @self.app.post("/tools/{tool_name}/stream")
            async def stream_tool_execution(tool_name: str, request: Request):
                """Stream individual tool execution via SSE."""
                connection_id = self._add_sse_connection(request)
                
                try:
                    body = await request.body()
                    tool_args = json.loads(body.decode('utf-8')) if body else {}
                    
                    return EventSourceResponse(
                        self._stream_tool_execution(request, connection_id, tool_name, tool_args),
                        headers={
                            "Cache-Control": "no-cache",
                            "Connection": "keep-alive", 
                            "X-Connection-ID": connection_id
                        }
                    )
                    
                except Exception as e:
                    self._remove_sse_connection(connection_id)
                    return Response(
                        content=json.dumps({"error": str(e)}),
                        status_code=400,
                        media_type="application/json"
                    )
            
            @self.app.get("/sse/connections")
            async def get_sse_connections():
                """Get active SSE connections (debug endpoint)."""
                return {
                    "total_connections": len(self.sse_connections),
                    "connections": [
                        {
                            "id": conn_id,
                            "created_at": info["created_at"],
                            "active": info["active"]
                        }
                        for conn_id, info in self.sse_connections.items()
                    ]
                }
        
        @self.app.get("/tools")
        async def list_tools_http():
            """HTTP endpoint to list all available tools."""
            try:
                tools_data = []
                
                # Access tools via the tool manager
                if hasattr(mcp_server, '_tool_manager'):
                    tool_manager = mcp_server._tool_manager
                    
                    # Get tools from the _tools dictionary
                    if hasattr(tool_manager, '_tools'):
                        tools_dict = tool_manager._tools
                        
                        for name, tool in tools_dict.items():
                            tools_data.append({
                                "name": name,
                                "description": getattr(tool, 'description', ''),
                                "parameters": getattr(tool, 'parameters', {}),
                                "is_async": getattr(tool, 'is_async', False)
                            })
                
                return {
                    "tools": tools_data,
                    "count": len(tools_data)
                }
            except Exception as e:
                return {"error": str(e), "tools": [], "count": 0}
        
        @self.app.post("/tools/{tool_name}")
        async def call_tool_http(tool_name: str, request: Request):
            """HTTP endpoint to call a specific tool."""
            try:
                # Create API client from request headers
                api_client = create_api_client_from_request(request)
                
                # Set request context for tools to access
                set_request_context(request, api_client)
                
                try:
                    # Get arguments from request body
                    body = await request.body()
                    if body:
                        arguments = json.loads(body.decode('utf-8'))
                    else:
                        arguments = {}
                    
                    # Access tools via the tool manager
                    tools_dict = None
                    if hasattr(mcp_server, '_tool_manager'):
                        tool_manager = mcp_server._tool_manager
                        if hasattr(tool_manager, '_tools'):
                            tools_dict = tool_manager._tools
                    
                    if not tools_dict or tool_name not in tools_dict:
                        return {
                            "error": f"Tool not found: {tool_name}",
                            "available_tools": list(tools_dict.keys()) if tools_dict else []
                        }
                    
                    # Call the tool
                    tool = tools_dict[tool_name]
                    tool_func = getattr(tool, 'fn', tool)
                    
                    if getattr(tool, 'is_async', False):
                        result = await tool_func(**arguments)
                    else:
                        result = tool_func(**arguments)
                    
                    return {
                        "tool": tool_name,
                        "arguments": arguments,
                        "result": result
                    }
                    
                finally:
                    # Always clean up request context
                    clear_request_context()
                    
            except Exception as e:
                clear_request_context()  # Cleanup on error too
                return {"error": str(e)}
        
        @self.app.post("/mcp")
        async def handle_mcp_request(request: Request):
            """Handle MCP JSON-RPC requests."""
            try:
                body = await request.body()
                if not body:
                    return Response(
                        content=json.dumps({"error": "Empty request body"}),
                        status_code=400,
                        media_type="application/json"
                    )
                
                # Parse JSON-RPC request
                try:
                    rpc_request = json.loads(body.decode('utf-8'))
                except json.JSONDecodeError as e:
                    return Response(
                        content=json.dumps({"error": f"Invalid JSON: {str(e)}"}),
                        status_code=400,
                        media_type="application/json"
                    )
                
                # Process the MCP request
                response = await self.process_mcp_request(rpc_request, request)
                
                return Response(
                    content=json.dumps(response, default=self.json_serializer),
                    media_type="application/json"
                )
                
            except Exception as e:
                logger.error(f"Error processing MCP request: {e}")
                error_response = {
                    "jsonrpc": "2.0",
                    "id": rpc_request.get("id") if 'rpc_request' in locals() else None,
                    "error": {
                        "code": -32603,
                        "message": f"Internal error: {str(e)}"
                    }
                }
                return Response(
                    content=json.dumps(error_response),
                    status_code=500,
                    media_type="application/json"
                )

        @self.app.post("/trace")
        async def trace_llm_interaction(request: Request):
            """Receive LLM prompt/response traces from the client and log them.

            Expected JSON body (flexible, extra fields allowed):
            {
              "provider": "openai",
              "model": "gpt-4o",
              "prompt": "user text",
              "response": "assistant text"
            }
            """
            try:
                payload = await request.json()
            except Exception:
                payload = {"error": "invalid_json"}

            # Minimal normalization
            record = {
                "provider": payload.get("provider"),
                "model": payload.get("model"),
                "prompt": payload.get("prompt"),
                "response": payload.get("response"),
                "ts": time.time()
            }

            # Log in structured form (single line JSON for easy parsing)
            try:
                logger.info("LLM_TRACE %s", json.dumps(record, ensure_ascii=False))
            except Exception as e:  # Fallback if serialization fails
                logger.info(f"LLM_TRACE provider={record.get('provider')} model={record.get('model')} serialization_error={e}")

            if settings.ENABLE_DEBUG_MESSAGES:
                # Also emit to stderr for quick visibility
                print(f"TRACE => {json.dumps(record, ensure_ascii=False)}", file=sys.stderr)

            return {"status": "ok"}
        
        @self.app.post("/mcp/stream")
        async def handle_streaming_mcp_request(request: Request):
            """Handle streaming MCP JSON-RPC requests."""
            try:
                body = await request.body()
                rpc_request = json.loads(body.decode('utf-8'))
                
                async def generate_response():
                    """Generate streaming response."""
                    response = await self.process_mcp_request(rpc_request, request)
                    yield f"data: {json.dumps(response, default=self.json_serializer)}\n\n"
                
                return StreamingResponse(
                    generate_response(),
                    media_type="text/event-stream",
                    headers={
                        "Cache-Control": "no-cache",
                        "Connection": "keep-alive",
                        "Access-Control-Allow-Origin": "*",
                        "Access-Control-Allow-Methods": "POST, GET, OPTIONS",
                        "Access-Control-Allow-Headers": "Content-Type",
                    }
                )
                
            except Exception as e:
                logger.error(f"Error processing streaming MCP request: {e}")
                return Response(
                    content=json.dumps({"error": str(e)}),
                    status_code=500,
                    media_type="application/json"
                )
    
    def _add_sse_connection(self, request: Request) -> str:
        """Add and track SSE connection."""
        connection_id = f"conn_{self.connection_counter}_{int(time.time())}"
        self.connection_counter += 1
        
        self.sse_connections[connection_id] = {
            "created_at": time.time(),
            "request": request,
            "active": True
        }
        
        # Clean up old connections if we exceed max
        if len(self.sse_connections) > settings.SSE_MAX_CONNECTIONS:
            oldest_conn = min(self.sse_connections.items(), 
                            key=lambda x: x[1]["created_at"])
            del self.sse_connections[oldest_conn[0]]
        
        return connection_id
    
    def _remove_sse_connection(self, connection_id: str):
        """Remove SSE connection from tracking."""
        self.sse_connections.pop(connection_id, None)
    
    async def _generate_sse_events(self, request: Request, connection_id: str) -> AsyncGenerator[Dict[str, Any], None]:
        """Generate SSE events for a connection."""
        try:
            # Send initial connection event
            yield {
                "event": "connected",
                "data": json.dumps({
                    "connection_id": connection_id,
                    "timestamp": time.time(),
                    "server": settings.SERVER_NAME
                })
            }
            
            # Keep connection alive with heartbeats
            while True:
                if await request.is_disconnected():
                    break
                
                connection = self.sse_connections.get(connection_id)
                if not connection or not connection.get("active"):
                    break
                
                if settings.SSE_HEARTBEAT_ENABLED:
                    yield {
                        "event": "heartbeat",
                        "data": json.dumps({
                            "timestamp": time.time(),
                            "connection_id": connection_id
                        })
                    }
                
                await asyncio.sleep(settings.SSE_PING_INTERVAL)
                
        except asyncio.CancelledError:
            self._remove_sse_connection(connection_id)
            raise
        except Exception as e:
            logger.error(f"SSE event generation error: {e}")
            yield {
                "event": "error", 
                "data": json.dumps({
                    "error": str(e),
                    "connection_id": connection_id
                })
            }
        finally:
            self._remove_sse_connection(connection_id)
    
    async def _stream_tool_execution(self, request: Request, connection_id: str, 
                                   tool_name: str, tool_args: Dict[str, Any]) -> AsyncGenerator[Dict[str, Any], None]:
        """Stream tool execution results generically."""
        try:
            # Send processing started event
            yield {
                "event": "tool_started",
                "data": json.dumps({
                    "tool": tool_name,
                    "arguments": tool_args,
                    "timestamp": time.time(),
                    "connection_id": connection_id
                })
            }
            
            # Execute the tool directly - let the tool handle its own streaming logic
            result = await self._execute_tool_direct(tool_name, tool_args, request)
            
            # Stream the result with optional batching for large datasets
            async for chunk in self._stream_result(request, tool_name, result):
                if await request.is_disconnected():
                    break
                yield chunk
            
            # Send completion event
            yield {
                "event": "tool_completed",
                "data": json.dumps({
                    "tool": tool_name,
                    "timestamp": time.time(),
                    "connection_id": connection_id
                })
            }
            
        except Exception as e:
            yield {
                "event": "tool_error",
                "data": json.dumps({
                    "tool": tool_name,
                    "error": str(e),
                    "timestamp": time.time(),
                    "connection_id": connection_id
                })
            }
    
    async def _stream_result(self, request: Request, tool_name: str, result: Any) -> AsyncGenerator[Dict[str, Any], None]:
        """Stream any result with intelligent batching."""
        # Handle different result types generically
        if isinstance(result, list) and len(result) > 10:
            # Stream large lists in batches
            batch_size = 10
            total_items = len(result)
            
            for i in range(0, total_items, batch_size):
                if await request.is_disconnected():
                    break
                    
                batch = result[i:i + batch_size]
                yield {
                    "event": "partial_result",
                    "data": json.dumps({
                        "tool": tool_name,
                        "batch": batch,
                        "progress": {
                            "current": i + len(batch),
                            "total": total_items,
                            "percentage": ((i + len(batch)) / total_items) * 100
                        },
                        "timestamp": time.time()
                    })
                }
                await asyncio.sleep(0.1)  # Small delay for streaming effect
                
        elif isinstance(result, dict) and any(key in result for key in ["anomalies", "incidents", "deployments", "traces"]):
            # Handle structured results with nested arrays
            for key, data in result.items():
                if isinstance(data, list) and len(data) > 5:
                    # Stream nested arrays in smaller batches
                    batch_size = 5
                    total_items = len(data)
                    
                    for i in range(0, total_items, batch_size):
                        if await request.is_disconnected():
                            break
                            
                        batch = data[i:i + batch_size]
                        yield {
                            "event": "partial_result",
                            "data": json.dumps({
                                "tool": tool_name,
                                "type": key,
                                "batch": batch,
                                "progress": {
                                    "current": i + len(batch),
                                    "total": total_items,
                                    "percentage": ((i + len(batch)) / total_items) * 100
                                },
                                "timestamp": time.time()
                            })
                        }
                        await asyncio.sleep(0.1)
                else:
                    # Small data or non-list data, send as complete result
                    yield {
                        "event": "tool_result",
                        "data": json.dumps({
                            "tool": tool_name,
                            "type": key,
                            "result": data,
                            "timestamp": time.time()
                        })
                    }
        else:
            # Simple result, send as complete
            yield {
                "event": "tool_result",
                "data": json.dumps({
                    "tool": tool_name,
                    "result": result,
                    "timestamp": time.time()
                })
            }
    
    async def _execute_tool_direct(self, tool_name: str, tool_args: Dict[str, Any], request: Request = None) -> Any:
        """Execute a tool directly and return results."""
        api_client = None
        
        try:
            # Set up API client context if request is provided
            if request:
                api_client = create_api_client_from_request(request)
                set_request_context(request, api_client)
            
            # Access tools via the tool manager
            tools_dict = None
            if hasattr(mcp_server, '_tool_manager'):
                tool_manager = mcp_server._tool_manager
                if hasattr(tool_manager, '_tools'):
                    tools_dict = tool_manager._tools
            
            if not tools_dict or tool_name not in tools_dict:
                raise ValueError(f"Tool not found: {tool_name}")
            
            # Call the tool
            tool = tools_dict[tool_name]
            tool_func = getattr(tool, 'fn', tool)
            
            if getattr(tool, 'is_async', False):
                return await tool_func(**tool_args)
            else:
                return tool_func(**tool_args)
                
        finally:
            # Always clean up request context if we set it up
            if request and api_client:
                clear_request_context()
    
    async def _handle_streaming_mcp_request(self, request: Request, connection_id: str, 
                                          mcp_request: Dict[str, Any]) -> AsyncGenerator[Dict[str, Any], None]:
        """Process MCP request and stream responses."""
        try:
            request_id = mcp_request.get("id")
            method = mcp_request.get("method")
            params = mcp_request.get("params", {})
            
            # Send initial acknowledgment
            yield {
                "event": "mcp_response",
                "data": json.dumps({
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "result": {"status": "processing", "connection_id": connection_id}
                })
            }
            
            if method == "tools/call":
                tool_name = params.get("name")
                tool_args = params.get("arguments", {})
                
                if not tool_name:
                    yield {
                        "event": "mcp_error",
                        "data": json.dumps({
                            "jsonrpc": "2.0",
                            "id": request_id,
                            "error": {"code": -32602, "message": "Missing tool name"}
                        })
                    }
                    return
                
                # Stream tool execution
                async for chunk in self._stream_tool_execution(request, connection_id, tool_name, tool_args):
                    if await request.is_disconnected():
                        break
                    
                    # Wrap tool events in MCP response format
                    if chunk.get("event") == "tool_result":
                        yield {
                            "event": "mcp_response",
                            "data": json.dumps({
                                "jsonrpc": "2.0",
                                "id": request_id,
                                "result": chunk["data"]
                            })
                        }
                    else:
                        yield chunk
                        
            elif method == "tools/list":
                # Handle tools list request
                tools_data = []
                if hasattr(mcp_server, '_tool_manager'):
                    tool_manager = mcp_server._tool_manager
                    if hasattr(tool_manager, '_tools'):
                        tools_dict = tool_manager._tools
                        for name, tool in tools_dict.items():
                            tools_data.append({
                                "name": name,
                                "description": getattr(tool, 'description', ''),
                                "parameters": getattr(tool, 'parameters', {}),
                                "is_async": getattr(tool, 'is_async', False)
                            })
                
                yield {
                    "event": "mcp_response",
                    "data": json.dumps({
                        "jsonrpc": "2.0",
                        "id": request_id,
                        "result": {"tools": tools_data}
                    })
                }
                
            elif method == "initialize":
                capabilities = await self.get_capabilities()
                yield {
                    "event": "mcp_response",
                    "data": json.dumps({
                        "jsonrpc": "2.0",
                        "id": request_id,
                        "result": {
                            "protocolVersion": "2024-11-05",
                            "capabilities": capabilities,
                            "serverInfo": {
                                "name": settings.SERVER_NAME,
                                "version": settings.SERVER_VERSION
                            }
                        }
                    })
                }
            else:
                # Process other MCP methods through the standard handler
                response = await self.process_mcp_request(mcp_request, request)
                yield {
                    "event": "mcp_response",
                    "data": json.dumps(response)
                }
            
            # Send completion event
            yield {
                "event": "mcp_complete",
                "data": json.dumps({
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "result": {"status": "completed", "connection_id": connection_id}
                })
            }
            
        except Exception as e:
            logger.error(f"Streaming MCP request error: {e}")
            yield {
                "event": "mcp_error",
                "data": json.dumps({
                    "jsonrpc": "2.0",
                    "id": mcp_request.get("id") if 'mcp_request' in locals() else None,
                    "error": {
                        "code": -32603,
                        "message": f"Internal error: {str(e)}",
                        "connection_id": connection_id
                    }
                })
            }
    
    async def process_mcp_request(self, rpc_request: dict, request: Request = None) -> dict:
        """Process an MCP JSON-RPC request."""
        try:
            method = rpc_request.get("method")
            params = rpc_request.get("params", {})
            request_id = rpc_request.get("id")
            
            if method == "initialize":
                # Handle initialization
                return {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "result": {
                        "protocolVersion": "2024-11-05",
                        "capabilities": await self.get_capabilities(),
                        "serverInfo": {
                            "name": settings.SERVER_NAME,
                            "version": settings.SERVER_VERSION
                        }
                    }
                }
            
            elif method == "tools/list":
                # List available tools
                tools = []
                
                # Access tools via the tool manager
                if hasattr(mcp_server, '_tool_manager'):
                    tool_manager = mcp_server._tool_manager
                    
                    # Get tools from the _tools dictionary
                    if hasattr(tool_manager, '_tools'):
                        tools_dict = tool_manager._tools
                        if settings.ENABLE_DEBUG_MESSAGES:
                            print(f"Found {len(tools_dict)} registered tools in _tool_manager._tools", file=sys.stderr)
                        
                        for name, tool in tools_dict.items():
                            try:
                                tool_info = {
                                    "name": name,
                                    "description": getattr(tool, 'description', ''),
                                    "inputSchema": getattr(tool, 'parameters', {})
                                }
                                tools.append(tool_info)
                            except Exception as e:
                                if settings.ENABLE_DEBUG_MESSAGES:
                                    print(f"Error processing tool {name}: {e}", file=sys.stderr)
                    else:
                        if settings.ENABLE_DEBUG_MESSAGES:
                            print("No _tools attribute found in tool_manager", file=sys.stderr)
                else:
                    if settings.ENABLE_DEBUG_MESSAGES:
                        print("No _tool_manager found in mcp_server", file=sys.stderr)
                
                if settings.ENABLE_DEBUG_MESSAGES:
                    print(f"Returning {len(tools)} tools", file=sys.stderr)
                
                return {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "result": {"tools": tools}
                }
            
            elif method == "tools/call":
                # Call a specific tool
                tool_name = params.get("name")
                arguments = params.get("arguments", {})
                
                # Access tools via the tool manager
                tools_dict = None
                if hasattr(mcp_server, '_tool_manager'):
                    tool_manager = mcp_server._tool_manager
                    if hasattr(tool_manager, '_tools'):
                        tools_dict = tool_manager._tools
                
                if not tools_dict or tool_name not in tools_dict:
                    return {
                        "jsonrpc": "2.0",
                        "id": request_id,
                        "error": {
                            "code": -32601,
                            "message": f"Tool not found: {tool_name}"
                        }
                    }
                
                try:
                    # Set up API client context if request is provided  
                    if request:
                        api_client = create_api_client_from_request(request)
                        set_request_context(request, api_client)
                    
                    try:
                        tool = tools_dict[tool_name]
                        tool_func = getattr(tool, 'fn', tool)  # Get the actual function from the Tool object
                        
                        # Call the tool function
                        if getattr(tool, 'is_async', False):
                            result = await tool_func(**arguments)
                        else:
                            result = tool_func(**arguments)
                            
                    finally:
                        # Always clean up request context if we set it up
                        if request:
                            clear_request_context()
                    
                    return {
                        "jsonrpc": "2.0",
                        "id": request_id,
                        "result": {
                            "content": [
                                {
                                    "type": "text",
                                    "text": json.dumps(result, default=self.json_serializer, indent=2)
                                }
                            ]
                        }
                    }
                except Exception as e:
                    return {
                        "jsonrpc": "2.0",
                        "id": request_id,
                        "error": {
                            "code": -32603,
                            "message": f"Tool execution error: {str(e)}"
                        }
                    }
            
            else:
                return {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "error": {
                        "code": -32601,
                        "message": f"Method not found: {method}"
                    }
                }
                
        except Exception as e:
            logger.error(f"Error in process_mcp_request: {e}")
            return {
                "jsonrpc": "2.0",
                "id": rpc_request.get("id"),
                "error": {
                    "code": -32603,
                    "message": f"Internal error: {str(e)}"
                }
            }
    
    async def get_capabilities(self) -> dict:
        """Get server capabilities."""
        # Count available tools
        tool_count = 0
        if hasattr(mcp_server, '_tool_manager'):
            tool_manager = mcp_server._tool_manager
            if hasattr(tool_manager, '_tools'):
                tool_count = len(tool_manager._tools)
        
        capabilities = {
            "tools": {
                "listChanged": True,
                "supportsProgress": True  # Enable progress support for streaming
            },
            "logging": {},
            "prompts": {},
            "resources": {},
            "experimental": {
                "toolCount": tool_count
            }
        }
        
        # Add streaming capabilities if SSE is enabled
        if settings.SSE_ENABLED:
            capabilities["streaming"] = {
                "supported": True,
                "transport": "sse",
                "endpoints": {
                    "events": "/mcp/events",
                    "stream": "/mcp/stream", 
                    "tool_stream": "/tools/{tool_name}/stream"
                },
                "features": {
                    "heartbeat": settings.SSE_HEARTBEAT_ENABLED,
                    "progress_tracking": True,
                    "batch_streaming": True,
                    "connection_tracking": True
                }
            }
        
        return capabilities
    
    def get_tool_schema(self, tool_func) -> dict:
        """Extract tool schema from function annotations."""
        import inspect
        
        sig = inspect.signature(tool_func)
        properties = {}
        required = []
        
        for param_name, param in sig.parameters.items():
            if param_name == 'self':
                continue
                
            param_type = param.annotation
            is_optional = param.default != inspect.Parameter.empty
            
            if not is_optional:
                required.append(param_name)
            
            # Basic type mapping
            if param_type == str:
                properties[param_name] = {"type": "string"}
            elif param_type == int:
                properties[param_name] = {"type": "integer"}
            elif param_type == bool:
                properties[param_name] = {"type": "boolean"}
            elif param_type == float:
                properties[param_name] = {"type": "number"}
            else:
                properties[param_name] = {"type": "string"}  # Default fallback
        
        return {
            "type": "object",
            "properties": properties,
            "required": required
        }
    
    def json_serializer(self, obj):
        """Custom JSON serializer for non-serializable objects."""
        if hasattr(obj, 'isoformat'):
            return obj.isoformat()
        raise TypeError(f"Object of type {type(obj)} is not JSON serializable")
    
    async def run(self):
        """Run the HTTP server."""
        config = uvicorn.Config(
            app=self.app,
            host=settings.SERVER_HOST,
            port=settings.SERVER_PORT,
            log_level="debug" if settings.ENABLE_DEBUG_MESSAGES else "info",
            access_log=settings.ENABLE_DEBUG_MESSAGES
        )
        
        server = uvicorn.Server(config)
        
        # Print startup information
        print(f"Starting {settings.SERVER_NAME} v{settings.SERVER_VERSION}", file=sys.stderr)
        print(f"Server running on http://{settings.SERVER_HOST}:{settings.SERVER_PORT}", file=sys.stderr)
        print(f"MCP endpoint: http://{settings.SERVER_HOST}:{settings.SERVER_PORT}/mcp", file=sys.stderr)
        
        # Print SSE information
        if settings.SSE_ENABLED:
            print(f"SSE Streaming: ENABLED", file=sys.stderr)
            print(f"SSE Events: http://{settings.SERVER_HOST}:{settings.SERVER_PORT}/mcp/events", file=sys.stderr)
            print(f"SSE Streaming: http://{settings.SERVER_HOST}:{settings.SERVER_PORT}/mcp/stream", file=sys.stderr)
            print(f"Max Connections: {settings.SSE_MAX_CONNECTIONS}", file=sys.stderr)
            if settings.SSE_HEARTBEAT_ENABLED:
                print(f"Heartbeat Interval: {settings.SSE_PING_INTERVAL}s", file=sys.stderr)
        else:
            print(f"SSE Streaming: DISABLED (set SSE_ENABLED=true to enable)", file=sys.stderr)
        
        # Print authentication status
        if settings.HTTP_AUTH_ENABLED:
            print(f"Authentication: ENABLED ({settings.HTTP_AUTH_METHOD.upper()})", file=sys.stderr)

            if settings.HTTP_AUTH_METHOD == "api_key":
                print(f"API Key: {settings.HTTP_API_KEY[:8]}...", file=sys.stderr)
                print(f"Usage: curl -H 'X-API-Key: {settings.HTTP_API_KEY}' http://localhost:{settings.SERVER_PORT}/mcp", file=sys.stderr)
            elif settings.HTTP_AUTH_METHOD == "bearer":
                print(f"Bearer Token: {settings.HTTP_BEARER_TOKEN[:8]}...", file=sys.stderr)
                print(f"Usage: curl -H 'Authorization: Bearer {settings.HTTP_BEARER_TOKEN}' http://localhost:{settings.SERVER_PORT}/mcp", file=sys.stderr)
            elif settings.HTTP_AUTH_METHOD == "basic":
                print(f"Basic Auth: {settings.HTTP_BASIC_USERNAME}:{settings.HTTP_BASIC_PASSWORD[:4]}...", file=sys.stderr)
                print(f"Usage: curl -u '{settings.HTTP_BASIC_USERNAME}:{settings.HTTP_BASIC_PASSWORD}' http://localhost:{settings.SERVER_PORT}/mcp", file=sys.stderr)

            if settings.HTTP_IP_WHITELIST:
                print(f"IP Whitelist: {settings.HTTP_IP_WHITELIST}", file=sys.stderr)

            if settings.HTTP_RATE_LIMIT_ENABLED:
                print(f"Rate Limit: {settings.MAX_REQUESTS_PER_MINUTE} requests/minute", file=sys.stderr)
        else:
            print(f"Authentication: DISABLED (set HTTP_AUTH_ENABLED=true to enable)", file=sys.stderr)

        # Print InsightFinder credential information
        print("", file=sys.stderr)  # Add blank line
        print("InsightFinder Credentials:", file=sys.stderr)
        print("  Provide via HTTP headers on each request:", file=sys.stderr)
        print("  - X-IF-License-Key: your-license-key", file=sys.stderr)
        print("  - X-IF-User-Name: your-username", file=sys.stderr)

        print("=" * 60, file=sys.stderr)
        
        await server.serve()

# Create server instance
http_server = HTTPMCPServer()
