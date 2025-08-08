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
    INSIGHTFINDER_LICENSE_KEY: str = os.getenv("INSIGHTFINDER_LICENSE_KEY")
    INSIGHTFINDER_SYSTEM_NAME: str = os.getenv("INSIGHTFINDER_SYSTEM_NAME")
    INSIGHTFINDER_USER_NAME: str = os.getenv("INSIGHTFINDER_USER_NAME")

    # MCP Server Configuration
    SERVER_NAME: str = "InsightFinderMCPServer"
    SERVER_VERSION: str = "1.1.0"
    
    # Logging Configuration
    ENABLE_DEBUG_MESSAGES: bool = os.getenv("ENABLE_DEBUG_MESSAGES", "false").lower() == "true"
    
    # Basic Security Configuration
    MAX_REQUESTS_PER_MINUTE: int = int(os.getenv("MAX_REQUESTS_PER_MINUTE", "60"))
    MAX_PAYLOAD_SIZE: int = int(os.getenv("MAX_PAYLOAD_SIZE", str(1024 * 1024)))  # 1MB default

# Create a singleton instance of the settings
settings = Settings()

# Validate that required settings are present
if not settings.INSIGHTFINDER_LICENSE_KEY:
    raise ValueError("Missing required environment variable: INSIGHTFINDER_LICENSE_KEY")

if not settings.INSIGHTFINDER_SYSTEM_NAME:
    raise ValueError("Missing required environment variable: INSIGHTFINDER_SYSTEM_NAME")

if not settings.INSIGHTFINDER_USER_NAME:
    raise ValueError("Missing required environment variable: INSIGHTFINDER_USER_NAME")