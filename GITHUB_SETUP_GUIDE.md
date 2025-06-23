# InsightFinder MCP Server - GitHub Container Registry Setup Guide

This guide will help you set up the InsightFinder MCP Server repository with GitHub Container Registry (ghcr.io) integration.

## Prerequisites

1. **Repository Access**: You need write access to the `insightfinder/insightfinder-mcp-server` repository
2. **GitHub Packages Permissions**: The repository needs GitHub Packages enabled
3. **Docker**: Docker installed locally for testing

## Repository Setup Steps

### 1. Repository Structure

Create the following structure in the `insightfinder/insightfinder-mcp-server` repository:

```
insightfinder-mcp-server/
├── .github/
│   └── workflows/
│       └── docker-publish.yml
├── src/
│   └── insightfinder_mcp_server/
│       ├── __init__.py
│       └── main.py
├── tests/
├── Dockerfile
├── requirements.txt
├── pyproject.toml
├── README.md
├── .gitignore
├── .dockerignore
└── LICENSE
```

### 2. Copy Essential Files

Copy these files from your current setup to the InsightFinder repository:

1. **`.github/workflows/docker-publish.yml`** - GitHub Actions workflow
2. **`Dockerfile`** - Docker container definition
3. **`requirements.txt`** - Python dependencies
4. **`.dockerignore`** - Docker build exclusions

### 3. GitHub Repository Settings

#### Enable GitHub Packages:
1. Go to `https://github.com/insightfinder/insightfinder-mcp-server/settings`
2. Navigate to "Actions" → "General"
3. Under "Workflow permissions", ensure:
   - ✅ "Read and write permissions" is selected
   - ✅ "Allow GitHub Actions to create and approve pull requests" is checked

#### Package Visibility:
1. After your first push, go to "Packages" tab
2. Click on your package
3. Set visibility to "Public" if desired

### 4. Environment Variables

The workflow uses these automatic GitHub secrets:
- `GITHUB_TOKEN` - Automatically provided by GitHub
- `GITHUB_ACTOR` - Your GitHub username

### 5. Using the Published Image

Once published, your image will be available at:
```
ghcr.io/insightfinder/insightfinder-mcp-server:latest
ghcr.io/insightfinder/insightfinder-mcp-server:main
ghcr.io/insightfinder/insightfinder-mcp-server:v1.0.0 (for tagged releases)
```

## Development Workflow

### Local Development

1. **Clone the repository:**
   ```bash
   git clone https://github.com/insightfinder/insightfinder-mcp-server.git
   cd insightfinder-mcp-server
   ```

2. **Build locally:**
   ```bash
   docker build -t insightfinder-mcp-server .
   ```

3. **Test locally:**
   ```bash
   docker run -it --rm \
     -e INSIGHTFINDER_JWT_TOKEN="your_token" \
     -e INSIGHTFINDER_API_URL="https://app.insightfinder.com" \
     insightfinder-mcp-server
   ```

### GitHub Actions Workflow

The workflow triggers on:
- **Push to main/master**: Builds and pushes `latest` and `main` tags
- **Pull Requests**: Builds but doesn't push (for testing)
- **Tagged Releases**: Builds and pushes version tags (e.g., `v1.0.0`)

### Creating a Release

1. **Tag your release:**
   ```bash
   git tag v1.0.0
   git push origin v1.0.0
   ```

2. **Create a GitHub Release:**
   - Go to `https://github.com/insightfinder/insightfinder-mcp-server/releases`
   - Click "Create a new release"
   - Select your tag
   - Add release notes

## Using the Docker Image

### In your main.py (like your current setup):

```python
def make_connections() -> Dict[str, Any]:
    api_url = os.getenv("INSIGHTFINDER_API_URL", "https://app.insightfinder.com")
    jwt_token = os.getenv("INSIGHTFINDER_JWT_TOKEN")
    
    if not jwt_token:
        raise RuntimeError("Set INSIGHTFINDER_JWT_TOKEN in env.")

    return {
        "insightfinder": {
            "command": "docker",
            "args": [
                "run",
                "-i",
                "--rm",
                "-e", f"INSIGHTFINDER_API_URL={api_url}",
                "-e", f"INSIGHTFINDER_JWT_TOKEN={jwt_token}",
                "-e", f"INSIGHTFINDER_SYSTEM_NAME={system_name}",
                "-e", f"INSIGHTFINDER_USER_NAME={user_name}",
                "ghcr.io/insightfinder/insightfinder-mcp-server:latest"
            ],
            "transport": "stdio",
        }
    }
```

### With Docker Compose:

```yaml
version: '3.8'
services:
  insightfinder-mcp:
    image: ghcr.io/insightfinder/insightfinder-mcp-server:latest
    environment:
      - INSIGHTFINDER_API_URL=${INSIGHTFINDER_API_URL}
      - INSIGHTFINDER_JWT_TOKEN=${INSIGHTFINDER_JWT_TOKEN}
      - INSIGHTFINDER_SYSTEM_NAME=${INSIGHTFINDER_SYSTEM_NAME}
      - INSIGHTFINDER_USER_NAME=${INSIGHTFINDER_USER_NAME}
    stdin_open: true
    tty: true
```

## Troubleshooting

### Common Issues:

1. **Permission Denied**: Ensure GitHub Packages is enabled and you have write access
2. **Image Not Found**: Check if the workflow completed successfully
3. **Build Failures**: Check the Actions tab for build logs

### Useful Commands:

```bash
# Pull the latest image
docker pull ghcr.io/insightfinder/insightfinder-mcp-server:latest

# List all available tags
gh api repos/insightfinder/insightfinder-mcp-server/packages

# View workflow runs
gh run list --repo insightfinder/insightfinder-mcp-server
```

## Next Steps

1. **Copy the workflow file** to the InsightFinder repository
2. **Set up the basic Python package structure**
3. **Create your first commit and push**
4. **Monitor the GitHub Actions** to ensure successful build
5. **Test the published image** in your chatbot application

The image will be automatically built and published to `ghcr.io/insightfinder/insightfinder-mcp-server` whenever you push to the main branch!
