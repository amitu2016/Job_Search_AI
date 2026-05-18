# Job Search AI Agent — Plan

## Goal

Automated agent running on EC2 on a fixed schedule (twice daily by default) that:
- Searches multiple job portals and company career pages for matching roles
- Scores each job against resume using Claude API
- Auto-applies on your behalf with a Claude-generated cover note
- Sends an email digest after each run

---

## Job Sources

### Aggregator Portals (login + search + apply)
| Portal     | Apply Method       | Notes                              |
|------------|--------------------|------------------------------------|
| Naukri.com | Easy Apply         | Largest Indian portal              |
| LinkedIn   | Easy Apply         | Broad reach, good for senior roles |
| Instahyre  | Direct Apply       | Curated, good for tech roles       |
| Indeed IN  | Easy Apply         | High volume                        |

### Direct Company Career Portals (India locations)
| Company         | ATS Used  | Career URL                              |
|-----------------|-----------|-----------------------------------------|
| J.P. Morgan     | Workday   | jpmc.com/careers                        |
| Goldman Sachs   | Workday   | goldmansachs.com/careers                |
| Mastercard      | Workday   | mastercard.com/global/en/business/issuers/careers |
| Visa            | Workday   | visa.com/careers                        |
| Morgan Stanley  | Taleo     | morganstanley.com/about-us/careers      |
| Citi            | Workday   | jobs.citi.com                           |
| American Express| Workday   | americanexpress.com/en-us/careers       |

> Most banks use **Workday** — one Workday adapter covers the majority.
> Add more portals by dropping a new adapter file in `src/portals/`.

---

## Architecture

```
EC2 (systemd timer — 9am + 6pm IST)
    └── agent.py  (orchestrator)
            ├── portals/
            │     naukri.py        — search + apply (Playwright)
            │     linkedin.py      — Easy Apply (Playwright)
            │     instahyre.py     — search + apply (Playwright)
            │     indeed.py        — search + apply (Playwright)
            │     workday.py       — generic Workday adapter (search + apply)
            │     taleo.py         — generic Taleo adapter
            │     base.py          — abstract Portal interface
            ├── matcher.py          — Claude API: score job vs resume
            ├── cover_note.py       — Claude API: generate tailored cover note
            ├── tracker.py          — SQLite: dedup, status, history
            └── notifier.py         — email digest (AWS SES)
```

Each portal implements the same `Portal` interface:
```python
class Portal:
    def search(self) -> list[Job]: ...
    def apply(self, job: Job, cover_note: str) -> ApplyResult: ...
```

---

## Data Flow (per run)

```
1. For each enabled portal:
      search() → raw job list

2. Dedup against DB (skip already seen URLs)

3. For each new job:
      Claude scores job description vs resume → score (0-100) + reason

4. For jobs with score >= min_match_score:
      Claude generates cover note (role-specific, 150 words max)
      portal.apply(job, cover_note)
      Mark result in DB (applied / failed / skipped)

5. Send email digest
```

---

## Directory Layout

```
Job_Search_AI/
├── plan/
│   └── plan.md
├── data/
│   resume.txt               # plain text resume (committed)
│   jobs.db                  # runtime SQLite DB (gitignored)
├── src/
│   agent.py                 # orchestrator entry point
│   matcher.py               # Claude: score job fit
│   cover_note.py            # Claude: write cover note
│   tracker.py               # SQLite helpers
│   notifier.py              # email digest
│   config.py                # load config.yaml + secrets from SSM
│   portals/
│       __init__.py
│       base.py              # abstract Portal class + Job / ApplyResult types
│       naukri.py
│       linkedin.py
│       instahyre.py
│       indeed.py
│       workday.py           # covers JPMC, Goldman, Mastercard, Visa, Citi, Amex
│       taleo.py             # covers Morgan Stanley
├── config.yaml              # preferences (committed, no secrets)
├── .env.example             # template — never commit real creds
├── pyproject.toml
├── run.sh                   # EC2 entry point
└── systemd/
    job-agent.service
    job-agent.timer
```

---

## Config Schema (`config.yaml`)

```yaml
search:
  keywords:
    - "Senior Software Engineer"
    - "Backend Engineer"
    - "Python Engineer"
    - "Staff Engineer"
  locations:
    - "Bengaluru"
    - "Mumbai"
    - "Remote"
    - "Hyderabad"
  experience_years: 8
  max_pages_per_portal: 5

portals:
  naukri:    { enabled: true }
  linkedin:  { enabled: true }
  instahyre: { enabled: true }
  indeed:    { enabled: true }
  workday_companies:
    - { name: "JPMC",          url: "https://jpmc.fa.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1001" }
    - { name: "Goldman Sachs", url: "https://hdpc.fa.us2.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX" }
    - { name: "Mastercard",    url: "https://mastercard.wd1.myworkdayjobs.com/CorporateCareers" }
    - { name: "Visa",          url: "https://jobs.smartrecruiters.com/Visa/" }
    - { name: "Citi",          url: "https://jobs.citi.com" }
    - { name: "Amex",          url: "https://aexp.eightfold.ai/careers" }
  taleo_companies:
    - { name: "Morgan Stanley", url: "https://ms.taleo.net/careersection/2/jobsearch.ftl" }

apply:
  min_match_score: 70          # 0-100
  max_applications_per_run: 15
  delay_between_apply_sec: 45  # randomised ±15s
  cover_note: true
  cover_note_max_words: 150

schedule:
  runs_per_day: 2              # 9am + 6pm IST

notify:
  to_email: "amitu2016@gmail.com"
```

---

## Secrets (never in code or committed config)

Stored in **AWS SSM Parameter Store** under `/job-agent/`:

| SSM Key                    | Value                    |
|----------------------------|--------------------------|
| `/job-agent/naukri_user`   | Naukri username/email    |
| `/job-agent/naukri_pass`   | Naukri password          |
| `/job-agent/linkedin_user` | LinkedIn email           |
| `/job-agent/linkedin_pass` | LinkedIn password        |
| `/job-agent/instahyre_user`| Instahyre email          |
| `/job-agent/instahyre_pass`| Instahyre password       |
| `/job-agent/indeed_user`   | Indeed email             |
| `/job-agent/indeed_pass`   | Indeed password          |
| `/job-agent/smtp_user`     | SES SMTP user            |
| `/job-agent/smtp_pass`     | SES SMTP password        |
| `/job-agent/openai_key`    | OpenAI API key           |

EC2 instance has IAM role with `ssm:GetParameter` on `/job-agent/*`.

---

## AI Usage (OpenAI)

**Model:** `gpt-4o` for both matching and cover note generation.
OpenAI automatically caches repeated prompt prefixes (the resume), so cost stays low across many calls per run.

### Job Matching (`matcher.py`)
```
System: You are a job fit evaluator. Given a resume and job description,
        return JSON: { "score": 0-100, "reason": "one sentence", "apply": true/false }
        Score >= 72 means apply.

User:   RESUME: <resume text>
        JOB: <title, company, description>
```

### Cover Note (`cover_note.py`)
```
System: Write a concise, genuine cover note (max 150 words) for a job application.
        Match tone to the company. No filler phrases. Highlight 2-3 relevant points
        from the resume. End with enthusiasm for the role.

User:   RESUME: <resume text>
        ROLE: <title at company>
        JOB DESCRIPTION: <description>
```

---

## Key Risks & Mitigations

| Risk | Mitigation |
|------|------------|
| Portal blocks automation | Randomise delays ±15s, realistic user-agent, slow scroll, no burst |
| UI changes break selectors | Each portal isolated; screenshot-on-failure saved to `/tmp/screenshots/` |
| Apply to wrong job | Claude scoring gate (≥70) + dry-run mode for testing |
| Apply twice to same job | Dedup by normalized job URL in SQLite |
| Credentials leaked | SSM only; `.env` gitignored; `.env.example` has no real values |
| Workday CAPTCHA | Detect CAPTCHA, mark job as `manual_required`, include in digest |
| EC2 runs out of memory | t3.small (2GB); Chromium uses ~350MB; one browser instance at a time |

---

## Build Phases

### Phase 1 — Foundation `COMPLETE` ✓ (2026-05-18)
- [x] `uv init`, `pyproject.toml`, directory scaffold
- [x] `data/resume.txt` — resume saved (Amit Kumar Upadhyay, Java/Spring Boot, 8yr)
- [x] `config.yaml` — keywords, locations (Delhi/Noida/Gurugram preferred), exclude HDFC+CDAC, min_salary 30 LPA, min_score 72
- [x] `src/portals/base.py` — `Portal` ABC, `Job` dataclass, `ApplyResult`, `ApplyStatus`
- [x] `src/tracker.py` — SQLite schema (`jobs` + `runs` tables), dedup by normalised URL
- [x] `src/config.py` — `load_config()` + `load_secrets()` (SSM with `.env` fallback)
- [x] `.env.example`, `.gitignore`
- [x] Smoke-tested: config loads, DB initialises, all portals + companies registered

### Phase 2 — Naukri Portal + OpenAI Matcher `COMPLETE` ✓ (2026-05-18)
- [x] `src/matcher.py` — OpenAI gpt-4o scoring (score 0-100, reason, apply bool)
- [x] `src/portals/naukri.py` — Playwright login + search + paginate + extract jobs
- [x] `src/agent.py` — orchestrator: search → dedup → score → log, `--dry-run` + `--test` flags
- [x] Root-caused Akamai TLS block on headless Chromium → fixed with `headless=False`
       Note: On EC2 pair with Xvfb virtual display (documented in Phase 7)
- [x] Duplicate-page detection: breaks pagination when Naukri loops back to pg1
- [x] Dry-run validated: 20 jobs found, 18 APPLY / 2 SKIP — scoring correct
- [x] Pune added to locations list

### Phase 3 — Cover Note + Naukri Apply `COMPLETE` ✓ (2026-05-18)
- [x] `src/cover_note.py` — OpenAI gpt-4o cover note (role-specific, ≤150 words)
- [x] NaukriPortal refactored to async context manager — one browser session for search + apply
- [x] `apply()` implemented: click Apply → fill form fields → submit
- [x] `_fill_apply_form()` / `_fill_naukri_known_fields()` — CTC, notice period, experience, location
- [x] External ATS redirects → `MANUAL_REQUIRED` status (tracked in DB, shown in digest)
- [x] `profile` section added to `config.yaml` (current_ctc=27, notice=2 Months, location=Mumbai)
- [x] `MANUAL_REQUIRED` added to `ApplyStatus` enum
- [x] `src/form_filler.py` — AI form filler for native HTML fields (text, select, radio, checkbox)
- [x] `_handle_chatbot()` — handles Naukri's chatbot_Drawer apply widget (class=chatbot_Drawer chatbot_right)
       - Consent radio (I Accept via SmartRecruiters) accepted via label click
       - Save button clicked via JavaScript (bypasses viewport/scroll issues with 1920×1080)
       - Multi-step loop with Skip fallback for unknown questions
- [x] Viewport changed to 1920×1080 so apply panel Save button is in viewport
- [x] Live apply test confirmed: Anaplan applied successfully (green "Applied to" confirmation)

### Phase 4 — Notifications `COMPLETE` ✓ (2026-05-18)
- [x] `src/notifier.py` — Telegram digest after each run (applied / failed / manual counts + job list)
- [x] Bot token + chat ID via SSM / `.env` — tested, message received

### Phase 5 — EC2 Deploy
- [ ] `run.sh` — EC2 entry point (activate venv, run agent, log to file)
- [ ] `systemd/job-agent.service` + `job-agent.timer` (9am + 6pm IST)
- [ ] EC2 setup script (Amazon Linux 2, install uv + Playwright + Chromium)
- [ ] Load all secrets into SSM Parameter Store
- [ ] IAM role with `ssm:GetParameter` on `/job-agent/*`
- [ ] Enable timer, verify first live run

---

## Credentials Needed

| Credential | Status | Needed for |
|------------|--------|-----------|
| Naukri email + password | pending | Phase 2 |
| OpenAI API key | pending | Phase 2 |
| LinkedIn email + password | pending | Phase 4 |
| Instahyre email + password | pending | Phase 4 |
| Indeed email + password | pending | Phase 4 |
| AWS account (EC2 + SSM + SES) | pending | Phase 6–7 |
