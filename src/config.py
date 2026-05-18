"""Load configuration from config.yaml and secrets from SSM (prod) or .env (local)."""

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml
from dotenv import load_dotenv

ROOT = Path(__file__).parent.parent

load_dotenv(ROOT / ".env")


@dataclass
class ApplyConfig:
    min_match_score: int = 72
    max_applications_per_run: int = 15
    delay_between_apply_sec: int = 45
    cover_note: bool = True
    cover_note_max_words: int = 150
    min_salary_lpa: int = 30


@dataclass
class SearchConfig:
    keywords: list[str] = field(default_factory=list)
    locations: list[str] = field(default_factory=list)
    experience_years: int = 8
    max_pages_per_portal: int = 5
    max_jobs_per_location: int = 10
    exclude_companies: list[str] = field(default_factory=list)


@dataclass
class PortalsConfig:
    naukri: bool = True


@dataclass
class ProfileConfig:
    current_ctc_lpa: int = 27
    expected_ctc_lpa: int = 30
    notice_period: str = "2 Months"
    current_location: str = "Mumbai"
    total_experience_years: int = 8


@dataclass
class NotifyConfig:
    send_on_empty_run: bool = False


@dataclass
class AppConfig:
    search: SearchConfig
    portals: PortalsConfig
    apply: ApplyConfig
    profile: ProfileConfig
    notify: NotifyConfig
    resume_text: str = ""


def _ssm_get(key: str) -> str:
    """Fetch from AWS SSM; fall back to env var (for local dev)."""
    env_key = key.lstrip("/").replace("/", "_").replace("-", "_").upper()
    env_val = os.getenv(env_key)
    if env_val:
        return env_val
    try:
        import boto3
        ssm = boto3.client("ssm", region_name=os.getenv("AWS_REGION", "ap-south-1"))
        resp = ssm.get_parameter(Name=key, WithDecryption=True)
        return resp["Parameter"]["Value"]
    except Exception:
        return ""


@dataclass
class Secrets:
    naukri_user: str
    naukri_pass: str
    openai_key: str
    telegram_bot_token: str
    telegram_chat_id: str


def load_secrets() -> Secrets:
    return Secrets(
        naukri_user=_ssm_get("/job-agent/naukri_user"),
        naukri_pass=_ssm_get("/job-agent/naukri_pass"),
        openai_key=_ssm_get("/job-agent/openai_key"),
        telegram_bot_token=_ssm_get("/job-agent/telegram_bot_token"),
        telegram_chat_id=_ssm_get("/job-agent/telegram_chat_id"),
    )


def load_config() -> AppConfig:
    raw = yaml.safe_load((ROOT / "config.yaml").read_text())

    s = raw["search"]
    search = SearchConfig(
        keywords=s["keywords"],
        locations=s["locations"],
        experience_years=s.get("experience_years", 8),
        max_pages_per_portal=s.get("max_pages_per_portal", 5),
        max_jobs_per_location=s.get("max_jobs_per_location", 10),
        exclude_companies=s.get("exclude_companies", []),
    )

    p = raw["portals"]
    portals = PortalsConfig(
        naukri=p.get("naukri", {}).get("enabled", True),
    )

    a = raw["apply"]
    apply_cfg = ApplyConfig(
        min_match_score=a.get("min_match_score", 72),
        max_applications_per_run=a.get("max_applications_per_run", 15),
        delay_between_apply_sec=a.get("delay_between_apply_sec", 45),
        cover_note=a.get("cover_note", True),
        cover_note_max_words=a.get("cover_note_max_words", 150),
        min_salary_lpa=a.get("min_salary_lpa", 30),
    )

    pr = raw.get("profile", {})
    profile = ProfileConfig(
        current_ctc_lpa=pr.get("current_ctc_lpa", 27),
        expected_ctc_lpa=pr.get("expected_ctc_lpa", 30),
        notice_period=pr.get("notice_period", "2 Months"),
        current_location=pr.get("current_location", "Mumbai"),
        total_experience_years=pr.get("total_experience_years", 8),
    )

    n = raw.get("notify", {})
    notify = NotifyConfig(
        send_on_empty_run=n.get("send_on_empty_run", False),
    )

    resume_text = (ROOT / "data" / "resume.txt").read_text().strip()

    return AppConfig(
        search=search,
        portals=portals,
        apply=apply_cfg,
        profile=profile,
        notify=notify,
        resume_text=resume_text,
    )
