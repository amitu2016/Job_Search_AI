"""Shared types and abstract base class for all job portal adapters."""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum


class ApplyStatus(str, Enum):
    APPLIED = "applied"
    SKIPPED = "skipped"           # below score threshold or at limit
    FAILED = "failed"             # automation error
    CAPTCHA = "captcha"           # CAPTCHA detected
    ALREADY_APPLIED = "already_applied"
    MANUAL_REQUIRED = "manual_required"  # external ATS redirect — apply manually
    DRY_RUN = "dry_run"


@dataclass
class Job:
    url: str
    title: str
    company: str
    location: str
    description: str
    portal: str
    job_id: str = ""            # portal-specific ID if available
    salary: str = ""
    posted_at: str = ""
    score: int = 0
    score_reason: str = ""

    @property
    def key(self) -> str:
        """Normalised dedup key — strip query params, lowercase."""
        from urllib.parse import urlparse, urlunparse
        p = urlparse(self.url)
        return urlunparse((p.scheme, p.netloc, p.path, "", "", "")).lower()


@dataclass
class ApplyResult:
    job: Job
    status: ApplyStatus
    error: str = ""
    applied_at: datetime = field(default_factory=datetime.utcnow)


class Portal(ABC):
    """Abstract base for all portal adapters.

    Use as an async context manager so the browser session stays alive
    across search() and apply() calls:

        async with NaukriPortal(config, secrets) as portal:
            jobs = await portal.search()
            result = await portal.apply(job, cover_note)
    """

    name: str = ""

    async def __aenter__(self):
        await self.open()
        return self

    async def __aexit__(self, *args):
        await self.close()

    async def open(self) -> None:
        """Start browser session and log in. Override in subclasses."""

    async def close(self) -> None:
        """Tear down browser session. Override in subclasses."""

    @abstractmethod
    async def search(self) -> list[Job]:
        """Return list of jobs matching configured keywords and locations."""
        ...

    @abstractmethod
    async def apply(self, job: Job, cover_note: str) -> ApplyResult:
        """Apply to a single job. Return result with status."""
        ...
