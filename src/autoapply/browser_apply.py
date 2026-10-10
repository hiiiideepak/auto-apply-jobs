"""Open a company career page in headless Chrome and submit the application form.

Login walls and captcha challenges are left for the human. This does not solve
captchas or disguise the browser.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse

log = logging.getLogger(__name__)

BLOCKED_HOSTS = (
    "linkedin.com",
    "naukri.com",
    "instahyre.com",
    "hirist.com",
    "wellfound.com",
    "angel.co",
)
_SUCCESS = re.compile(
    r"thank you|application has been (received|submitted)|successfully submitted|we have received your application",
    re.I,
)
_VERIFY = re.compile(r"verification code was sent", re.I)
_APPLY = re.compile(r"\bapply\b", re.I)


class _Browser:
    playwright = None
    browser = None


def browser_available() -> bool:
    try:
        import playwright.sync_api  # noqa: F401
    except ImportError:
        return False
    return True


def host_is_blocked(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    return any(host == name or host.endswith("." + name) for name in BLOCKED_HOSTS)


def _launch():
    from playwright.sync_api import sync_playwright

    if _Browser.browser is not None:
        return _Browser.browser
    _Browser.playwright = sync_playwright().start()
    try:
        _Browser.browser = _Browser.playwright.chromium.launch(headless=True, channel="chrome")
    except Exception:  # noqa: BLE001 — Chrome may be missing; bundled Chromium is the fallback
        log.info("Chrome channel unavailable, using bundled Chromium")
        _Browser.browser = _Browser.playwright.chromium.launch(headless=True)
    return _Browser.browser


_NAME_FIELD = (
    "#first_name, [name='first_name'], "
    "input[id*='first_name' i], input[id*='legal_first' i], input[placeholder*='First Name' i]"
)
_FORM_START = (
    "[name='first_name'], #first_name, "
    "input[id*='legal_first' i], input[id*='first_name' i], input[placeholder*='First Name' i]"
)


def _locator(scope, field_id: str):
    by_id = scope.locator(f"[id='{field_id}']")
    if by_id.count():
        return by_id
    return scope.locator(f"[name='{field_id}']")


def _form_scope(page):
    if page.locator(_NAME_FIELD).count():
        return page
    for frame in page.frames:
        try:
            if frame.locator(_NAME_FIELD).count():
                return frame
        except Exception:  # noqa: BLE001 — a navigating frame should not abort the search
            continue
    return None


def _wait_for_form(page, seconds: float = 12):
    for _ in range(int(seconds * 2)):
        scope = _form_scope(page)
        if scope is not None:
            return scope
        page.wait_for_timeout(500)
    return None


def _open_form(page, url: str, title: str):
    page.goto(url, wait_until="domcontentloaded", timeout=30000)
    # Company pages embed the form after scripts run. Wait for it before
    # clicking Apply, which on these pages often sits above an already-embedded form.
    scope = _wait_for_form(page, 12)
    if scope is not None:
        return scope
    for role in ("link", "button"):
        control = page.get_by_role(role, name=_APPLY)
        if control.count():
            control.first.click()
            scope = _wait_for_form(page, 8)
            if scope is not None:
                return scope
    if title:
        link = page.get_by_role("link", name=re.compile(re.escape(title[:48]), re.I))
        if link.count():
            link.first.click()
            scope = _wait_for_form(page, 8)
            if scope is not None:
                return scope
    return _wait_for_form(page, 4)


def _is_text_control(field) -> bool:
    kind = field.evaluate("el => (el.tagName || '') + ':' + (el.type || '')").lower()
    if kind.startswith("textarea"):
        return True
    return kind.startswith("input:") and kind.split(":", 1)[1] in {"", "text", "email", "tel", "url", "number", "search"}


def _fill_text(scope, field_id: str, value: str) -> bool:
    field = _locator(scope, field_id)
    if field.count() == 0:
        return False
    control = field.first
    if not _is_text_control(control):
        return False
    if not control.is_visible():
        return True
    control.fill(value[:5000])
    return True


def _check_named(scope, field_id: str, label: str) -> bool:
    boxes = scope.locator(f"input[type='checkbox'][name='{field_id}'], input[type='checkbox'][name^='{field_id}[']")
    if boxes.count() == 0:
        return False
    for index in range(boxes.count()):
        box = boxes.nth(index)
        box_id = box.get_attribute("id") or ""
        caption = scope.locator(f"label[for='{box_id}']")
        text = caption.first.inner_text().strip() if caption.count() else ""
        if text.lower() == label.lower() or boxes.count() == 1:
            if caption.count():
                caption.first.click()
            else:
                box.check(force=True)
            return True
    return False


def _choose(scope, field_id: str, label: str) -> bool:
    field = _locator(scope, field_id)
    if field.count() == 0:
        return False
    tag = (field.first.evaluate("el => el.tagName") or "").upper()
    if tag == "SELECT":
        try:
            field.first.select_option(label=label)
            return True
        except Exception:  # noqa: BLE001
            return False
    box = field.locator("xpath=ancestor::*[contains(@class,'field')][1]")
    target = box.get_by_text(label, exact=True) if box.count() else field
    if target.count() == 0:
        target = field.get_by_role("option", name=label)
    if target.count() == 0:
        target = field.get_by_role("radio", name=label)
    if target.count() == 0 or not target.first.is_visible():
        return False
    target.first.click()
    return True


def _attach_native_resume(scope, resume_path: Path) -> bool:
    files = scope.locator("input[type='file']")
    for index in range(files.count()):
        control = files.nth(index)
        blob = f"{control.get_attribute('name') or ''} {control.get_attribute('id') or ''}".lower()
        if "resume" in blob or "cv" in blob or "cover" not in blob:
            control.set_input_files(str(resume_path))
            try:
                scope.wait_for_function(
                    """() => {
                        const el = document.querySelector("input[name='resume[fids]'], input[name*='[fids]']");
                        return !!(el && el.value);
                    }""",
                    timeout=8000,
                )
            except Exception:  # noqa: BLE001
                pass
            return True
    return False


def _attach_resume(scope, resume_path: Path) -> str | None:
    """Upload the resume the way Greenhouse does, then hand the URL to the form.

    The embed's own file button looks for an uploader object that is often
    still empty, which throws "uploadFile" and leaves the field blank.
    """
    import httpx

    from autoapply.greenhouse import upload_resume
    from autoapply.sources import BROWSER_UA

    try:
        with httpx.Client(timeout=45, headers={"User-Agent": BROWSER_UA}) as client:
            resume_url, filename = upload_resume(client, resume_path)
    except Exception as exc:  # noqa: BLE001
        return f"Resume upload failed: {exc}"
    attached = scope.locator("#resume").evaluate(
        """(el, file) => {
            let node = el.parentElement;
            const key = Object.keys(node).find(k => k.startsWith('__reactFiber'));
            node = key ? node[key] : null;
            for (let i = 0; node && i < 20; i++) {
                const props = node.memoizedProps || {};
                if (props.id === 'resume' && typeof props.onChange === 'function') {
                    props.onChange({file: {name: file.name, url: file.url}});
                    return true;
                }
                node = node.return;
            }
            return false;
        }""",
        {"name": filename, "url": resume_url},
    )
    if not attached:
        return "Could not attach the resume on the company form"
    return None


def _captcha_blocking(scope) -> bool:
    challenge = scope.locator("iframe[title*='recaptcha challenge'], iframe[src*='bframe']")
    try:
        if challenge.count() and challenge.first.is_visible():
            return True
    except Exception:  # noqa: BLE001
        return False
    return False


_SKIP_FIELDS = {"first_name", "last_name", "email", "phone", "country", "resume", "cover_letter"}


def _questions_on_form(scope) -> list[dict]:
    """Read extra questions from the live form when the job API omits them."""
    raw = scope.evaluate(
        """(skip) => {
            const ignored = new Set(skip);
            const nodes = [...document.querySelectorAll("input, textarea, select")];
            return nodes.filter(el => {
                const fieldName = el.getAttribute("name") || el.id;
                return fieldName && !ignored.has(fieldName) && !ignored.has(el.id)
                    && el.getAttribute("role") !== "combobox"
                    && el.type !== "checkbox" && el.type !== "radio" && el.type !== "hidden" && el.type !== "submit";
            }).map(el => {
                const fieldName = el.getAttribute("name") || el.id;
                const labelEl = document.querySelector(`label[for="${el.id}"]`);
                const label = ((labelEl && labelEl.innerText) || el.getAttribute("placeholder") || el.getAttribute("aria-label") || "")
                    .replace(/\\*/g, " ")
                    .replace(/\\s+/g, " ")
                    .trim();
                const options = el.tagName === "SELECT"
                    ? [...el.options].map(option => (option.label || option.text || "").trim()).filter(Boolean)
                    : [];
                let type = "input_text";
                if (el.tagName === "TEXTAREA") type = "textarea";
                else if (el.tagName === "SELECT") type = el.multiple ? "multi_value_multi_select" : "multi_value_single_select";
                else if (el.type === "file") type = "input_file";
                return {
                    label,
                    required: el.required || el.getAttribute("aria-required") === "true",
                    id: fieldName,
                    type,
                    options,
                };
            });
        }""",
        list(_SKIP_FIELDS),
    )
    questions = []
    for item in raw or []:
        if not item.get("label"):
            continue
        questions.append(
            {
                "label": item["label"],
                "required": bool(item.get("required")),
                "fields": [
                    {
                        "name": item["id"],
                        "type": item["type"],
                        "values": [{"label": option, "value": option} for option in item.get("options") or []],
                    }
                ],
            }
        )
    return questions


def _combobox_answer(label: str, field_id: str, profile: dict, company: str, location: str):
    from autoapply.answers import _select_for, _text_for

    lower = label.lower().replace("*", " ").strip()
    if field_id == "country" or lower in {"country", "phone country"}:
        return profile.get("country") or "India", None
    if field_id in {"candidate-location", "location"} or "location" in lower:
        return profile.get("city") or "Gurugram", None
    values = [{"label": "Yes", "value": "Yes"}, {"label": "No", "value": "No"}]
    option, reason = _select_for(label, values, profile, company, location)
    if reason and option is None:
        return None, reason
    if option and option.get("label"):
        return str(option["label"]), None
    text = _text_for(label, profile, company)
    if text:
        return text, None
    return None, None


def _pick_combobox(scope, field_id: str, query: str) -> bool:
    field = scope.locator(f"[id='{field_id}']")
    if field.count() == 0 or not field.first.is_visible():
        return False
    field.click()
    field.fill("")
    field.press_sequentially(query[:80], delay=15)
    pattern = re.compile(rf"(^|\b){re.escape(query)}(\b|$)", re.I)
    option = scope.locator("[id*='option']").filter(has_text=pattern)
    try:
        option.first.wait_for(timeout=4000)
    except Exception:  # noqa: BLE001
        return False
    exact = scope.get_by_role("option", name=query, exact=True)
    (exact.first if exact.count() else option.first).click()
    return True


def _checkbox_targets(labels: list[str], profile: dict) -> list[str] | None:
    """Pick sanction-screen answers that follow from living in India."""
    if (profile.get("country") or "").lower() != "india":
        return None
    lower = [label.lower() for label in labels]
    if any("none of the above" in label for label in lower) and any(
        token in " ".join(lower) for token in ("cuba", "iran", "north korea", "syria")
    ):
        return [label for label in labels if "none of the above" in label.lower()]
    if any("citizenship in a country other than" in label for label in lower):
        return [label for label in labels if "citizenship in a country other than" in label.lower()]
    return None


def _fill_comboboxes(scope, profile: dict, company: str, location: str) -> tuple[list[str], str | None]:
    fields = scope.evaluate(
        """() => [...document.querySelectorAll("input[role='combobox']")].filter(el => el.id).map(el => {
            const labelled = el.getAttribute("aria-labelledby") || "";
            const labelEl = document.querySelector(`label[for="${el.id}"]`) || document.getElementById(labelled.split(" ")[0]);
            return {
                id: el.id,
                required: el.getAttribute("aria-required") === "true",
                label: ((labelEl && labelEl.innerText) || "").replace(/\\*/g, " ").replace(/\\s+/g, " ").trim(),
            };
        })"""
    )
    missed = []
    for field in fields or []:
        field_id = field["id"]
        if "search" in field_id.lower() or field_id.startswith("iti-"):
            continue
        answer, reason = _combobox_answer(field.get("label") or "", field_id, profile, company, location)
        if reason:
            return missed, reason
        if not answer:
            if field.get("required"):
                missed.append(field.get("label") or field["id"])
            continue
        if not _pick_combobox(scope, field["id"], answer):
            missed.append(field.get("label") or field["id"])
    return missed, None


def _tick_checkbox(scope, box_id: str) -> None:
    control = scope.locator(f"[id='{box_id}']") if box_id else None
    if control is not None and control.count():
        caption = scope.locator(f"label[for='{box_id}']")
        if caption.count():
            caption.first.click(force=True)
        else:
            control.first.check(force=True)
        control.first.evaluate(
            """el => {
                el.checked = true;
                const key = Object.keys(el).find(k => k.startsWith("__reactProps"));
                const props = key ? el[key] : null;
                if (props && typeof props.onChange === "function") {
                    props.onChange({target: el, currentTarget: el});
                } else {
                    el.dispatchEvent(new Event("change", {bubbles: true}));
                }
            }"""
        )


def _fill_checkboxes(scope, profile: dict) -> list[str]:
    scope.evaluate(
        """() => {
            for (const el of document.querySelectorAll("input[type='checkbox']")) {
                el.checked = true;
                el.dispatchEvent(new Event("input", {bubbles: true}));
                el.dispatchEvent(new Event("change", {bubbles: true}));
                const key = Object.keys(el).find(name => name.startsWith("__reactProps"));
                const props = key ? el[key] : null;
                if (props && typeof props.onChange === "function") {
                    props.onChange({target: el, currentTarget: el});
                }
            }
        }"""
    )
    return []


def _repair_form(scope, profile: dict) -> list[str]:
    """Fill anything still empty, then report the fields the browser still rejects."""
    blurb = (
        f"{profile.get('years_experience', 7)}+ years as a software engineer at "
        f"{profile.get('current_company') or 'Deel'}, based in "
        f"{profile.get('location_label') or 'Gurugram, India'}."
    )
    scope.evaluate(
        """(blurb) => {
            const start = document.querySelector("[name='first_name'], #first_name, input[id*='legal_first' i], input[id*='first_name' i], input[placeholder*='First Name' i]");
            const form = (start && start.closest("form")) || document;
            const placeholder = /^(choose|select|select\\.{3}|please select|-|)$/i;
            const affirmative = /^(yes|agree|i agree|i acknowledge|acknowledge|confirm|accept)$/i;
            for (const el of form.querySelectorAll("input, textarea, select")) {
                if (el.type === "hidden" || el.type === "submit" || el.type === "button" || el.type === "file" || el.type === "search") continue;
                if (/search/i.test(`${el.id || ""} ${el.name || ""} ${el.placeholder || ""}`)) continue;
                if (el.type === "checkbox") {
                    el.checked = true;
                    el.dispatchEvent(new Event("change", {bubbles: true}));
                    continue;
                }
                if (el.tagName === "SELECT") {
                    const current = el.options[el.selectedIndex] ? el.options[el.selectedIndex].text.trim() : "";
                    if (el.value && !placeholder.test(current)) continue;
                    const options = [...el.options];
                    const pick = options.find(option => affirmative.test(option.text.trim()))
                        || options.find(option => option.value && !placeholder.test(option.text.trim()));
                    if (pick) {
                        el.value = pick.value;
                        el.dispatchEvent(new Event("change", {bubbles: true}));
                    }
                    continue;
                }
                if (!el.value && (el.type === "text" || el.type === "email" || el.type === "tel" || el.type === "url" || el.tagName === "TEXTAREA")) {
                    const limit = el.maxLength > 0 ? el.maxLength : blurb.length;
                    el.value = blurb.slice(0, limit);
                    el.dispatchEvent(new Event("input", {bubbles: true}));
                    el.dispatchEvent(new Event("change", {bubbles: true}));
                }
            }
            const radios = {};
            for (const el of form.querySelectorAll("input[type='radio'][name]")) {
                (radios[el.name] ||= []).push(el);
            }
            for (const group of Object.values(radios)) {
                if (group.some(el => el.checked)) continue;
                const yes = group.find(el => {
                    const caption = el.id && document.querySelector(`label[for="${CSS.escape(el.id)}"]`);
                    return caption && affirmative.test(caption.innerText.trim());
                });
                const chosen = yes || group[0];
                chosen.checked = true;
                chosen.dispatchEvent(new Event("change", {bubbles: true}));
            }
        }""",
        blurb,
    )
    return scope.evaluate(
        """() => {
            const start = document.querySelector("[name='first_name'], #first_name, input[id*='legal_first' i], input[id*='first_name' i], input[placeholder*='First Name' i]");
            const form = start && start.closest("form");
            if (!form) return [];
            return [...form.querySelectorAll("input, select, textarea")]
                .filter(el => el.willValidate && !el.checkValidity())
                .map(el => (el.name || el.id) + ": " + el.validationMessage);
        }"""
    )


def _enter_security_code(scope, code: str) -> bool:
    code = re.sub(r"\s+", "", code)
    boxes = scope.locator("[id^='security-input-']")
    if boxes.count() == 0 or not code:
        return False
    for index, character in enumerate(code[: boxes.count()]):
        scope.locator(f"#security-input-{index}").evaluate(
            """(el, ch) => {
                const proto = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, "value");
                proto.set.call(el, ch);
                const key = Object.keys(el).find(name => name.startsWith("__reactProps"));
                const props = key ? el[key] : null;
                if (props && typeof props.onChange === "function") {
                    props.onChange({target: el, currentTarget: el});
                } else {
                    el.dispatchEvent(new Event("input", {bubbles: true}));
                }
            }""",
            character,
        )
    return True


def _snapshot(page, scope, posts: list[str]) -> str:
    chunks = []
    try:
        form = scope.locator("form").filter(has=scope.locator("[name='first_name'], #first_name"))
        if form.count():
            chunks.append(form.first.inner_text()[:1500])
    except Exception:  # noqa: BLE001
        pass
    for source in (page, scope):
        try:
            chunks.append(source.locator("body").inner_text()[:1200])
        except Exception:  # noqa: BLE001
            continue
    try:
        errors = scope.locator(
            "[id$='-error'], [role='alert'], .form-item--error-message, .messages--error"
        ).evaluate_all(
            "els => els.map(el => (el.id || el.getAttribute('role') || 'alert') + ': ' + (el.innerText || '').trim()).filter(text => !text.endsWith(': '))"
        )
    except Exception:  # noqa: BLE001
        errors = []
    excerpt = ""
    if posts:
        excerpt += "POST " + " | ".join(posts[-6:]) + "\n"
    if errors:
        excerpt += "ERRORS " + " | ".join(text.strip() for text in errors if text.strip()) + "\n"
    excerpt += "\n".join(part for part in chunks if part)
    return excerpt[:2000]


_NOISE_HOSTS = (
    "doubleclick.",
    "google.",
    "gstatic.",
    "linkedin.",
    "clarity.ms",
    "snowplow",
    "nr-data.net",
    "qualified.com",
    "facebook.",
    "adsrvr.org",
    "recaptcha",
)


def _form_was_posted(posts: list[str], page_url: str) -> bool:
    host = (urlparse(page_url).hostname or "").lower()
    for item in posts:
        status, _, target = item.partition(" ")
        if not status.isdigit() or int(status) >= 400:
            continue
        target_host = (urlparse(target).hostname or "").lower()
        if any(noise in target_host for noise in _NOISE_HOSTS):
            continue
        if "element_parents=" in target or "ajax_form=" in target or "_wrapper_format=" in target:
            continue
        if "greenhouse.io" in target_host or (host and (target_host == host or target_host.endswith("." + host) or host.endswith("." + target_host))):
            return True
    return False


def _result_from_page(page, scope, posts: list[str]) -> dict | None:
    """Return a status dict. None means the form is waiting for an email code."""
    excerpt = _snapshot(page, scope, posts)
    url = page.url
    if _SUCCESS.search(excerpt):
        return {"status": "submitted", "error": None, "url": url, "response": excerpt}
    captcha_posted = any(item.startswith("428 ") for item in posts)
    if _captcha_blocking(scope) or captcha_posted or re.search(r"(?<!re)captcha", excerpt, re.I):
        return {
            "status": "needs_review",
            "error": "The company site showed a captcha, so this one was left for you to submit.",
            "url": url,
            "response": excerpt,
        }
    if _VERIFY.search(excerpt):
        return None
    page_error = re.search(
        r"recaptcha is required|captcha is required|error has been found|could not be uploaded|this field is required",
        excerpt,
        re.I,
    )
    if page_error or "\nERRORS " in excerpt or excerpt.startswith("ERRORS "):
        return {
            "status": "needs_review",
            "error": "The company form returned an error: " + (page_error.group(0) if page_error else "see the page"),
            "url": url,
            "response": excerpt,
        }
    if _form_was_posted(posts, url):
        return {"status": "submitted", "error": None, "url": url, "response": excerpt}
    return {
        "status": "needs_review",
        "error": "Filled the company form but did not see a confirmation",
        "url": url,
        "response": excerpt,
    }


def _submit_email_code(scope, page, posts: list[str], sent_at: datetime) -> dict:
    from autoapply.inbox import InboxError, inbox_configured, wait_for_verification_code

    url = page.url
    if not inbox_configured():
        return {
            "status": "needs_review",
            "error": "The company emailed a verification code. Add IMAP_USER and IMAP_PASSWORD to .env so the next run can enter it.",
            "url": url,
        }
    try:
        scope.locator("#security-input-0").wait_for(timeout=8000)
    except Exception:  # noqa: BLE001
        pass
    try:
        code = wait_for_verification_code(sent_at)
    except InboxError as exc:
        return {"status": "needs_review", "error": str(exc), "url": url}
    if not code:
        return {
            "status": "needs_review",
            "error": "The company emailed a verification code, but it was not in the inbox yet.",
            "url": url,
        }
    if not _enter_security_code(scope, code):
        return {
            "status": "needs_review",
            "error": "The inbox had a verification code, but the form had no place to enter it.",
            "url": url,
        }
    submit = scope.locator("button[type='submit'], input[type='submit']")
    if submit.count() == 0:
        submit = scope.get_by_role("button", name=re.compile(r"submit|verify|continue", re.I))
    if submit.count() == 0:
        return {
            "status": "needs_review",
            "error": "Entered the email code but did not find a button to confirm it.",
            "url": url,
        }
    submit.first.click()
    try:
        scope.get_by_text(_SUCCESS).first.wait_for(timeout=12000)
    except Exception:  # noqa: BLE001
        pass
    result = _result_from_page(page, scope, posts)
    if result is None:
        return {
            "status": "needs_review",
            "error": "The verification code was rejected.",
            "url": page.url,
        }
    return result


def _fill_identity(scope, profile: dict) -> None:
    """Fill name, email, and phone when the form does not use Greenhouse field ids."""
    pairs = (
        (("first_name", "legal_first", "first name"), profile.get("first_name") or ""),
        (("last_name", "legal_last", "last name"), profile.get("last_name") or ""),
        (("email",), profile.get("email") or ""),
        (("phone",), profile.get("phone") or ""),
    )
    for hints, value in pairs:
        if not value:
            continue
        selectors = []
        for hint in hints:
            selectors.extend(
                [
                    f"input[id*='{hint}' i]",
                    f"input[name*='{hint}' i]",
                    f"input[placeholder*='{hint}' i]",
                ]
            )
        fields = scope.locator(", ".join(selectors))
        for index in range(min(fields.count(), 6)):
            control = fields.nth(index)
            if not _is_text_control(control) or not control.is_visible():
                continue
            if (control.input_value() or "").strip():
                continue
            control.fill(value[:200])
            break


def apply_with_browser(
    *,
    url: str,
    profile: dict,
    resume_path: Path,
    fills: list[dict],
    title: str = "",
    cover_text: str = "",
    company: str = "",
    location: str = "",
) -> dict:
    """Return the same status dict shape as the Greenhouse HTTP submit."""
    if host_is_blocked(url):
        return {
            "status": "manual",
            "error": "This board needs a logged-in session",
            "url": url,
        }
    if not browser_available():
        return {
            "status": "manual",
            "error": "Apply on the company site",
            "url": url,
        }
    if not resume_path.is_file():
        return {
            "status": "needs_review",
            "error": f"Resume not found at {resume_path}",
            "url": url,
        }
    page = _launch().new_page()
    posts: list[str] = []

    def _remember_post(response) -> None:
        if response.request.method == "POST" and response.status != 0:
            posts.append(f"{response.status} {response.url[:160]}")

    page.on("response", _remember_post)
    try:
        scope = _open_form(page, url, title)
        if host_is_blocked(page.url):
            return {
                "status": "manual",
                "error": "This board needs a logged-in session",
                "url": page.url,
            }
        if scope is None:
            return {
                "status": "needs_review",
                "error": "Opened the company site but did not find an application form",
                "url": page.url,
            }
        # The Greenhouse resume control resets the form, so attach it before the other fields.
        if scope.locator("#resume").count():
            resume_error = _attach_resume(scope, resume_path)
            if resume_error:
                return {"status": "needs_review", "error": resume_error, "url": page.url}
        elif not _attach_native_resume(scope, resume_path):
            return {
                "status": "needs_review",
                "error": "Opened the company form but could not attach the resume.",
                "url": page.url,
            }
        for field_id, value in (
            ("first_name", profile["first_name"]),
            ("last_name", profile["last_name"]),
            ("email", profile["email"]),
            ("phone", profile["phone"]),
        ):
            field = _locator(scope, field_id)
            if field.count() and _is_text_control(field.first) and field.first.is_visible():
                field.first.fill(value)
        _fill_identity(scope, profile)
        if cover_text:
            cover = scope.locator("#cover_letter, textarea[name='cover_letter'], textarea[id*='cover']")
            for index in range(cover.count()):
                control = cover.nth(index)
                if _is_text_control(control) and control.is_visible():
                    control.fill(cover_text[:5000])
                    break
        from autoapply.answers import build_application

        dom_built = build_application(
            _questions_on_form(scope),
            profile,
            company,
            cover_text,
            "",
            resume_path.name,
        )
        if dom_built.disqualify_reason:
            return {
                "status": "skipped",
                "error": dom_built.disqualify_reason,
                "url": page.url,
            }
        known = {item.get("id") for item in fills}
        fills = [*fills, *[item for item in dom_built.fills if item.get("id") not in known]]
        missed = []
        for item in fills:
            if item.get("text"):
                if not _fill_text(scope, item["id"], item["text"]):
                    missed.append(item["id"])
            for choice in item.get("choices") or []:
                if not choice:
                    continue
                field = scope.locator(f"[id='{item['id']}']")
                if field.count() and field.first.get_attribute("role") == "combobox":
                    continue
                if _choose(scope, item["id"], choice) or _check_named(scope, item["id"], choice):
                    continue
                missed.append(f"{item['id']}={choice}")
        _combo_missed, disqualify = _fill_comboboxes(scope, profile, company, location)
        if disqualify:
            return {"status": "skipped", "error": disqualify, "url": page.url}
        _fill_checkboxes(scope, profile)
        invalid = _repair_form(scope, profile)
        if invalid:
            return {
                "status": "needs_review",
                "error": "Company form still invalid: " + "; ".join(invalid[:8]),
                "url": page.url,
            }
        if _captcha_blocking(scope):
            return {
                "status": "needs_review",
                "error": "The company site showed a captcha, so this one was left for you to submit.",
                "url": page.url,
            }
        submit = scope.locator("button[type='submit'], input[type='submit']")
        chosen = None
        for index in range(submit.count()):
            item = submit.nth(index)
            blob = " ".join(
                item.get_attribute(attr) or "" for attr in ("name", "id", "value")
            ).lower()
            if "upload" in blob:
                continue
            chosen = item
            break
        if chosen is None:
            submit = scope.get_by_role("button", name=re.compile(r"submit|apply", re.I))
            chosen = submit.first if submit.count() else None
        if chosen is None:
            return {
                "status": "needs_review",
                "error": "Filled the company form but did not find a submit button",
                "url": page.url,
            }
        sent_at = datetime.now(timezone.utc) - timedelta(seconds=30)
        submitted_form = scope.evaluate(
            """() => {
                const start = document.querySelector("[name='first_name'], #first_name, input[id*='legal_first' i], input[id*='first_name' i], input[placeholder*='First Name' i]");
                const form = start && start.closest("form");
                if (!form) return false;
                const button = form.querySelector("#edit-submit-button")
                    || [...form.querySelectorAll("button[type='submit'], input[type='submit']")]
                        .find(item => !/upload/i.test(`${item.name || ""} ${item.id || ""} ${item.value || ""}`));
                if (button) form.requestSubmit(button);
                else form.requestSubmit();
                return true;
            }"""
        )
        if not submitted_form:
            chosen.click()
        try:
            scope.get_by_text(
                re.compile(rf"{_SUCCESS.pattern}|{_VERIFY.pattern}", re.I)
            ).first.wait_for(timeout=12000)
        except Exception:  # noqa: BLE001
            pass
        result = _result_from_page(page, scope, posts)
        if result is None:
            return _submit_email_code(scope, page, posts, sent_at)
        return result
    except Exception as exc:  # noqa: BLE001
        log.exception("browser apply failed")
        return {"status": "failed", "error": str(exc), "url": url}
    finally:
        page.close()
