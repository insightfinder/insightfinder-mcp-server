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

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD python -c "import sys; sys.path.append('/app/src'); import insightfinder_mcp_server; print('OK')" || exit 1

# Set the entrypoint to use the script defined in pyproject.toml
ENTRYPOINT ["run-insightfinder-mcp-server"]
