#!/bin/bash

# Quick setup script for InsightFinder MCP Server GitHub Container Registry

echo "🚀 InsightFinder MCP Server - GitHub Container Registry Setup"
echo "============================================================"

# Check if we're in a git repository and have the expected structure
if [[ -d ".git" ]] && [[ -d "src/insightfinder_mcp_server" ]]; then
    echo "✅ Found InsightFinder MCP Server project structure"
    REPO_PATH="."
else
    echo "⚠️  Expected project structure not found"
    echo "This script expects to be run from the root of the InsightFinder MCP Server project"
    echo "Looking for: src/insightfinder_mcp_server/ directory and .git folder"
    
    # Offer to continue anyway
    read -p "Continue anyway? (y/N): " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        exit 1
    fi
fi

# Create necessary directories if they don't exist
echo "📋 Setting up repository structure..."
mkdir -p .github/workflows
mkdir -p tests

# Backup existing files before overwriting
backup_file() {
    local file="$1"
    if [[ -f "$file" ]]; then
        echo "📁 Backing up existing $file to ${file}.backup"
        cp "$file" "${file}.backup"
    fi
}

# Create GitHub Actions workflow
echo "📁 Creating GitHub Actions workflow..."
backup_file ".github/workflows/docker-publish.yml"
cat > .github/workflows/docker-publish.yml << 'EOF'
name: Build and Push Docker Image

on:
  push:
    branches: [ "main", "master" ]
    tags: [ 'v*.*.*' ]
  pull_request:
    branches: [ "main", "master" ]

env:
  REGISTRY: ghcr.io
  IMAGE_NAME: ${{ github.repository }}

jobs:
  build:
    runs-on: ubuntu-latest
    permissions:
      contents: read
      packages: write
      id-token: write

    steps:
      - name: Checkout repository
        uses: actions/checkout@v4

      - name: Set up Docker Buildx
        uses: docker/setup-buildx-action@v3

      - name: Log into registry ${{ env.REGISTRY }}
        if: github.event_name != 'pull_request'
        uses: docker/login-action@v3
        with:
          registry: ${{ env.REGISTRY }}
          username: ${{ github.actor }}
          password: ${{ secrets.GITHUB_TOKEN }}

      - name: Extract metadata (tags, labels) for Docker
        id: meta
        uses: docker/metadata-action@v5
        with:
          images: ${{ env.REGISTRY }}/${{ env.IMAGE_NAME }}
          tags: |
            type=ref,event=branch
            type=ref,event=pr
            type=semver,pattern={{version}}
            type=semver,pattern={{major}}.{{minor}}
            type=raw,value=latest,enable={{is_default_branch}}

      - name: Build and push Docker image
        uses: docker/build-push-action@v6
        with:
          context: .
          platforms: linux/amd64,linux/arm64
          push: ${{ github.event_name != 'pull_request' }}
          tags: ${{ steps.meta.outputs.tags }}
          labels: ${{ steps.meta.outputs.labels }}
          cache-from: type=gha
          cache-to: type=gha,mode=max
EOF

# Create requirements.txt for Docker (without the problematic git dependency)
echo "📦 Creating Docker requirements.txt..."
backup_file "docker-requirements.txt"
cat > docker-requirements.txt << 'EOF'
annotated-types==0.7.0
anyio==4.9.0
certifi==2025.4.26
click==8.2.1
fastapi==0.115.13
h11==0.16.0
httpcore==1.0.9
httpx==0.28.1
httpx-sse==0.4.0
idna==3.10
markdown-it-py==3.0.0
mcp==1.9.4
mdurl==0.1.2
pydantic==2.11.5
pydantic-settings==2.9.1
pydantic_core==2.33.2
Pygments==2.19.1
python-dotenv==1.1.0
python-multipart==0.0.20
rich==14.0.0
shellingham==1.5.4
sniffio==1.3.1
sse-starlette==2.3.6
starlette==0.46.2
typer==0.16.0
typing-inspection==0.4.1
typing_extensions==4.14.0
uvicorn==0.34.3
EOF

# Create Dockerfile
echo "🐳 Creating Dockerfile..."
backup_file "Dockerfile"
cat > Dockerfile << 'EOF'
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

# Set environment variables
ENV INSIGHTFINDER_API_URL=""
ENV INSIGHTFINDER_JWT_TOKEN=""
ENV INSIGHTFINDER_SYSTEM_NAME=""
ENV INSIGHTFINDER_USER_NAME=""

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD python -c "import sys; sys.path.append('/app/src'); import insightfinder_mcp_server; print('OK')" || exit 1

# Set the entrypoint to use the script defined in pyproject.toml
ENTRYPOINT ["run-insightfinder-mcp-server"]
EOF

# Create .dockerignore
echo "🚫 Creating .dockerignore..."
backup_file ".dockerignore"
cat > .dockerignore << 'EOF'
.git
.github
__pycache__
*.pyc
*.pyo
*.pyd
.pytest_cache
.coverage
.venv
venv/
env/
.env
.env.local
*.log
.DS_Store
Thumbs.db
node_modules
.idea
.vscode
*.swp
*.swo
*~
EOF

# Note: Preserving existing Python package structure in src/insightfinder_mcp_server/
echo "🐍 Preserving existing Python package structure..."
if [[ -f "src/insightfinder_mcp_server/main.py" ]]; then
    echo "✅ Found existing main.py - keeping current implementation"
else
    echo "⚠️  main.py not found, you may need to implement your MCP server logic"
fi

if [[ -f "pyproject.toml" ]]; then
    echo "✅ Found existing pyproject.toml - keeping current configuration"
else
    echo "📄 Creating basic pyproject.toml..."
    cat > pyproject.toml << 'EOF'
[build-system]
requires = ["setuptools>=61.0", "wheel"]
build-backend = "setuptools.build_meta"

[project]
name = "insightfinder-mcp-server"
version = "0.1.0"
description = "MCP Server for InsightFinder AI Engine"
authors = [
    {name = "InsightFinder", email = "support@insightfinder.com"}
]
license = {text = "Apache-2.0"}
readme = "README.md"
requires-python = ">=3.11"
dependencies = [
    "mcp>=1.0.0",
    "httpx>=0.25.0",
    "python-dotenv>=1.0.0",
    "pydantic>=2.0.0",
]

[project.urls]
Homepage = "https://github.com/insightfinder/insightfinder-mcp-server"
Repository = "https://github.com/insightfinder/insightfinder-mcp-server"

[project.scripts]
run-insightfinder-mcp-server = "insightfinder_mcp_server.main:run"
EOF
fi

echo ""
echo "✅ Setup complete! Here's what was created/updated:"
echo ""
echo "📁 Repository structure:"
echo "├── .github/workflows/docker-publish.yml  (GitHub Actions workflow)"
echo "├── src/insightfinder_mcp_server/          (Existing Python package preserved)"
echo "├── Dockerfile                             (Docker container definition)"
echo "├── docker-requirements.txt               (Docker-specific Python dependencies)"
echo "├── pyproject.toml                         (Python project config - preserved if exists)"
echo "├── .dockerignore                          (Docker build exclusions)"
echo ""
echo "� Backup files created:"
echo "Any existing files were backed up with .backup extension"
echo ""
echo "🚀 Next steps:"
echo "1. Review the generated Dockerfile and docker-requirements.txt"
echo "2. Test the Docker build locally:"
echo "   docker build -t insightfinder-mcp-server ."
echo "3. Commit and push to trigger the first build:"
echo ""
echo "   git add ."
echo "   git commit -m 'Add GitHub Container Registry support'"
echo "   git push origin main"
echo ""
echo "4. Monitor the build in GitHub Actions"
echo "5. Once built, your image will be available at:"
echo "   ghcr.io/insightfinder/insightfinder-mcp-server:latest"
echo ""
echo "🧪 To test locally:"
echo "   docker build -t test-mcp-server ."
echo "   docker run -it --rm \\"
echo "     -e INSIGHTFINDER_JWT_TOKEN=\"your_token\" \\"
echo "     -e INSIGHTFINDER_API_URL=\"https://lenovo.insightfinder.com\" \\"
echo "     test-mcp-server"
echo ""
echo "📖 For more details, see the setup guide: GITHUB_SETUP_GUIDE.md"
