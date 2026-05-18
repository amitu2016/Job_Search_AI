"""Telegram notifier — sends a run digest after each agent run."""

import json
import urllib.request
from dataclasses import dataclass, field

from portals.base import ApplyStatus


@dataclass
class RunResult:
    found: int = 0
    scored: int = 0
    applied: list[dict] = field(default_factory=list)   # {title, company, location, score}
    failed: list[dict] = field(default_factory=list)
    manual: list[dict] = field(default_factory=list)
    skipped: int = 0


def _build_message(result: RunResult) -> str:
    lines = ["<b>Job Search AI — Run Complete</b>", ""]
    lines.append(f"Found:   {result.found} new jobs")
    lines.append(f"Scored:  {result.scored}")
    lines.append(f"Applied: {len(result.applied)}")
    lines.append(f"Failed:  {len(result.failed)}")
    if result.manual:
        lines.append(f"Manual:  {len(result.manual)} (external ATS)")

    if result.applied:
        lines.append("")
        lines.append("<b>Applied:</b>")
        for j in result.applied:
            lines.append(f"  • {j['title']} @ {j['company']}  [score {j['score']}]")

    if result.manual:
        lines.append("")
        lines.append("<b>Manual Required (external ATS):</b>")
        for j in result.manual:
            lines.append(f"  • {j['title']} @ {j['company']}")

    if result.failed:
        lines.append("")
        lines.append("<b>Failed:</b>")
        for j in result.failed:
            lines.append(f"  • {j['title']} @ {j['company']} — {j.get('error', '')[:60]}")

    return "\n".join(lines)


def send(bot_token: str, chat_id: str, result: RunResult, send_on_empty: bool = False) -> None:
    """Send Telegram digest. No-op if credentials missing or run is empty and send_on_empty=False."""
    if not bot_token or not chat_id:
        return
    if not send_on_empty and result.found == 0:
        return

    text = _build_message(result)
    payload = json.dumps({
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
    }).encode()

    req = urllib.request.Request(
        f"https://api.telegram.org/bot{bot_token}/sendMessage",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            if resp.status == 200:
                print("[notify] Telegram message sent")
            else:
                print(f"[notify] Telegram returned {resp.status}")
    except Exception as e:
        print(f"[notify] Telegram send failed: {e}")
