#!/bin/bash

# Docker build and publish script for InsightFinder MCP Server

# Configuration
IMAGE_NAME="insightfinder-mcp-server"
REGISTRY="docker.io"  # Docker Hub registry
USERNAME="${DOCKER_USERNAME:-insightfinder}"  # Docker Hub username (can be overridden with env var)
TAG="${DOCKER_TAG:-latest}"  # Tag (can be overridden with env var)

FULL_IMAGE_NAME="${REGISTRY}/${USERNAME}/${IMAGE_NAME}:${TAG}"

echo "Building Docker image: ${FULL_IMAGE_NAME}"

# Check if Docker is running
if ! docker info > /dev/null 2>&1; then
    echo "✗ Docker is not running or accessible"
    exit 1
fi

# Build the Docker image
docker build -t "${FULL_IMAGE_NAME}" .

if [ $? -eq 0 ]; then
    echo "✓ Docker image built successfully"
    
    # Ask if user wants to push to registry
    read -p "Do you want to push to registry? (y/N): " -n 1 -r
    echo
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        # Check if logged into Docker Hub
        if ! docker info | grep -q "Username:"; then
            echo "Please log in to Docker Hub first:"
            echo "docker login"
            exit 1
        fi
        
        echo "Pushing to registry..."
        docker push "${FULL_IMAGE_NAME}"
        
        if [ $? -eq 0 ]; then
            echo "✓ Image pushed successfully"
            echo "Image available at: ${FULL_IMAGE_NAME}"
        else
            echo "✗ Failed to push image"
            exit 1
        fi
    else
        echo "Skipping push to registry"
        echo "Local image available as: ${FULL_IMAGE_NAME}"
    fi
else
    echo "✗ Failed to build Docker image"
    exit 1
fi

echo ""
echo "To use this image, update your make_connections() function with:"
echo "\"command\": \"docker\","
echo "\"args\": ["
echo "    \"run\","
echo "    \"-i\","
echo "    \"--rm\","
echo "    \"-e\", \"INSIGHTFINDER_API_URL=your_api_url\","
echo "    \"-e\", \"INSIGHTFINDER_JWT_TOKEN=your_jwt_token\","
echo "    \"-e\", \"INSIGHTFINDER_SYSTEM_NAME=your_system_name\","
echo "    \"-e\", \"INSIGHTFINDER_USER_NAME=your_user_name\","
echo "    \"${FULL_IMAGE_NAME}\""
echo "]"
