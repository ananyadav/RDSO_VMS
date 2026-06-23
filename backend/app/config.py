import yaml
import os
from dotenv import load_dotenv

# Load environment variables from .env file
# This will look for .env file in the project root (two levels up from this file)
env_path = os.path.join(os.path.dirname(__file__), '..', '..', '.env')
load_dotenv(env_path)

def load_config():
    """Load configuration from config.yaml file."""
    config_path = os.path.join(os.path.dirname(__file__), '..', 'config.yaml')
    if os.path.exists(config_path):
        with open(config_path, 'r') as file:
            return yaml.safe_load(file)
    return {}

class Config:
    """Configuration class that loads from YAML and environment variables."""
    def __init__(self):
        self._config = load_config()
    
    @property
    def mongodb_uri(self):
        """Get MongoDB connection URI from environment variable."""
        return os.getenv('MONGODB_URI', 'mongodb://localhost:27017')
    
    @property
    def database_name(self):
        """Get database name from MongoDB URI or use default."""
        uri = self.mongodb_uri
        # Extract database name from URI if present, otherwise use default
        if '/nvr_database' in uri or '/nvr_interface' in uri:
            # Try to extract from URI
            parts = uri.split('/')
            if len(parts) > 3:
                db_part = parts[-1].split('?')[0]
                if db_part:
                    return db_part
        return 'nvr_database'

    @property
    def cameras(self):
        return self._config.get('cameras', [])

    # Add other config properties as needed

# Global config instance
config = Config()
