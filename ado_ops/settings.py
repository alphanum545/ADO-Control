"""Application settings."""

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Environment-backed settings for the standalone ADO console."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    ado_org: str = Field(..., description="Azure DevOps organization name")
    ado_pat: str = Field(..., description="Azure DevOps PAT")

    llm_api_key: str = Field(..., description="OpenAI-compatible API key")
    llm_base_url: str = Field(default="https://api.openai.com/v1")
    llm_model: str = Field(default="gpt-4o")
    llm_temperature: float = Field(default=0.0, ge=0.0, le=2.0)

    default_work_item_assignee: str = Field(default="silaparasetti.lohith@maqsoftware.com")
    default_project_process: str = Field(default="Agile")
    default_project_visibility: str = Field(default="private")


settings = Settings()

