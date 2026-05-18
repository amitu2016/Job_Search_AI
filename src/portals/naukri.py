"""Naukri.com portal adapter — login, search, and apply via Playwright."""

import asyncio
import random
from pathlib import Path
from urllib.parse import urlencode

from playwright.async_api import BrowserContext, Page, async_playwright

import form_filler
from portals.base import ApplyResult, ApplyStatus, Job, Portal

SCREENSHOT_DIR = Path("/tmp/screenshots")
SEARCH_BASE = "https://www.naukri.com/jobs-in-india"

_JOB_CARD_SELECTOR = (
    "article.jobTuple, "
    ".srp-jobtuple-wrapper, "
    "[class*='jobTuple'], "
    "[class*='job-tuple']"
)

# Apply button selectors tried in order on the job detail page
_APPLY_BTN_SELECTORS = [
    "button#apply-button",
    "button.apply-button",
    "a.apply-button",
    "button:has-text('Apply Now')",
    "button:has-text('Easy Apply')",
    "button:has-text('Apply')",
    "a:has-text('Apply Now')",
]

# Cover note textarea selectors inside the apply modal
_COVER_NOTE_SELECTORS = [
    "textarea[name='message']",
    "textarea[placeholder*='cover']",
    "textarea[placeholder*='Cover']",
    "textarea[placeholder*='message']",
    "textarea[placeholder*='Message']",
    "textarea[placeholder*='note']",
    "[class*='cover'] textarea",
    "[class*='message'] textarea",
]

# Success indicators after submitting application
_SUCCESS_SELECTORS = [
    "[class*='success']",
    "[class*='Success']",
    ":has-text('Application Submitted')",
    ":has-text('Successfully Applied')",
    ":has-text('applied successfully')",
]


class NaukriPortal(Portal):
    name = "naukri"

    def __init__(self, config, secrets):
        self.config = config
        self.secrets = secrets
        self._pw = None
        self._browser = None
        self._ctx: BrowserContext | None = None
        self._page: Page | None = None
        SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Session lifecycle
    # ------------------------------------------------------------------

    async def open(self) -> None:
        self._pw = await async_playwright().start()
        self._browser = await self._pw.chromium.launch(
            headless=False,
            args=["--disable-blink-features=AutomationControlled", "--no-sandbox"],
        )
        self._ctx = await self._browser.new_context(
            viewport={"width": 1920, "height": 1080},
            locale="en-IN",
            timezone_id="Asia/Kolkata",
        )
        self._page = await self._ctx.new_page()
        self._page.set_default_timeout(30_000)
        await self._login(self._page)

    async def close(self) -> None:
        if self._browser:
            await self._browser.close()
        if self._pw:
            await self._pw.stop()

    # ------------------------------------------------------------------
    # Search
    # ------------------------------------------------------------------

    async def search(self) -> list[Job]:
        assert self._page, "Call open() or use as async context manager first"
        seen_keys: set[str] = set()
        jobs: list[Job] = []

        exp_min = max(0, self.config.search.experience_years - 3)
        exp_max = self.config.search.experience_years + 1

        for keyword in self.config.search.keywords:
            for location in self.config.search.locations:
                found = await self._search_one(self._page, keyword, location, exp_min, exp_max)
                for job in found:
                    if job.key not in seen_keys:
                        seen_keys.add(job.key)
                        jobs.append(job)
                await asyncio.sleep(random.uniform(3, 6))

        return jobs

    # ------------------------------------------------------------------
    # Apply
    # ------------------------------------------------------------------

    async def apply(self, job: Job, cover_note: str) -> ApplyResult:
        assert self._ctx, "Call open() or use as async context manager first"
        page = await self._ctx.new_page()
        try:
            # Hard 90-second wall clock limit per application — never hangs
            return await asyncio.wait_for(
                self._do_apply(page, job, cover_note),
                timeout=90,
            )
        except asyncio.TimeoutError:
            await self._screenshot(page, f"timeout_{job.company[:20]}")
            return ApplyResult(job=job, status=ApplyStatus.FAILED, error="Apply timed out after 90s")
        finally:
            await page.close()

    async def _do_apply(self, page: Page, job: Job, cover_note: str) -> ApplyResult:
        page.set_default_timeout(8_000)

        try:
            await page.goto(job.url, wait_until="domcontentloaded", timeout=20000)
        except Exception as e:
            return ApplyResult(job=job, status=ApplyStatus.FAILED, error=f"Page load failed: {e}")

        await page.wait_for_timeout(2000)

        # Already applied?
        if await page.locator(":has-text('Already Applied'), [class*='alreadyApplied']").count() > 0:
            return ApplyResult(job=job, status=ApplyStatus.ALREADY_APPLIED)

        # Find Apply button
        apply_btn = None
        for sel in _APPLY_BTN_SELECTORS:
            try:
                btn = page.locator(sel).first
                if await btn.is_visible(timeout=2000):
                    apply_btn = btn
                    break
            except Exception:
                continue

        if not apply_btn:
            await self._screenshot(page, f"no_apply_btn_{job.company[:20]}")
            return ApplyResult(job=job, status=ApplyStatus.FAILED, error="Apply button not found")

        # Click Apply — detect if a new tab opens (= external ATS redirect)
        try:
            async with self._ctx.expect_page(timeout=3000) as new_page_info:
                await apply_btn.click()
            new_tab = await new_page_info.value
            await new_tab.close()
            return ApplyResult(
                job=job, status=ApplyStatus.MANUAL_REQUIRED,
                error="Apply opened external tab",
            )
        except Exception:
            pass  # No new tab — good, stayed on naukri

        await page.wait_for_timeout(2000)

        if "naukri.com" not in page.url:
            return ApplyResult(
                job=job, status=ApplyStatus.MANUAL_REQUIRED,
                error=f"External ATS: {page.url}",
            )

        # Handle Naukri chatbot widget (detected by chatbot_Drawer class)
        submitted = await self._handle_chatbot(page, cover_note)

        # Fallback for traditional form (no chatbot)
        if not submitted:
            await form_filler.fill(
                page, self.config.profile, self.config.resume_text, self.secrets.openai_key
            )
            await self._fill_naukri_known_fields(page)
            if cover_note:
                await self._fill_cover_note(page, cover_note)

            for sel in [
                "button:has-text('Submit')",
                "button:has-text('Apply Now')",
                "button:has-text('Confirm')",
                "button[type='submit']",
                "button:has-text('Apply')",
            ]:
                try:
                    btns = page.locator(sel)
                    count = await btns.count()
                    for i in range(count - 1, -1, -1):
                        btn = btns.nth(i)
                        if await btn.is_visible(timeout=800):
                            await btn.click()
                            submitted = True
                            break
                    if submitted:
                        break
                except Exception:
                    continue

        if not submitted:
            await self._screenshot(page, f"no_submit_{job.company[:20]}")
            return ApplyResult(job=job, status=ApplyStatus.FAILED, error="Submit button not found")

        await page.wait_for_timeout(3000)

        for sel in _SUCCESS_SELECTORS:
            try:
                if await page.locator(sel).count() > 0:
                    return ApplyResult(job=job, status=ApplyStatus.APPLIED)
            except Exception:
                continue

        if await page.locator(":has-text('Applied'), [class*='applied']").count() > 0:
            return ApplyResult(job=job, status=ApplyStatus.APPLIED)

        await self._screenshot(page, f"post_apply_{job.company[:20]}")
        return ApplyResult(job=job, status=ApplyStatus.APPLIED)

    async def _handle_chatbot(self, page: Page, cover_note: str) -> bool:
        """Handle Naukri's chatbot_Drawer apply widget. Returns True if handled."""
        CHATBOT = '[class*="chatbot_Drawer"]'
        try:
            if not await page.locator(CHATBOT).count():
                return False
        except Exception:
            return False

        print("[chatbot] chatbot widget detected — waiting for render")
        # Wait for chatbot to fully render before interacting
        await page.wait_for_timeout(3000)

        profile = self.config.profile

        def quick_answer(question: str) -> str | None:
            q = question.lower()
            if "expected ctc" in q or "expected salary" in q:
                return str(profile.expected_ctc_lpa)
            if "current ctc" in q or "current salary" in q:
                return str(profile.current_ctc_lpa)
            if "notice period" in q:
                return "60"  # 2 months ≈ 60 days
            if "experience" in q or "years" in q:
                if any(kw in q for kw in ["angular", "react", "vue", "python",
                                           "node", ".net", "php", "ruby", "swift"]):
                    return "0"
                return str(profile.total_experience_years)
            if "location" in q or "relocate" in q:
                return profile.current_location
            return None

        saved_count = 0
        prev_text = ""

        for step in range(15):
            # Wait for chatbot to render / update after previous action
            await page.wait_for_timeout(2000)

            # Extract question text (retry once if empty — React may still be rendering)
            question = await self._chatbot_text(page, CHATBOT)
            if not question:
                await page.wait_for_timeout(1500)
                question = await self._chatbot_text(page, CHATBOT)
            print(f"[chatbot] step {step}: {question[:150]!r}")

            # Chatbot unchanged after last action → we're done
            if question == prev_text and step > 0:
                print("[chatbot] content unchanged — done")
                break
            prev_text = question

            # 1. Accept consent (I Accept radio/label)
            await self._accept_consent(page)

            # 2. Fill any text/number input inside chatbot
            for sel in [
                f"{CHATBOT} input[type='number']",
                f"{CHATBOT} input[type='text']",
                f"{CHATBOT} textarea",
                f"{CHATBOT} input:not([type='radio']):not([type='checkbox'])",
            ]:
                try:
                    el = page.locator(sel).first
                    if await el.is_visible(timeout=600):
                        answer = quick_answer(question)
                        if answer:
                            await el.click()
                            await el.fill(answer)
                            print(f"[chatbot] filled input: {answer!r}")
                        break
                except Exception:
                    continue

            # 3. Cover note textarea
            if cover_note:
                await self._fill_cover_note(page, cover_note)

            # 4. Click "Save" via JavaScript — bypasses viewport/scroll visibility issues
            saved = await page.evaluate("""() => {
                const btns = Array.from(document.querySelectorAll(
                    'button, [role="button"], div.btn, a.btn'
                ));
                // Prefer visible Save first
                const visible = btns.find(b =>
                    b.textContent.trim() === 'Save' && b.offsetParent !== null
                );
                const any = btns.find(b => b.textContent.trim() === 'Save');
                const target = visible || any;
                if (target) { target.click(); return true; }
                return false;
            }""")
            if saved:
                saved_count += 1
                print(f"[chatbot] clicked Save via JS (total: {saved_count})")
                continue

            # 5. Skip if no answer and no Save
            skipped = await page.evaluate("""() => {
                const btns = Array.from(document.querySelectorAll('button, [role="button"]'));
                const skip = btns.find(b => b.textContent.trim().startsWith('Skip'));
                if (skip) { skip.click(); return true; }
                return false;
            }""")
            if skipped:
                print("[chatbot] skipped question via JS")
                continue

            print(f"[chatbot] no Save/Skip at step {step} — done")
            break

        await self._screenshot(page, f"chatbot_done_{page.url[-30:]}")
        print(f"[chatbot] finished — {saved_count} save(s) clicked")
        return True

    async def _chatbot_text(self, page: Page, chatbot_sel: str) -> str:
        """Extract visible text from the chatbot widget."""
        try:
            return await page.locator(chatbot_sel).evaluate(
                "el => el.innerText?.trim().substring(0, 400) || ''"
            )
        except Exception:
            return ""

    async def _dump_apply_panel(self, page: Page) -> None:
        """Print visible text + interactive elements inside the apply panel (debug)."""
        try:
            info = await page.evaluate("""() => {
                const containers = [
                    document.querySelector('[class*="applyWidget"]'),
                    document.querySelector('[class*="apply-widget"]'),
                    document.querySelector('[class*="chatbot"]'),
                    document.querySelector('[class*="applyForm"]'),
                    document.querySelector('[class*="apply-form"]'),
                    document.querySelector('[class*="applyModal"]'),
                    document.querySelector('[class*="rightPanel"]'),
                    document.querySelector('[class*="right-panel"]'),
                    document.querySelector('form'),
                ];
                const panel = containers.find(c => c !== null);
                if (!panel) return {found: false, text: '', inputs: []};

                const inputs = Array.from(panel.querySelectorAll(
                    'input, select, textarea, button, label, [role="button"], [role="option"]'
                )).filter(el => el.offsetParent).map(el => ({
                    tag: el.tagName,
                    type: el.type || '',
                    id: el.id || '',
                    name: el.name || '',
                    class: el.className.toString().substring(0, 80),
                    text: el.textContent?.trim().substring(0, 60) || '',
                    placeholder: el.placeholder || '',
                }));

                return {
                    found: true,
                    containerClass: panel.className.toString().substring(0, 100),
                    text: panel.innerText?.substring(0, 500) || '',
                    inputs,
                };
            }""")
            print(f"[apply-panel] container class: {info.get('containerClass', 'n/a')}")
            print(f"[apply-panel] text preview: {info.get('text', '')[:300]}")
            for el in info.get("inputs", []):
                print(f"  {el['tag']}[type={el['type']}] id={el['id']!r} name={el['name']!r} "
                      f"class=...{el['class'][-40:]!r} text={el['text']!r} placeholder={el['placeholder']!r}")
        except Exception as e:
            print(f"[apply-panel] dump failed: {e}")

    async def _accept_consent(self, page: Page) -> None:
        """Click any 'I Accept' / consent checkbox that gates the apply form."""
        selectors = [
            "label:has-text('I Accept')",
            "label:has-text('I agree')",
            "label:has-text('I Agree')",
            "input[type='checkbox'][id*='accept']",
            "input[type='checkbox'][id*='consent']",
            "input[type='checkbox'][name*='consent']",
            "input[type='checkbox'][name*='accept']",
            "[class*='consent'] input[type='checkbox']",
            "[class*='accept'] input[type='checkbox']",
            "[class*='chatbot'] input[type='checkbox']",
        ]
        for sel in selectors:
            try:
                el = page.locator(sel).first
                if await el.is_visible(timeout=600):
                    if "input" in sel and await el.is_checked():
                        return  # already checked
                    await el.click()
                    await page.wait_for_timeout(700)
                    print(f"[naukri] accepted consent via: {sel}")
                    return
            except Exception:
                continue

    async def _fill_naukri_known_fields(self, page: Page) -> None:
        """Hardcoded fallback for Naukri's common apply-form fields."""
        p = self.config.profile

        async def try_text(selectors: list[str], value: str) -> None:
            for sel in selectors:
                try:
                    el = page.locator(sel).first
                    if await el.is_visible(timeout=600):
                        current = await el.input_value()
                        if not current:
                            await el.click()
                            await el.fill(str(value))
                        return
                except Exception:
                    continue

        async def try_select(selectors: list[str], value: str) -> None:
            for sel in selectors:
                try:
                    el = page.locator(sel).first
                    if await el.is_visible(timeout=600):
                        await el.select_option(label=value)
                        return
                except Exception:
                    continue

        await try_text([
            "input[placeholder*='Current CTC']", "input[placeholder*='current ctc']",
            "input[name*='currentCTC']", "input[name*='current_ctc']",
            "[class*='currentCTC'] input", "[class*='current-ctc'] input",
        ], p.current_ctc_lpa)

        await try_text([
            "input[placeholder*='Expected CTC']", "input[placeholder*='expected ctc']",
            "input[name*='expectedCTC']", "input[name*='expected_ctc']",
            "[class*='expectedCTC'] input", "[class*='expected-ctc'] input",
        ], p.expected_ctc_lpa)

        await try_select([
            "select[name*='notice']", "[class*='noticePeriod'] select",
            "select[id*='notice']",
        ], p.notice_period)
        await try_text([
            "input[placeholder*='Notice Period']", "input[placeholder*='notice']",
            "input[name*='noticePeriod']", "input[name*='notice']",
        ], p.notice_period)

        await try_text([
            "input[placeholder*='Total Experience']", "input[placeholder*='Years of Experience']",
            "input[placeholder*='experience']", "input[name*='experience']",
            "input[name*='totalExp']",
        ], p.total_experience_years)

        await try_text([
            "input[placeholder*='Current Location']", "input[placeholder*='location']",
            "input[name*='location']", "input[name*='currentLocation']",
        ], p.current_location)

    async def _fill_cover_note(self, page: Page, cover_note: str) -> None:
        """Fill cover note / message textarea if present."""
        for sel in _COVER_NOTE_SELECTORS:
            try:
                field = page.locator(sel).first
                if await field.is_visible(timeout=800):
                    await field.click()
                    await field.clear()
                    await field.fill(cover_note)
                    return
            except Exception:
                continue

    # ------------------------------------------------------------------
    # Login
    # ------------------------------------------------------------------

    async def _login(self, page: Page) -> None:
        await page.goto("https://www.naukri.com/nlogin/login", wait_until="domcontentloaded")
        await page.wait_for_timeout(random.uniform(2000, 3000))

        email = await page.wait_for_selector(
            "input[placeholder='Enter Email ID / Username']", timeout=10000
        )
        await email.click()
        await asyncio.sleep(0.3)
        await email.fill(self.secrets.naukri_user)
        await asyncio.sleep(random.uniform(0.4, 0.7))

        pwd = await page.wait_for_selector("input[placeholder='Enter Password']", timeout=5000)
        await pwd.click()
        await asyncio.sleep(0.3)
        await pwd.fill(self.secrets.naukri_pass)
        await asyncio.sleep(random.uniform(0.4, 0.7))

        await page.locator("button:has-text('Login')").first.click()
        await page.wait_for_load_state("networkidle", timeout=25000)
        await page.wait_for_timeout(2500)

        if await page.locator("input[placeholder*='OTP'], input[placeholder*='otp']").count() > 0:
            await self._screenshot(page, "otp_required")
            raise RuntimeError(
                "Naukri requires OTP — log in once manually from this IP first, then retry"
            )

        if "nlogin" in page.url or "/login" in page.url.lower():
            await self._screenshot(page, "login_failed")
            raise RuntimeError("Naukri login failed — verify credentials in .env")

        print("[naukri] login successful")

    # ------------------------------------------------------------------
    # Search internals
    # ------------------------------------------------------------------

    async def _search_one(
        self, page: Page, keyword: str, location: str, exp_min: int, exp_max: int
    ) -> list[Job]:
        jobs: list[Job] = []
        seen_keys_this_search: set[str] = set()

        for pg in range(1, self.config.search.max_pages_per_portal + 1):
            params = urlencode({
                "k": keyword,
                "l": location,
                "experience": f"{exp_min}-{exp_max}",
                "jobAge": 30,
                "pg": pg,
            })
            url = f"{SEARCH_BASE}?{params}"

            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=30000)
                await page.wait_for_timeout(3000)
            except Exception as e:
                print(f"[naukri] load error pg{pg} ({keyword}/{location}): {e}")
                break

            try:
                await page.wait_for_selector(_JOB_CARD_SELECTOR, timeout=8000)
            except Exception:
                print(f"[naukri] no cards on pg{pg} ({keyword}/{location})")
                break

            page_jobs = await self._extract_jobs(page, location)
            if not page_jobs:
                break

            new_this_page = [j for j in page_jobs if j.key not in seen_keys_this_search]
            if not new_this_page:
                print(f"[naukri] pg{pg} is a repeat — stopping pagination")
                break

            for j in new_this_page:
                seen_keys_this_search.add(j.key)
            jobs.extend(new_this_page)
            print(f"[naukri] '{keyword}' | '{location}' | pg{pg}: {len(new_this_page)} jobs")

            limit = self.config.search.max_jobs_per_location
            if len(jobs) >= limit:
                jobs = jobs[:limit]
                print(f"[naukri] reached max_jobs_per_location={limit} — stopping")
                break

            await asyncio.sleep(random.uniform(1.5, 3.0))

        return jobs

    async def _extract_jobs(self, page: Page, fallback_location: str) -> list[Job]:
        raw: list[dict] = await page.evaluate("""
            () => {
                const cards = document.querySelectorAll(
                    'article.jobTuple, .srp-jobtuple-wrapper, [class*="jobTuple"], [class*="job-tuple"]'
                );
                return Array.from(cards).map(card => {
                    const titleEl = card.querySelector(
                        'a.title, a[class*="title"], h2 a, .jobTitle a, [class*="job-title"] a'
                    );
                    const compEl = card.querySelector(
                        '.comp-name, a[class*="comp-name"], [class*="companyName"], [class*="company-name"]'
                    );
                    const locEl = card.querySelector(
                        '.locWdth, [class*="location"], [class*="loc"]'
                    );
                    const descEl = card.querySelector(
                        '.job-desc, [class*="job-desc"], [class*="jobDesc"], [class*="job-description"]'
                    );
                    const salEl = card.querySelector('[class*="sal"], [class*="salary"]');
                    return {
                        title:       titleEl?.textContent?.trim() || '',
                        url:         titleEl?.href || '',
                        company:     compEl?.textContent?.trim() || '',
                        location:    locEl?.textContent?.trim() || '',
                        description: descEl?.textContent?.trim() || '',
                        salary:      salEl?.textContent?.trim() || '',
                    };
                }).filter(j => j.title && j.url);
            }
        """)

        jobs: list[Job] = []
        for r in raw:
            company = r.get("company", "").strip()
            if any(ex.lower() in company.lower() for ex in self.config.search.exclude_companies):
                continue
            jobs.append(Job(
                url=r["url"],
                title=r["title"],
                company=company,
                location=r.get("location") or fallback_location,
                description=r.get("description", ""),
                salary=r.get("salary", ""),
                portal=self.name,
            ))

        if not jobs:
            await self._screenshot(page, "no_jobs_extracted")

        return jobs

    async def _screenshot(self, page: Page, name: str) -> None:
        path = SCREENSHOT_DIR / f"naukri_{name}.png"
        try:
            await page.screenshot(path=str(path), full_page=True)
            print(f"[naukri] screenshot: {path}")
        except Exception:
            pass
