#!/usr/bin/env python3
"""
IELTStation → ExamBridge Scraper
Fetches all 217 reading + 187 listening tests from api.otaboyev-prep.uz
and imports them into exambridge.uz in the correct format.

Usage:
  python ieltstation_scraper.py --source-token YOUR_IELTSTATION_JWT
                                  --target-token YOUR_EXAMBRIDGE_JWT
                                  [--mode reading|listening|all]
                                  [--dry-run]
"""

import argparse
import json
import time
import sys
from pathlib import Path

import requests

# Fix Windows console encoding
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# ─── Config ───────────────────────────────────────────────────────────────────
SOURCE_BASE = "https://api.otaboyev-prep.uz/api"
TARGET_BASE = "https://exambridge.uz/api"   # change to http://localhost:8000/api for local
OUTPUT_DIR  = Path(__file__).parent / "scraped" / "ieltstation"

# IELTStation question type → ExamBridge question type
TYPE_MAP = {
    "TRUE_FALSE_NOT_GIVEN":          "TFNG",
    "YES_NO_NOT_GIVEN":              "YNNG",
    "MATCHING_HEADINGS":             "MATCH",
    "MATCHING_INFORMATION":          "MINFO",
    "MATCHING_NAMES":                "MINFO",
    "MATCHING_FEATURES":             "MINFO",
    "MATCHING_SENTENCE_ENDINGS":     "SENT",
    "MULTIPLE_CHOICE":               "MCQ",
    "MULTIPLE_CHOICE_MULTIPLE":      "MULTI",
    "NOTE_COMPLETION":               "GAP",
    "SUMMARY_COMPLETION":            "GAP",
    "SUMMARY_COMPLETION_DRAG_DROP":  "GAP",
    "TABLE_COMPLETION":              "TABLE",
    "FORM_COMPLETION":               "TABLE",
    "FLOW_CHART_COMPLETION":         "TABLE",
    "SENTENCE_COMPLETION":           "SENT",
    "SHORT_ANSWER_QUESTION":         "SHORT",
    "DIAGRAM_LABELLING":             "GAP",
    "MAP_LABELLING":                 "MAP",
    "PLAN_LABELLING":                "MAP",
    "PICK_FROM_A_LIST":              "MULTI",
}

DELAY = 0.4  # seconds between API calls (be polite)

# ─── HTTP sessions ─────────────────────────────────────────────────────────────
def make_source_session(token: str) -> requests.Session:
    s = requests.Session()
    s.headers.update({
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
    })
    return s

def make_target_session(token: str) -> requests.Session:
    s = requests.Session()
    s.headers.update({
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    })
    return s

# ─── Fetch helpers ──────────────────────────────────────────────────────────────
def fetch_all_tests(session, endpoint: str) -> list:
    """Paginate through all tests and return full list."""
    all_tests = []
    page = 0
    while True:
        url = f"{SOURCE_BASE}/{endpoint}?type=REAL_EXAM&page={page}&size=50"
        try:
            resp = session.get(url, timeout=15)
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            print(f"  [ERROR] fetch page {page}: {e}")
            break

        content = data.get("content", [])
        all_tests.extend(content)
        print(f"  page {page}: got {len(content)} items  (total so far: {len(all_tests)})")

        if data.get("last", True):
            break
        page += 1
        time.sleep(DELAY)

    return all_tests

def fetch_detail(session, endpoint: str, test_id: int) -> dict | None:
    url = f"{SOURCE_BASE}/{endpoint}/{test_id}"
    try:
        resp = session.get(url, timeout=20)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        print(f"    [WARN] detail {test_id} failed: {e}")
        return None

# ─── Conversion helpers ────────────────────────────────────────────────────────
def _map_type(raw: str) -> str:
    t = TYPE_MAP.get(raw.upper().replace(" ", "_"), None)
    if t is None:
        print(f"    [WARN] unknown question type: '{raw}' — defaulting to GAP")
        return "GAP"
    return t

def _extract_correct_answer(sq: dict, group: dict, qtype: str) -> str:
    """
    IELTStation stores correctAnswer=null for most types.
    Strategy:
      MCQ/MINFO/MATCH  → group.options[isCorrect=True].optionKey
      MULTI            → pipe-joined group.options[isCorrect=True] keys
      TFNG/YNNG        → sub-question options OR sub-question-level isCorrect option key
      GAP/TABLE/SENT…  → fromPassage (passage excerpt containing the answer)
    """
    # Explicit answer wins if present
    if sq.get("correctAnswer") is not None:
        return str(sq["correctAnswer"]).strip()

    group_opts = group.get("options", [])
    sq_opts    = sq.get("options") or []

    if qtype == "MCQ":
        # group-level options, single isCorrect=True
        for o in group_opts:
            if o.get("isCorrect"):
                return o.get("optionKey", "")
        for o in sq_opts:
            if o.get("isCorrect"):
                return o.get("optionKey", "")

    if qtype == "MULTI":
        keys = [o["optionKey"] for o in group_opts if o.get("isCorrect")]
        if not keys:
            keys = [o["optionKey"] for o in sq_opts if o.get("isCorrect")]
        return "|".join(keys)

    if qtype in ("MATCH", "MINFO"):
        for o in group_opts:
            if o.get("isCorrect"):
                return o.get("optionKey", "")

    if qtype in ("TFNG", "YNNG"):
        # Sometimes per-sub-question options carry T/F/NG
        for o in sq_opts:
            if o.get("isCorrect"):
                return o.get("optionKey", "")
        # Fallback: explanation often says "TRUE" / "FALSE" / "NOT GIVEN" at start
        expl = (sq.get("explanation") or "").strip().upper()
        for kw in ("TRUE", "FALSE", "NOT GIVEN", "YES", "NO"):
            if expl.startswith(kw):
                return kw

    # GAP / TABLE / SENT / SHORT / NOTE_COMPLETION — fromPassage as answer hint
    fp = (sq.get("fromPassage") or "").strip()
    if fp:
        return fp[:300]   # store passage excerpt as answer; admin can refine later

    return ""


def convert_question_group(group: dict, start_num: int) -> list:
    """
    One question group in IELTStation may contain multiple sub-questions.
    Returns list of ExamBridge question dicts.
    """
    raw_type    = group.get("type", "")
    qtype       = _map_type(raw_type)
    instruction = (group.get("instruction") or "").strip()
    group_opts  = group.get("options", [])
    sub_qs      = group.get("questions", [])

    result = []
    first  = True

    for sq in sub_qs:
        correct = _extract_correct_answer(sq, group, qtype)
        sq_opts = sq.get("options") or group_opts

        q = {
            "number":            sq.get("questionNumber", start_num),
            "question_type":     qtype,
            "content":           (sq.get("questionText") or "").strip(),
            "correct_answer":    correct,
            "explanation":       (sq.get("explanation") or group.get("explanation") or "").strip(),
            "group_instruction": instruction if first else "",
        }
        first = False

        # Choices for option-based types
        if qtype in ("MCQ", "MULTI", "MATCH", "MINFO") and sq_opts:
            q["choices"] = [
                {"option": o.get("optionKey", ""), "text": (o.get("optionText") or "").strip()}
                for o in sorted(sq_opts, key=lambda x: x.get("orderIndex", 0))
            ]

        if qtype == "MULTI":
            q["max_selections"] = max(1, len([o for o in sq_opts if o.get("isCorrect")]))

        result.append(q)
        start_num += 1

    return result


def convert_part_to_dict(part: dict, global_title: str, part_num: int) -> dict:
    """One IELTStation reading part → ExamBridge parts[] entry."""
    questions = []
    q_num = 1
    for group in part.get("questions", []):
        converted = convert_question_group(group, q_num)
        questions.extend(converted)
        q_num += len(converted)

    return {
        "passage_number": part_num,
        "title": (part.get("title") or f"{global_title} – Part {part_num}").strip(),
        "content": (part.get("content") or "").strip(),
        "questions": questions,
    }


def convert_reading_test(detail: dict, list_item: dict) -> dict:
    """Full IELTStation reading test → ExamBridge FULL_MOCK import payload."""
    global_title = (detail.get("globalTitle") or list_item.get("title", "")).strip()
    is_premium   = list_item.get("isPremium", False) or list_item.get("isGold", False)
    difficulty   = list_item.get("difficulty", "MEDIUM")

    parts = []
    for p in range(1, 4):
        part_data = detail.get(f"part{p}")
        if part_data:
            parts.append(convert_part_to_dict(part_data, global_title, p))

    if len(parts) == 1:
        # Single passage — flat format
        part = parts[0]
        return {
            "title":          part["title"],
            "content":        part["content"],
            "passage_number": part["passage_number"],
            "difficulty":     difficulty,
            "time_limit":     20,
            "is_standalone":  True,
            "is_premium":     is_premium,
            "questions":      part["questions"],
        }

    # Multi-part — FULL_MOCK format
    return {
        "title":      global_title,
        "test_type":  "FULL_MOCK",
        "difficulty": difficulty,
        "is_premium": is_premium,
        "parts":      parts,
    }


def convert_section_to_dict(section: dict, global_title: str, sec_num: int,
                            audio_url: str = "") -> dict:
    """One IELTStation listening section → ExamBridge sections[] entry."""
    questions = []
    q_num = 1
    for group in section.get("questions", []):
        converted = convert_question_group(group, q_num)
        questions.extend(converted)
        q_num += len(converted)

    return {
        "section_number": sec_num,
        "title":      (section.get("title") or f"{global_title} – Part {sec_num}").strip(),
        "audio_url":  section.get("audioUrl") or audio_url,
        "transcript": (section.get("transcript") or "").strip(),
        "questions":  questions,
    }


def convert_listening_test(detail: dict, list_item: dict) -> dict:
    """Full IELTStation listening test → ExamBridge import payload."""
    global_title = (detail.get("globalTitle") or detail.get("title") or list_item.get("title", "")).strip()
    is_premium   = list_item.get("isPremium", False) or list_item.get("isGold", False)
    difficulty   = list_item.get("difficulty", "MEDIUM")
    # Audio is at the top-level of the listening detail (one file for all parts)
    audio_url    = detail.get("audioUrl") or list_item.get("audioUrl", "")

    # Build sections list from part1..part4 or sections[]
    sections_raw = detail.get("sections", [])
    if not sections_raw:
        for p in range(1, 5):
            s = detail.get(f"part{p}") or detail.get(f"section{p}")
            if s:
                sections_raw.append(s)
    if not sections_raw:
        sections_raw = [detail]

    sections = [convert_section_to_dict(s, global_title, i + 1, audio_url)
                for i, s in enumerate(sections_raw)]

    if len(sections) == 1:
        sec = sections[0]
        return {
            "title":          sec["title"],
            "section_number": sec["section_number"],
            "audio_url":      audio_url,
            "transcript":     sec["transcript"],
            "difficulty":     difficulty,
            "is_standalone":  True,
            "is_premium":     is_premium,
            "questions":      sec["questions"],
        }

    return {
        "title":      global_title,
        "audio_url":  audio_url,
        "difficulty": difficulty,
        "is_premium": is_premium,
        "sections":   [dict(s, audio_url=audio_url) for s in sections],
    }

# ─── Import helpers ─────────────────────────────────────────────────────────────
def import_to_exambridge(target: requests.Session, endpoint: str,
                         payload: dict) -> tuple[int, dict]:
    url = f"{TARGET_BASE}/{endpoint}"
    try:
        resp = target.post(url, json=payload, timeout=30)
        return resp.status_code, resp.json()
    except Exception as e:
        return 0, {"error": str(e)}

# ─── Main pipelines ────────────────────────────────────────────────────────────
def run_readings(source: requests.Session, target: requests.Session,
                 dry_run: bool, save_json: bool):
    print("\n========== READING TESTS ==========")
    tests = fetch_all_tests(source, "readings")
    print(f"Total reading tests: {len(tests)}\n")

    ok = fail = skip = 0
    out_dir = OUTPUT_DIR / "reading"
    out_dir.mkdir(parents=True, exist_ok=True)

    for idx, test in enumerate(tests, 1):
        test_id    = test["id"]
        test_title = test.get("title", f"Test_{test_id}")
        is_premium = test.get("isPremium", False) or test.get("isGold", False)

        print(f"[{idx}/{len(tests)}] {test_title} (id={test_id}) premium={is_premium}")

        detail = fetch_detail(source, "readings", test_id)
        if not detail:
            skip += 1
            continue

        payload = convert_reading_test(detail, test)
        n_parts = len(payload.get("parts", [])) or 1

        if save_json:
            fname = out_dir / f"{test_id}.json"
            fname.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

        if dry_run:
            total_q = sum(len(p["questions"]) for p in payload.get("parts", [payload]))
            print(f"   {n_parts} part(s), {total_q} questions [DRY RUN]")
            continue

        status, result = import_to_exambridge(target, "import/ielts/reading/", payload)
        if status in (200, 201):
            print(f"   OK ({n_parts} parts)")
            ok += 1
        else:
            print(f"   FAIL {status} — {result}")
            fail += 1

        time.sleep(DELAY)

    print(f"\nReading done: {ok} imported, {fail} failed, {skip} skipped")


def run_listenings(source: requests.Session, target: requests.Session,
                   dry_run: bool, save_json: bool):
    print("\n========== LISTENING TESTS ==========")
    tests = fetch_all_tests(source, "listenings")
    print(f"Total listening tests: {len(tests)}\n")

    ok = fail = skip = 0
    out_dir = OUTPUT_DIR / "listening"
    out_dir.mkdir(parents=True, exist_ok=True)

    for idx, test in enumerate(tests, 1):
        test_id    = test["id"]
        test_title = test.get("title", f"Test_{test_id}")
        is_premium = test.get("isPremium", False) or test.get("isGold", False)

        print(f"[{idx}/{len(tests)}] {test_title} (id={test_id}) premium={is_premium}")

        detail = fetch_detail(source, "listenings", test_id)
        if not detail:
            skip += 1
            continue

        payload = convert_listening_test(detail, test)
        n_secs = len(payload.get("sections", [])) or 1

        if save_json:
            fname = out_dir / f"{test_id}.json"
            fname.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

        if dry_run:
            total_q = sum(len(s["questions"]) for s in payload.get("sections", [payload]))
            print(f"   {n_secs} section(s), {total_q} questions [DRY RUN]")
            continue

        status, result = import_to_exambridge(target, "import/ielts/listening/", payload)
        if status in (200, 201):
            print(f"   OK ({n_secs} sections)")
            ok += 1
        else:
            print(f"   FAIL {status} — {result}")
            fail += 1

        time.sleep(DELAY)

    print(f"\nListening done: {ok} imported, {fail} failed, {skip} skipped")

# ─── CLI ───────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="IELTStation → ExamBridge scraper")
    parser.add_argument("--source-token", required=True,
                        help="JWT token from ieltstation.com (get from browser localStorage or Network tab)")
    parser.add_argument("--target-token", required=True,
                        help="JWT token for exambridge.uz admin account")
    parser.add_argument("--mode", choices=["reading", "listening", "all"], default="all")
    parser.add_argument("--dry-run", action="store_true",
                        help="Fetch and convert but do NOT import to ExamBridge")
    parser.add_argument("--save-json", action="store_true", default=True,
                        help="Save converted JSON files to data/scraped/ieltstation/")
    parser.add_argument("--target-base", default="http://localhost:8000/api",
                        help="ExamBridge API base URL")
    args = parser.parse_args()

    global TARGET_BASE
    TARGET_BASE = args.target_base.rstrip("/")

    source = make_source_session(args.source_token)
    target = make_target_session(args.target_token)

    # Quick auth check on source
    me = source.get(f"{SOURCE_BASE}/auth/me", timeout=10)
    if me.status_code != 200:
        print(f"[ERROR] IELTStation token invalid (status {me.status_code}). Get a fresh token.")
        sys.exit(1)
    print(f"Authenticated as: {me.json().get('email', '?')}")

    if args.mode in ("reading", "all"):
        run_readings(source, target, args.dry_run, args.save_json)

    if args.mode in ("listening", "all"):
        run_listenings(source, target, args.dry_run, args.save_json)

    print("\nAll done!")

if __name__ == "__main__":
    main()
