import os
from dataclasses import dataclass
from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class AppConfig:
    # OpenAI
    openai_api_key: str = os.getenv("OPENAI_API_KEY", "").strip()
    openai_model: str = os.getenv("OPENAI_MODEL", "gpt-5.1").strip()
    use_openai_api: bool = os.getenv("USE_OPENAI_API", "true").strip().lower() == "true"

    # Persona & style
    quirky_mode: bool = os.getenv("QUIRKY_MODE", "false").strip().lower() == "true"
    response_style: str = os.getenv("RESPONSE_STYLE", "summarized").strip().lower()

    # VE Requirements API
    requirements_api_base: str = os.getenv(
        "VE_REQUIREMENTS_API_BASE",
        "https://d4u8k6edt3njs.cloudfront.net/api/public/requirements/v1",
    ).rstrip("/")
    requirements_api_key: str = os.getenv("VE_REQUIREMENTS_API_KEY", "").strip()
    requirements_limit: int = int(os.getenv("VE_REQUIREMENTS_LIMIT", "10"))

    # VE Bench API
    bench_api_url: str = os.getenv(
        "VE_BENCH_API_URL",
        "https://d4u8k6edt3njs.cloudfront.net/api/public-ppc/",
    ).strip()
    bench_api_key: str = os.getenv("VE_BENCH_API_KEY", "").strip()
    verify_ssl: bool = os.getenv("VERIFY_SSL", "true").strip().lower() == "true"

    # Matching & Budget Tiers
    budget_low_max_yoe: float = float(os.getenv("BUDGET_LOW_MAX_YOE", "3.0"))
    budget_mid_max_yoe: float = float(os.getenv("BUDGET_MID_MAX_YOE", "8.0"))
    top_matches_limit: int = int(os.getenv("TOP_MATCHES_LIMIT", "5"))
    source_review_token: str = os.getenv("SOURCE_REVIEW_TOKEN", "").strip()

config = AppConfig()