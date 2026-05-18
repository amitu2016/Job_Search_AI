"""Score a job listing against the resume using OpenAI gpt-4o."""

import json

from openai import AsyncOpenAI

from portals.base import Job

SYSTEM_PROMPT = """You are a job fit evaluator for a senior Java backend engineer with ~8 years of experience.
Evaluate the fit between the resume and the job listing, then return a JSON object with exactly these keys:
  score   — integer 0 to 100
  reason  — one sentence explaining the score
  apply   — boolean, true if score >= 72

Scoring guidance:
- Core match: Java, Spring Boot, microservices, Kubernetes, Kafka, distributed systems (strong weight)
- Seniority: target Senior / Lead / Staff / Principal engineer roles; score junior roles low
- Location: India-based (Delhi/Noida/Gurugram preferred, other cities acceptable) or remote
- Salary: minimum 30 LPA; if the role appears junior or low-paying, reduce score accordingly
- Leadership and system design experience is a positive signal

Return only valid JSON. No markdown, no explanation outside the JSON object."""


async def score_job(job: Job, resume_text: str, openai_key: str) -> tuple[int, str, bool]:
    """Return (score 0-100, reason, apply bool) for a job."""
    client = AsyncOpenAI(api_key=openai_key)

    user_content = (
        f"RESUME:\n{resume_text}\n\n"
        f"JOB TITLE: {job.title}\n"
        f"COMPANY: {job.company}\n"
        f"LOCATION: {job.location}\n"
        f"SALARY: {job.salary or 'not specified'}\n"
        f"DESCRIPTION:\n{job.description}"
    )

    resp = await client.chat.completions.create(
        model="gpt-4o",
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ],
        temperature=0.1,
    )

    data = json.loads(resp.choices[0].message.content)
    score = max(0, min(100, int(data.get("score", 0))))
    reason = str(data.get("reason", ""))
    apply = bool(data.get("apply", score >= 72))
    return score, reason, apply
