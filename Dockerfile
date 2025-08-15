FROM python:3.11-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    git \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements and install Python dependencies
COPY docker-requirements.txt requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Copy the application source code
COPY src/ ./src/
COPY pyproject.toml* ./
COPY README.md* ./

# Install the package in development mode
RUN pip install -e .

# Create non-root user for security
RUN groupadd -r appuser && useradd -r -g appuser appuser
RUN chown -R appuser:appuser /app
USER appuser

# Expose default ports
EXPOSE 8000

# Environment variables with defaults
ENV TRANSPORT_TYPE=stdio
ENV SERVER_HOST=0.0.0.0
ENV SERVER_PORT=8000
ENV HTTP_AUTH_ENABLED=true
ENV HTTP_AUTH_METHOD=api_key
ENV HTTP_RATE_LIMIT_ENABLED=true
ENV MAX_REQUESTS_PER_MINUTE=60
ENV ENABLE_DEBUG_MESSAGES=false

# Health check that works for both stdio and http modes
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD if [ "$TRANSPORT_TYPE" = "http" ]; then \
            curl -f http://localhost:${SERVER_PORT}/health || exit 1; \
        else \
            python -c "import sys; sys.path.append('/app/src'); import insightfinder_mcp_server; print('OK')" || exit 1; \
        fi

# Set the entrypoint to use the script defined in pyproject.toml
ENTRYPOINT ["run-insightfinder-mcp-server"]
