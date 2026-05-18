"""AI-powered apply form filler — handles text, select, radio, checkbox fields."""

import json

from openai import AsyncOpenAI
from playwright.async_api import Page

# Extracts all visible, interactable form fields from the apply modal/form
_EXTRACT_JS = """
() => {
    function getLabel(el) {
        if (el.id) {
            const lbl = document.querySelector('label[for="' + el.id + '"]');
            if (lbl) return lbl.textContent.trim();
        }
        const parent = el.closest('.form-group, [class*="field"], [class*="Field"], [class*="row"], li, div');
        if (parent) {
            const lbl = parent.querySelector('label, [class*="label"], [class*="Label"], span.title, p.title');
            if (lbl && lbl !== el) return lbl.textContent.trim();
        }
        return el.getAttribute('aria-label') || el.placeholder || el.name || '';
    }

    // Find the apply modal/form container — prefer the most specific one
    const container = (
        document.querySelector('[class*="applyForm"]') ||
        document.querySelector('[class*="apply-form"]') ||
        document.querySelector('[class*="applyModal"]') ||
        document.querySelector('[class*="apply-modal"]') ||
        document.querySelector('[class*="modal"][class*="apply"]') ||
        document.querySelector('[id*="apply"]') ||
        document.querySelector('form') ||
        document.body
    );

    const fields = [];

    // ── Text / number / textarea inputs ──────────────────────────────
    container.querySelectorAll(
        'input[type="text"], input[type="number"], input[type="tel"], textarea, input:not([type])'
    ).forEach(el => {
        if (!el.offsetParent) return;   // skip hidden
        const label = getLabel(el);
        if (!label) return;
        fields.push({
            type: el.tagName === 'TEXTAREA' ? 'textarea' : 'text',
            id: el.id || '',
            name: el.name || '',
            label: label,
            placeholder: el.placeholder || '',
            current_value: el.value || '',
            required: el.required,
        });
    });

    // ── Native <select> dropdowns ─────────────────────────────────────
    container.querySelectorAll('select').forEach(el => {
        if (!el.offsetParent) return;
        const label = getLabel(el);
        if (!label) return;
        fields.push({
            type: 'select',
            id: el.id || '',
            name: el.name || '',
            label: label,
            options: Array.from(el.options)
                .map(o => o.text.trim())
                .filter(t => t && t !== '--Select--' && t !== 'Select' && t !== '-- Select --'),
            current_value: el.options[el.selectedIndex]?.text?.trim() || '',
            required: el.required,
        });
    });

    // ── Radio button groups ────────────────────────────────────────────
    const radioGroups = {};
    container.querySelectorAll('input[type="radio"]').forEach(el => {
        if (!el.offsetParent) return;
        const key = el.name || el.id;
        if (!radioGroups[key]) radioGroups[key] = { name: key, options: [] };
        const optLabel = getLabel(el) ||
            el.parentElement?.textContent?.trim() ||
            el.nextElementSibling?.textContent?.trim() ||
            el.value;
        radioGroups[key].options.push({ value: el.value, label: optLabel, id: el.id });
    });
    Object.values(radioGroups).forEach(group => {
        const firstId = group.options[0]?.id;
        const groupEl = firstId ? document.getElementById(firstId) : null;
        const groupParent = groupEl?.closest('[class*="group"], [class*="field"], [class*="row"], fieldset, li');
        const groupLabel = groupParent?.querySelector('legend, label, [class*="label"], p, span.question')
            ?.textContent?.trim() || group.name;
        fields.push({
            type: 'radio',
            name: group.name,
            label: groupLabel,
            options: group.options,
        });
    });

    // ── Checkboxes ─────────────────────────────────────────────────────
    container.querySelectorAll('input[type="checkbox"]').forEach(el => {
        if (!el.offsetParent) return;
        const label = getLabel(el);
        if (!label) return;
        fields.push({
            type: 'checkbox',
            id: el.id || '',
            name: el.name || '',
            label: label,
            checked: el.checked,
        });
    });

    return fields;
}
"""

_SYSTEM_PROMPT = """You fill job application forms on behalf of a candidate.
Given the candidate profile and a list of form fields, return a JSON object with key "answers"
containing an array of answer objects.

Each answer object must have:
- "field_id": the value of the field's "id" key if non-empty, else the "name" key (never the label text)
- "field_name": the value of the field's "name" key (for fallback lookup)
- "type": same type as the field (text, textarea, select, radio, checkbox)
- "value": the answer to fill

Rules:
- For radio: value must exactly match one of the provided option labels or values
- For select: value must exactly match one of the provided option strings
- For checkbox: value is "true" (check it) or "false" (leave unchecked)
- For text/textarea/number: value is the string to type
- Skip fields already filled (current_value not empty) unless the value looks wrong
- Skip cover note / message / description / letter fields (handled separately)
- Answer honestly based on the profile — do not invent facts
Return only valid JSON."""


async def fill(
    page: Page,
    profile,
    resume_text: str,
    openai_key: str,
) -> int:
    """Fill all apply form fields using AI. Returns number of fields filled."""
    fields: list[dict] = await page.evaluate(_EXTRACT_JS)
    print(f"[form_filler] extracted {len(fields)} fields from page")
    if not fields:
        return 0

    for f in fields:
        print(f"  field: type={f.get('type')} id={f.get('id')!r} name={f.get('name')!r} label={f.get('label')!r}")

    skip_keywords = {"cover", "message", "note", "letter", "description"}
    pending = [
        f for f in fields
        if not f.get("current_value")
        and not any(kw in f.get("label", "").lower() for kw in skip_keywords)
    ]
    print(f"[form_filler] {len(pending)} fields need filling")
    if not pending:
        return 0

    client = AsyncOpenAI(api_key=openai_key)

    profile_summary = (
        f"Name: Amit Kumar Upadhyay\n"
        f"Total Experience: {profile.total_experience_years} years\n"
        f"Current CTC: {profile.current_ctc_lpa} LPA\n"
        f"Expected CTC: {profile.expected_ctc_lpa} LPA\n"
        f"Notice Period: {profile.notice_period}\n"
        f"Current Location: {profile.current_location}\n"
        f"Skills: Java, Spring Boot, Microservices, Kubernetes, Kafka, AWS, Docker\n"
    )

    resp = await client.chat.completions.create(
        model="gpt-4o",
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": _SYSTEM_PROMPT},
            {
                "role": "user",
                "content": (
                    f"CANDIDATE PROFILE:\n{profile_summary}\n\n"
                    f"FORM FIELDS:\n{json.dumps(pending, indent=2)}\n\n"
                    "Return JSON with key 'answers'."
                ),
            },
        ],
        temperature=0.1,
    )

    data = json.loads(resp.choices[0].message.content)
    answers: list[dict] = data.get("answers", [])
    print(f"[form_filler] GPT returned {len(answers)} answers: {json.dumps(answers, indent=2)}")

    filled = 0
    for ans in answers:
        if await _apply_answer(page, ans):
            filled += 1
            print(f"  filled: {ans.get('field_id') or ans.get('field_name')} = {ans.get('value')!r}")
        else:
            print(f"  MISSED: {ans.get('field_id') or ans.get('field_name')} = {ans.get('value')!r}")

    print(f"[form_filler] filled {filled}/{len(answers)} fields")
    return filled


async def _apply_answer(page: Page, answer: dict) -> bool:
    field_id: str = str(answer.get("field_id", "")).strip()
    field_name: str = str(answer.get("field_name", "")).strip()
    field_type: str = answer.get("type", "text")
    value: str = str(answer.get("value", "")).strip()

    if not value:
        return False

    # Build selectors: prefer id, fall back to name
    id_sel = f"[id='{field_id}']" if field_id else ""
    name_sel = f"[name='{field_name}']" if field_name else ""

    def locator_for(tag_filter: str = "") -> str:
        parts = []
        if id_sel:
            parts.append(f"{tag_filter}{id_sel}")
        if name_sel:
            parts.append(f"{tag_filter}{name_sel}")
        return ", ".join(parts) if parts else ""

    try:
        if field_type == "radio":
            name = field_name or field_id
            # Try matching by value attribute first
            radio = page.locator(
                f"input[type='radio'][name='{name}'][value='{value}']"
            ).first
            if not await radio.is_visible(timeout=600):
                # Fallback: find the radio whose parent text contains the value
                all_radios = page.locator(f"input[type='radio'][name='{name}']")
                count = await all_radios.count()
                for i in range(count):
                    r = all_radios.nth(i)
                    parent_text = await r.evaluate(
                        "el => el.closest('label,li,div')?.textContent?.trim() || ''"
                    )
                    if value.lower() in parent_text.lower():
                        radio = r
                        break
            if await radio.is_visible(timeout=600):
                await radio.click()
                return True

        elif field_type == "select":
            sel_str = locator_for("select")
            if not sel_str:
                return False
            sel = page.locator(sel_str).first
            if await sel.is_visible(timeout=600):
                try:
                    await sel.select_option(label=value)
                except Exception:
                    await sel.select_option(value=value)
                return True

        elif field_type == "checkbox":
            sel_str = locator_for("input[type='checkbox']")
            if not sel_str:
                return False
            chk = page.locator(sel_str).first
            if await chk.is_visible(timeout=600):
                is_checked = await chk.is_checked()
                should_check = value.lower() == "true"
                if is_checked != should_check:
                    await chk.click()
                return True

        else:  # text / number / textarea
            sel_str = locator_for()
            if not sel_str:
                return False
            field = page.locator(sel_str).first
            if await field.is_visible(timeout=600):
                await field.click()
                await field.clear()
                await field.fill(value)
                return True

    except Exception as exc:
        print(f"  [form_filler] error applying {field_id or field_name}: {exc}")

    return False
