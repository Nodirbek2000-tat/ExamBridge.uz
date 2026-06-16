#!/usr/bin/env python3
"""
SATStation → ExamBridge Scraper
Fetches SAT full-length tests from app.satstation.io and imports to exambridge.uz
"""
import json
import time
import sys
import re
import urllib.parse
from pathlib import Path

import requests

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# ── Config ────────────────────────────────────────────────────────────────────
SATSTATION_BASE  = "https://app.satstation.io"
EXAMBRIDGE_BASE  = "https://exambridge.uz/api"
IMAGES_DIR       = Path(__file__).parent / "scraped" / "satstation" / "images"
OUTPUT_DIR       = Path(__file__).parent / "scraped" / "satstation"
DELAY            = 0.5

SATSTATION_TOKEN = (
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9"
    ".eyJzdWIiOiIxMTMiLCJleHAiOjE3ODM1MTc4MTQsImlhdCI6MTc4MDkyNTgxNCwidHlwZSI6ImFjY2VzcyIsInJvbGUiOiJzdHVkZW50In0"
    ".oWZZGp-yAuw3ZOhQqq15___XdEz_0XQGtT_oz59LrKE"
)
EXAMBRIDGE_TOKEN = (
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9"
    ".eyJ0b2tlbl90eXBlIjoiYWNjZXNzIiwiZXhwIjoxNzgwOTU3OTM2LCJpYXQiOjE3ODA5NTQzMzYsImp0aSI6IjQxZTg4ZmVlNTIyNzQ0NjVhMTAxZmJiMDlhZjFmMzA1IiwidXNlcl9pZCI6IjEifQ"
    ".hYqjnWaItNgabl2cR_F34yxfAq8leCFH93WB0mp0K0Y"
)

MONTH_MAP = {
    "january": 1, "february": 2, "march": 3,    "april": 4,
    "may": 5,     "june": 6,     "july": 7,     "august": 8,
    "september": 9, "october": 10, "november": 11, "december": 12,
}

DIFF_MAP = {
    "easy": "EASY", "easier": "EASY",
    "medium": "MEDIUM", "standard": "MEDIUM",
    "hard": "HARD", "harder": "HARD",
}

# SATStation (section, module, difficulty) → ExamBridge payload key
ADAPTIVE_MODULE_MAP = {
    ("reading_writing", "module_1", "standard"): "english_m1",
    ("reading_writing", "module_2", "easier"):   "english_m2_easy",
    ("reading_writing", "module_2", "harder"):   "english_m2_hard",
    ("math",           "module_1", "standard"):  "math_m1",
    ("math",           "module_2", "easier"):    "math_m2_easy",
    ("math",           "module_2", "harder"):    "math_m2_hard",
}

# Non-adaptive (4 standard modules) → INDIVIDUAL mode keys
INDIVIDUAL_MODULE_MAP = {
    ("reading_writing", "module_1", "standard"): "english_m1",
    ("reading_writing", "module_2", "standard"): "english_m2",
    ("math",           "module_1", "standard"):  "math_m1",
    ("math",           "module_2", "standard"):  "math_m2",
}


# ── HTTP sessions ─────────────────────────────────────────────────────────────
def make_ss_session():
    s = requests.Session()
    s.headers["Authorization"] = f"Bearer {SATSTATION_TOKEN}"
    s.headers["Accept"] = "application/json"
    return s

def make_eb_session():
    s = requests.Session()
    s.headers["Authorization"] = f"Bearer {EXAMBRIDGE_TOKEN}"
    s.headers["Content-Type"] = "application/json"
    return s


# ── Image download ────────────────────────────────────────────────────────────
def download_image(relative_url: str, test_id: int, ss: requests.Session) -> str:
    """Download image from satstation, return full https URL for embedding."""
    if not relative_url:
        return ""
    # Strip query params from URL for filename
    clean_path = relative_url.split("?")[0]
    full_url   = f"{SATSTATION_BASE}{clean_path}"

    # Save locally
    filename  = Path(urllib.parse.urlparse(clean_path).path).name
    save_dir  = IMAGES_DIR / str(test_id)
    save_path = save_dir / filename

    if not save_path.exists():
        try:
            resp = ss.get(full_url, timeout=20)
            resp.raise_for_status()
            save_dir.mkdir(parents=True, exist_ok=True)
            save_path.write_bytes(resp.content)
        except Exception as e:
            print(f"    [WARN] image download failed {relative_url}: {e}")

    return full_url   # use the satstation URL directly in content


# ── Question conversion ───────────────────────────────────────────────────────
def convert_question(q: dict, test_id: int, ss: requests.Session) -> dict:
    raw_type = q.get("question_type", "")
    if raw_type == "student_produced_response":
        qtype = "INPUT"
    else:
        qtype = "MCQ"

    difficulty = DIFF_MAP.get((q.get("difficulty") or "medium").lower(), "MEDIUM")

    # ── Passage (Reading/Writing) ──
    passage_html = ""
    passage_obj  = q.get("passage")
    if passage_obj:
        passage_html = (passage_obj.get("content") or "").strip()

    # ── Question text ──
    question_text = (q.get("question_text") or "").strip()

    # ── Question image ──
    img_url = q.get("question_image_url")
    img_html = ""
    if img_url:
        full = download_image(img_url, test_id, ss)
        if full:
            img_html = f'<img src="{full}" alt="question image" style="max-width:100%;margin:8px 0">'

    # Build content: question text + optional image
    content_parts = [question_text]
    if img_html:
        content_parts.append(img_html)
    content = "\n".join(p for p in content_parts if p)

    # ── Correct answer ──
    correct = q.get("correct_answer") or []
    if isinstance(correct, list):
        correct = correct[0] if correct else ""
    correct = str(correct).strip()

    # ── Choices ──
    choices = []
    if qtype == "MCQ":
        for opt in sorted(q.get("options") or [], key=lambda x: x.get("id", "")):
            opt_text = (opt.get("text") or "").strip()
            opt_img  = opt.get("image_url")
            if opt_img:
                fi = download_image(opt_img, test_id, ss)
                if fi:
                    opt_text += f'<img src="{fi}" alt="" style="max-width:100%">'
            choices.append({"option": opt["id"], "text": opt_text})

    result = {
        "number":        q.get("question_number", 1),
        "question_type": qtype,
        "difficulty":    difficulty,
        "passage":       passage_html,
        "content":       content,
        "correct_answer": correct,
        "explanation":   (q.get("explanation") or "").strip(),
    }
    if choices:
        result["choices"] = choices
    return result


# ── Detect adaptive vs non-adaptive ──────────────────────────────────────────
def is_adaptive(modules: list) -> bool:
    """True if test has easier/harder module_2 variants (adaptive)."""
    diffs = {m.get("difficulty") for m in modules if m.get("module") == "module_2"}
    return "easier" in diffs or "harder" in diffs


# ── Test conversion ───────────────────────────────────────────────────────────
def convert_test(detail: dict, list_item: dict, test_id: int, ss: requests.Session) -> dict:
    title      = list_item.get("display_title") or list_item.get("title", "")
    month_str  = (list_item.get("administration_month") or "").lower()
    year       = list_item.get("administration_year") or None
    month      = MONTH_MAP.get(month_str) if month_str else None

    # Bluebook / practice tests with no date
    if not year:
        year = 2024
        # Try to extract test number from title for month (e.g. "Bluebook Practice Test 4" → month=4)
        m = re.search(r"\b(\d+)\b", title)
        month = int(m.group(1)) if m and 1 <= int(m.group(1)) <= 12 else 1
    if not month:
        month = 1
    is_premium = list_item.get("is_premium", False)

    variant = list_item.get("variant_label") or ""
    is_intl = bool(re.search(r"int(ernational|l)", variant, re.I))

    # Derive form letter: last uppercase letter/code in variant
    form = "A"
    for part in reversed(variant.split()):
        if re.match(r"^[A-Z][0-9]?$|^V[0-9]+$", part):
            form = part
            break

    modules  = detail.get("modules", [])
    adaptive = is_adaptive(modules)
    mod_map  = ADAPTIVE_MODULE_MAP if adaptive else INDIVIDUAL_MODULE_MAP
    test_mode = "FULL" if adaptive else "INDIVIDUAL"

    payload = {
        "name":           title,
        "year":           year,
        "month":          month,
        "form":           form,
        "is_international": is_intl,
        "is_premium":     is_premium,
        "test_mode":      test_mode,
    }

    for module in modules:
        sec  = module.get("section", "")
        mod  = module.get("module", "")
        diff = module.get("difficulty", "")
        key  = mod_map.get((sec, mod, diff))
        if not key:
            print(f"    [WARN] no mapping for {sec} {mod} {diff}")
            continue
        qs = [convert_question(q, test_id, ss) for q in module.get("questions", [])]
        payload[key] = qs
        print(f"    {key}: {len(qs)} questions")

    return payload


# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    IMAGES_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    ss = make_ss_session()
    eb = make_eb_session()

    # ── Fetch test list ──
    resp = ss.get(f"{SATSTATION_BASE}/api/v1/tests", timeout=15)
    resp.raise_for_status()
    all_tests = resp.json().get("items", [])
    print(f"Found {len(all_tests)} tests on SATStation\n")

    ok = fail = skip = 0

    for idx, test in enumerate(all_tests, 1):
        tid   = test["id"]
        title = test.get("display_title") or test.get("title", f"Test_{tid}")
        prem  = test.get("is_premium", False)

        print(f"\n[{idx}/{len(all_tests)}] {title} (id={tid}) premium={prem}")

        # Fetch detail
        detail_resp = ss.get(f"{SATSTATION_BASE}/api/v1/tests/{tid}", timeout=20)
        if detail_resp.status_code == 402:
            print("  [SKIP] 402 Payment Required (premium)")
            skip += 1
            time.sleep(DELAY)
            continue
        detail_resp.raise_for_status()
        detail = detail_resp.json()

        modules = detail.get("modules", [])
        if not modules:
            print("  [SKIP] no modules (premium or empty)")
            skip += 1
            time.sleep(DELAY)
            continue

        # Convert
        payload = convert_test(detail, test, tid, ss)

        # Save JSON
        out_path = OUTPUT_DIR / f"{tid}.json"
        out_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"  Saved → {out_path.name}")

        # Import to ExamBridge
        r = eb.post(f"{EXAMBRIDGE_BASE}/import/sat/mock/", json=payload, timeout=60)
        if r.status_code in (200, 201):
            d = r.json()
            print(f"  ✓ IMPORTED: test_id={d.get('test_id')} questions={d.get('total_questions')} mode={d.get('test_mode')}")
            ok += 1
        else:
            print(f"  ✗ FAIL {r.status_code}: {r.text[:300]}")
            fail += 1

        time.sleep(DELAY)

    print(f"\n{'='*50}")
    print(f"Done: {ok} imported, {fail} failed, {skip} skipped (premium)")
    print(f"JSON files saved to: {OUTPUT_DIR}")
    print(f"Images saved to:     {IMAGES_DIR}")


if __name__ == "__main__":
    main()
