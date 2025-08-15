import asyncio
import sys
from typing import Optional
import uvicorn
from fastapi import FastAPI, Request, Response, Depends
from fastapi.responses import StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
import json
import logging
from ..config.settings import settings
from ..security import security_manager, AuthenticationError, AuthorizationError, RateLimitError
from .server import mcp_server

logger = logging.getLogger(__name__)

class HTTPMCPServer:
    """
    HTTP MCP Server that provides streaming JSON-RPC over HTTP.
    """
    
    def __init__(self):
        self.app = FastAPI(
            title=settings.SERVER_NAME,
            version=settings.SERVER_VERSION,
            description="InsightFinder MCP Server - HTTP Transport"
        )
        
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
        
        # Add CORS middleware if enabled
        if settings.HTTP_CORS_ENABLED:
            origins = [origin.strip() for origin in settings.HTTP_CORS_ORIGINS.split(",")]
            self.app.add_middleware(
                CORSMiddleware,
                allow_origins=origins,
                allow_credentials=True,
                allow_methods=["GET", "POST"],
                allow_headers=["*"],
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
                
            except Exception as e:
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
                response = await self.process_mcp_request(rpc_request)
                
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
        
        @self.app.post("/mcp/stream")
        async def handle_streaming_mcp_request(request: Request):
            """Handle streaming MCP JSON-RPC requests."""
            try:
                body = await request.body()
                rpc_request = json.loads(body.decode('utf-8'))
                
                async def generate_response():
                    """Generate streaming response."""
                    response = await self.process_mcp_request(rpc_request)
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
    
    async def process_mcp_request(self, rpc_request: dict) -> dict:
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
                    tool = tools_dict[tool_name]
                    tool_func = getattr(tool, 'fn', tool)  # Get the actual function from the Tool object
                    
                    # Call the tool function
                    if getattr(tool, 'is_async', False):
                        result = await tool_func(**arguments)
                    else:
                        result = tool_func(**arguments)
                    
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
        
        return {
            "tools": {
                "listChanged": True,
                "supportsProgress": False
            },
            "logging": {},
            "prompts": {},
            "resources": {},
            "experimental": {
                "toolCount": tool_count
            }
        }
    
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

        print("=" * 60, file=sys.stderr)
        
        await server.serve()

# Create server instance
http_server = HTTPMCPServer()
