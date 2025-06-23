import os
from dotenv import load_dotenv

# Load environment variables from a .env file if it exists
load_dotenv()

class Settings:
    """
    Application settings loaded from environment variables.
    """
    # InsightFinder API Configuration
    INSIGHTFINDER_API_URL: str = os.getenv("INSIGHTFINDER_API_URL", "https://app.insightfinder.com")
    INSIGHTFINDER_JWT_TOKEN: str = os.getenv("INSIGHTFINDER_JWT_TOKEN")
    INSIGHTFINDER_SYSTEM_NAME: str = os.getenv("INSIGHTFINDER_SYSTEM_NAME")
    INSIGHTFINDER_USER_NAME: str = os.getenv("INSIGHTFINDER_USER_NAME")

    # MCP Server Configuration
    SERVER_NAME: str = "InsightFinderMCPServer"
    SERVER_VERSION: str = "1.0.0"

# Create a singleton instance of the settings
settings = Settings()

# Validate that required settings are present
if not settings.INSIGHTFINDER_JWT_TOKEN:
    raise ValueError("Missing required environment variable: INSIGHTFINDER_JWT_TOKEN")

if not settings.INSIGHTFINDER_SYSTEM_NAME:
    raise ValueError("Missing required environment variable: INSIGHTFINDER_SYSTEM_NAME")

if not settings.INSIGHTFINDER_USER_NAME:
    raise ValueError("Missing required environment variable: INSIGHTFINDER_USER_NAME")