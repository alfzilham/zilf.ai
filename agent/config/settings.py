"""
Centralized settings â€” single source of truth for all configuration.

Loads values from (in priority order):
  1. Environment variables  (highest priority)
  2. .env file              (via python-dotenv)
  3. config/*.yaml files    (base defaults)

Usage::

    from agent.config.settings import settings

    print(settings.groq_api_key)
    print(settings.agent.max_steps)
    print(settings.sandbox.memory_limit)
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


# ---------------------------------------------------------------------------
# Sub-models (nested config sections)
# ---------------------------------------------------------------------------


class AgentSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="AGENT_", extra="ignore")

    llm_provider: str = "ollama"
    model: str = "deepseek-coder"
    max_steps: int = 30
    tool_timeout: int = 30
    use_planner: bool = True
    verbose: bool = True
    temperature: float = 0.0
    max_tokens: int = 4096
    enabled_tools: list[str] = Field(default_factory=lambda: [
        "read_file", "write_file", "list_dir", "search_files",
        "delete_file", "run_command", "run_code", "web_search",
    ])


class SandboxSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="SANDBOX_", extra="ignore")

    image: str = "zilf-ai-sandbox:latest"
    cpu_limit: str = "1.0"
    memory_limit: str = "512m"
    pids_limit: int = 64
    execution_timeout: int = 30
    network_mode: str = "none"
    auto_remove: bool = True
    reuse_container: bool = False


class LLMProviderSettings(BaseSettings):
    """Settings for a single LLM provider."""
    default_model: str = ""
    max_tokens: int = 4096
    temperature: float = 0.0
    base_url: str = ""


class MemorySettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="MEMORY_", extra="ignore")

    short_term_max: int = 50
    enable_long_term: bool = True
    persist_dir: str = "./chroma_db"
    recall_top_k: int = 3


# ---------------------------------------------------------------------------
# Root settings
# ---------------------------------------------------------------------------


class Settings(BaseSettings):
    """
    Root settings object. Import and use `settings` singleton below.
    All values can be overridden by environment variables.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # --- API Keys ---
    groq_api_key: SecretStr = Field(default=SecretStr(""), alias="GROQ_API_KEY")
    google_api_key: SecretStr = Field(default=SecretStr(""), alias="GOOGLE_API_KEY")
    tavily_api_key: SecretStr = Field(default=SecretStr(""), alias="TAVILY_API_KEY")
    hf_token: SecretStr = Field(default=SecretStr(""), alias="HF_TOKEN")
    zilf_max_api_key: SecretStr | None = None
    qwen_api_key: SecretStr | None = None
    together_api_key: SecretStr | None = None

    # --- Ollama ---
    ollama_base_url: str = Field("http://localhost:11434", alias="OLLAMA_BASE_URL")

    # --- Log level ---
    log_level: str = Field("INFO", alias="LOG_LEVEL")

    # --- Sub-models (loaded from YAML, overridable via env) ---
    agent: AgentSettings = Field(default_factory=AgentSettings)
    sandbox: SandboxSettings = Field(default_factory=SandboxSettings)
    memory: MemorySettings = Field(default_factory=MemorySettings)

    # -----------------------------------------------------------------------
    # Validators
    # -----------------------------------------------------------------------

    @field_validator("log_level")
    @classmethod
    def validate_log_level(cls, v: str) -> str:
        allowed = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
        upper = v.upper()
        if upper not in allowed:
            raise ValueError(f"log_level must be one of {allowed}, got {v!r}")
        return upper

    # -----------------------------------------------------------------------
    # Helpers
    # -----------------------------------------------------------------------

    def get_api_key(self, provider: str) -> str:
        """Return the API key string for the given provider name."""
        mapping = {
            "groq": self.groq_api_key,
            "google": self.google_api_key,
            "tavily": self.tavily_api_key,
        }
        secret = mapping.get(provider.lower())
        return secret.get_secret_value() if secret else ""

    def has_api_key(self, provider: str) -> bool:
        return bool(self.get_api_key(provider))

    def active_providers(self) -> list[str]:
        """Return a list of providers that have API keys configured."""
        candidates = ["groq", "google", "ollama"]
        return [p for p in candidates if self.has_api_key(p)]

    def __repr__(self) -> str:
        providers = self.active_providers()
        return f"Settings(provider={self.agent.llm_provider!r}, active_keys={providers})"


# ---------------------------------------------------------------------------
# YAML loader â€” merges YAML defaults under the Settings model
# ---------------------------------------------------------------------------


def _load_yaml_config(config_dir: Path) -> dict[str, Any]:
    """Load and merge all YAML config files from config/ directory."""
    merged: dict[str, Any] = {}
    for fname in ["agent_config.yaml", "sandbox_config.yaml", "logging_config.yaml"]:
        path = config_dir / fname
        if path.exists():
            with open(path) as f:
                data = yaml.safe_load(f) or {}
            merged.update(data)
    return merged


def _find_config_dir() -> Path:
    """Walk up from cwd to find the config/ directory."""
    current = Path.cwd()
    for parent in [current, *current.parents]:
        candidate = parent / "config"
        if candidate.is_dir():
            return candidate
    return Path("config")  # fallback


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """
    Return the cached Settings singleton.

    Called once at import time by `settings = get_settings()` below.
    Use `get_settings.cache_clear()` in tests to reload.
    """
    # Load YAML defaults first
    config_dir = _find_config_dir()
    yaml_data = _load_yaml_config(config_dir)

    # Extract sub-sections for sub-models
    agent_yaml = yaml_data.get("agent", {})
    sandbox_yaml = yaml_data.get("sandbox", {})
    memory_yaml = yaml_data.get("memory", {})

    # Build sub-models (env vars override YAML via pydantic-settings)
    agent_settings = AgentSettings(**agent_yaml)
    sandbox_settings = SandboxSettings(**sandbox_yaml)
    memory_settings = MemorySettings(**memory_yaml)

    return Settings(
        agent=agent_settings,
        sandbox=sandbox_settings,
        memory=memory_settings,
    )


# Module-level singleton â€” import this everywhere
settings: Settings = get_settings()
