from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "Autonomous Job Agent"
    database_path: str = "./data/agent.db"
    log_level: str = "INFO"
    groq_api_base: str = "https://api.groq.com/openai/v1"
    groq_model: str = "openai/gpt-oss-120b"
    groq_api_key: str = ""
    llm_max_requests_per_minute: int = 3
    llm_max_requests_per_day: int = 60
    llm_max_retries: int = 2
    llm_timeout_seconds: float = 25
    llm_cache_ttl_seconds: int = 604800
    llm_enabled: bool = True
    adzuna_app_id: str = ""
    adzuna_app_key: str = ""
    job_country: str = "in"
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_username: str = ""
    smtp_password: str = ""
    email_from: str = ""
    email_to: str = ""
    smtp_starttls: bool = True
    llm_rank_top_n: int = 3
    default_job_location: str = "India"
    default_job_query: str = "agentic AI engineer OR generative AI engineer OR GenAI developer"
    daily_report_hour: int = 18
    discovery_interval_hours: int = 24
    scheduler_enabled: bool = False
    ranking_worker_enabled: bool = False
    ranking_worker_interval_seconds: int = 65

    # One-click workflow limits: avoid spending LLM quota on every listing.
    auto_tailor_enabled: bool = True
    auto_tailor_top_n: int = 3
    auto_tailor_min_score: int = 65
    json_exports_enabled: bool = True

    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )


settings = Settings()
