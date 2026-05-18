"""Main orchestrator — search → deduplicate → score → apply (or dry-run log)."""

import argparse
import asyncio
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import cover_note as cover_note_mod
import notifier
from config import load_config, load_secrets
from matcher import score_job
from notifier import RunResult
from portals.base import ApplyResult, ApplyStatus
from portals.naukri import NaukriPortal
from tracker import finish_run, init_db, is_seen, mark_result, mark_seen, start_run


def _build_portals(config, secrets, portal_filter: str):
    portals = []
    if portal_filter in ("all", "naukri") and config.portals.naukri:
        portals.append(NaukriPortal(config, secrets))
    return portals


async def run(dry_run: bool, portal_filter: str, test: bool = False, max_apply: int | None = None) -> None:
    config = load_config()
    secrets = load_secrets()

    if test:
        config.search.keywords = config.search.keywords[:1]
        config.search.locations = config.search.locations[:1]
        config.search.max_pages_per_portal = 1
        print(f"[test mode] keyword='{config.search.keywords[0]}' location='{config.search.locations[0]}'")

    if max_apply is not None:
        config.apply.max_applications_per_run = max_apply
        print(f"[max-apply] capped at {max_apply} application(s) this run")

    init_db()
    run_id = start_run()
    portals = _build_portals(config, secrets, portal_filter)

    if not portals:
        print("No portals enabled. Check config.yaml or --portal flag.")
        return

    total_found = total_scored = total_applied = total_failed = 0
    applied_count = 0
    run_result = RunResult()

    for portal in portals:
        print(f"\n{'='*55}")
        print(f"Portal: {portal.name.upper()}")
        print(f"{'='*55}")

        async with portal:
            # --- Search ---
            try:
                jobs = await portal.search()
            except Exception as e:
                print(f"[{portal.name}] search failed: {e}")
                continue

            new_jobs = [j for j in jobs if not is_seen(j)]
            print(f"\nFound {len(jobs)} jobs, {len(new_jobs)} new\n")
            total_found += len(new_jobs)
            run_result.found += len(new_jobs)

            # --- Score + Apply ---
            for job in new_jobs:
                mark_seen(job)

                try:
                    score, reason, should_apply = await score_job(
                        job, config.resume_text, secrets.openai_key
                    )
                except Exception as e:
                    print(f"  [score error] {job.title} @ {job.company}: {e}")
                    continue

                job.score = score
                job.score_reason = reason
                total_scored += 1
                run_result.scored += 1

                tag = "APPLY" if should_apply else "SKIP "
                print(f"  [{score:3d}] {tag} | {job.title} @ {job.company} | {job.location}")
                print(f"             {reason}")

                at_limit = applied_count >= config.apply.max_applications_per_run

                if dry_run:
                    mark_result(ApplyResult(job=job, status=ApplyStatus.DRY_RUN))
                    continue

                if not should_apply or at_limit:
                    mark_result(ApplyResult(job=job, status=ApplyStatus.SKIPPED))
                    run_result.skipped += 1
                    continue

                # Generate cover note
                note = ""
                if config.apply.cover_note:
                    try:
                        note = await cover_note_mod.generate(
                            job, config.resume_text, secrets.openai_key,
                            max_words=config.apply.cover_note_max_words,
                        )
                    except Exception as e:
                        print(f"             [cover note error] {e} — applying without note")

                # Apply
                try:
                    result = await portal.apply(job, note)
                    mark_result(result)

                    _jd = {"title": job.title, "company": job.company,
                           "location": job.location, "score": score, "error": result.error}
                    if result.status == ApplyStatus.APPLIED:
                        applied_count += 1
                        total_applied += 1
                        run_result.applied.append(_jd)
                        print(f"             -> APPLIED")
                    elif result.status == ApplyStatus.ALREADY_APPLIED:
                        print(f"             -> already applied")
                    elif result.status == ApplyStatus.MANUAL_REQUIRED:
                        run_result.manual.append(_jd)
                        print(f"             -> MANUAL REQUIRED ({result.error})")
                    else:
                        total_failed += 1
                        run_result.failed.append(_jd)
                        print(f"             -> {result.status.value}: {result.error}")

                except Exception as e:
                    total_failed += 1
                    mark_result(ApplyResult(job=job, status=ApplyStatus.FAILED, error=str(e)))
                    print(f"             -> FAILED: {e}")

                # Delay between applications
                if applied_count < config.apply.max_applications_per_run:
                    delay = config.apply.delay_between_apply_sec + random.uniform(-15, 15)
                    await asyncio.sleep(max(10, delay))

    finish_run(run_id, total_found, total_scored, total_applied, total_failed)

    if not dry_run:
        notifier.send(
            bot_token=secrets.telegram_bot_token,
            chat_id=secrets.telegram_chat_id,
            result=run_result,
            send_on_empty=config.notify.send_on_empty_run,
        )

    print(f"\n{'='*55}")
    print(f"Run complete")
    print(f"  Found:   {total_found}")
    print(f"  Scored:  {total_scored}")
    print(f"  Applied: {total_applied}")
    print(f"  Failed:  {total_failed}")
    if dry_run:
        print(f"  (dry-run — no applications sent)")
    print(f"{'='*55}")


def main():
    parser = argparse.ArgumentParser(description="Job Search AI Agent")
    parser.add_argument("--dry-run", action="store_true",
                        help="Search and score only — do not apply")
    parser.add_argument("--portal", default="all",
                        help="Portal to run: naukri | all (default: all)")
    parser.add_argument("--test", action="store_true",
                        help="Quick mode: 1 keyword, 1 location, 1 page")
    parser.add_argument("--max-apply", type=int, default=None,
                        help="Cap number of applications this run (overrides config)")
    args = parser.parse_args()

    asyncio.run(run(
        dry_run=args.dry_run,
        portal_filter=args.portal,
        test=args.test,
        max_apply=args.max_apply,
    ))


if __name__ == "__main__":
    main()
