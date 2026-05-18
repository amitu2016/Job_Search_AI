"""Generate a tailored cover note for a job application using OpenAI gpt-4o."""

from openai import AsyncOpenAI

from portals.base import Job

SYSTEM_PROMPT = """You write concise, genuine cover notes for job applications.
Rules:
- Maximum 150 words
- No generic filler ("I am excited to apply...", "I believe I am a perfect fit...")
- Highlight exactly 2-3 specific skills or achievements from the resume relevant to THIS role
- Name the company and role explicitly
- Close with one sentence of genuine interest in the specific problem the company solves
- Professional tone, first person, no bullet points
Return only the cover note text. No subject line, no salutation, no sign-off."""


async def generate(job: Job, resume_text: str, openai_key: str, max_words: int = 150) -> str:
    client = AsyncOpenAI(api_key=openai_key)

    resp = await client.chat.completions.create(
        model="gpt-4o",
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": (
                    f"RESUME:\n{resume_text}\n\n"
                    f"ROLE: {job.title} at {job.company}\n"
                    f"LOCATION: {job.location}\n"
                    f"JOB DESCRIPTION:\n{job.description}\n\n"
                    f"Write a cover note under {max_words} words."
                ),
            },
        ],
        temperature=0.4,
        max_tokens=300,
    )

    return resp.choices[0].message.content.strip()
