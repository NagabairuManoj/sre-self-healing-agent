import os
import logging
from typing import Optional
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# Configure Logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("sre-agent-core.config")

class Settings(BaseSettings):
    github_token: str = Field("", validation_alias="GITHUB_TOKEN")
    github_webhook_secret: Optional[str] = Field(None, validation_alias="GITHUB_WEBHOOK_SECRET")
    gemini_api_key: Optional[str] = Field(None, validation_alias="GEMINI_API_KEY")
    
    # Database, OAuth, and JWT Settings
    database_url: str = Field("postgresql://sre_user:sre_password@db:5432/sre_agent_db", validation_alias="DATABASE_URL")
    github_client_id: str = Field("", validation_alias="GITHUB_CLIENT_ID")
    github_client_secret: str = Field("", validation_alias="GITHUB_CLIENT_SECRET")
    jwt_secret: str = Field("sre-agent-super-secret-jwt-key", validation_alias="JWT_SECRET")
    
    # Optional settings for runner and server configuration
    port: int = Field(8000, validation_alias="PORT")
    host: str = Field("0.0.0.0", validation_alias="HOST")
    
    # Path where git repositories will be cloned temporarily for editing
    workspace_dir: str = Field("/tmp/sre-agent-workspace", validation_alias="WORKSPACE_DIR")
    
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )

    # Dynamic fallback to os.environ to handle pytest module caching and runtime changes
    @property
    def github_token_val(self) -> str:
        return self.__dict__.get("github_token", "") or os.environ.get("GITHUB_TOKEN", "")

    @property
    def github_webhook_secret_val(self) -> Optional[str]:
        return self.__dict__.get("github_webhook_secret") or os.environ.get("GITHUB_WEBHOOK_SECRET")

    @property
    def gemini_api_key_val(self) -> Optional[str]:
        return self.__dict__.get("gemini_api_key") or os.environ.get("GEMINI_API_KEY")

settings = Settings()

# Override properties to allow transparent fallback lookup
class ActiveSettings:
    def __init__(self, s: Settings):
        self._s = s

    @property
    def github_token(self) -> str:
        return self._s.github_token_val

    @property
    def github_webhook_secret(self) -> Optional[str]:
        return self._s.github_webhook_secret_val

    @property
    def gemini_api_key(self) -> Optional[str]:
        return self._s.gemini_api_key_val

    @property
    def database_url(self) -> str:
        return self._s.database_url

    @property
    def github_client_id(self) -> str:
        return self._s.github_client_id

    @property
    def github_client_secret(self) -> str:
        return self._s.github_client_secret

    @property
    def jwt_secret(self) -> str:
        return self._s.jwt_secret

    @property
    def port(self) -> int:
        return self._s.port

    @property
    def host(self) -> str:
        return self._s.host

    @property
    def workspace_dir(self) -> str:
        return self._s.workspace_dir

settings = ActiveSettings(Settings())

# Validate that we have the GITHUB_TOKEN which is critical
if not settings.github_token:
    logger.warning("GITHUB_TOKEN is not set in environment or .env file. GitHub operations will fail.")
