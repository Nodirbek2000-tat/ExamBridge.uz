# -*- coding: utf-8 -*-
"""
SATStation Question Bank -> ExamBridge Practice Questions
https://app.satstation.io/question-bank -> /api/import/sat/practice/

Jami ~9601 savol. Har bir savol uchun detail API chaqiriladi.
Progress data/satstation_bank_progress.json ga saqlanadi (davom ettiriladi).

Ishlatish:
  cd E:\SAT_plus\sat
  venv\Scripts\python scripts\scrape_satstation_bank.py
"""

import requests
import json
import time
import re
import sys
import os
from collections import defaultdict

# ── CONFIG ─────────────────────────────────────────────────────────────────────
SAT_BASE   = "https://api.satstation.io/api/v1"
EB_BASE    = "https://exambridge.uz/api"

SAT_EMAIL  = "nodirbekshukurov382@gmail.com"
SAT_PASS   = "Nodirbek2000"

EB_EMAIL   = "nodirbek.shukurov09q@gmail.com"
EB_PASS    = "Nodirbek_2000"

# Sahifadagi savol soni (max 100)
PAGE_SIZE  = 100
# Har nechta savoldan keyin import qilish
IMPORT_BATCH = 100
# Har so'rov orasidagi kutish (soniya)
DELAY = 0.05

DATA_DIR      = os.path.join(os.path.dirname(__file__), "..", "data")
PROGRESS_FILE = os.path.join(DATA_DIR, "satstation_bank_progress.json")

# ── Category map ───────────────────────────────────────────────────────────────
DOMAIN_MAP = {
    "craft_and_structure":                "craft_structure",
    "information_and_ideas":              "info_ideas",
    "standard_english_conventions":       "standard_english",
    "expression_of_ideas":                "expression_ideas",
    "algebra":                            "algebra",
    "advanced_math":                      "advanced_math",
    "problem_solving_and_data_analysis":  "problem_data",
    "geometry_and_trigonometry":          "geometry",
}
MATH_CATS    = {"algebra", "advanced_math", "problem_data", "geometry"}
ENGLISH_CATS = {"craft_structure", "info_ideas", "standard_english", "expression_ideas"}

# ── Auth ───────────────────────────────────────────────────────────────────────
def sat_login():
    r = requests.post(f"{SAT_BASE}/auth/login",
                      json={"email": SAT_EMAIL, "password": SAT_PASS}, timeout=20)
    r.raise_for_status()
    return r.json()["access_token"]


def eb_login():
    r = requests.post(f"{EB_BASE}/auth/login/",
                      json={"email": EB_EMAIL, "password": EB_PASS}, timeout=20)
    r.raise_for_status()
    return r.json().get("access") or r.json().get("token")


# ── Fetch question list (with pagination) ──────────────────────────────────────
def fetch_question_ids(sat_h):
    """Barcha savol ID larini qaytaradi (metadata bilan)."""
    all_items = []
    page = 1
    while True:
        r = requests.get(
            f"{SAT_BASE}/question-bank/questions",
            headers=sat_h,
            params={"page": page, "page_size": PAGE_SIZE},
            timeout=30,
        )
        r.raise_for_status()
        data = r.json()
        items = data.get("items", [])
        if not items:
            break
        all_items.extend(items)
        total = data.get("total", 0)
        total_pages = data.get("total_pages", 1)
        print(f"  Sahifa {page}/{total_pages}: {len(items)} savol | Jami: {len(all_items)}/{total}")
        if page >= total_pages:
            break
        page += 1
        time.sleep(DELAY)
    return all_items


# ── Fetch question detail ──────────────────────────────────────────────────────
def fetch_detail(sat_h, qid):
    """Bitta savol detail ni oladi."""
    r = requests.get(
        f"{SAT_BASE}/question-bank/questions/{qid}",
        headers=sat_h,
        timeout=20,
    )
    if r.status_code == 429:
        print("    429 rate limit - 10s kutilmoqda...")
        time.sleep(10)
        r = requests.get(f"{SAT_BASE}/question-bank/questions/{qid}", headers=sat_h, timeout=20)
    if not r.ok:
        return None
    return r.json()


# ── MathML -> readable text ─────────────────────────────────────────────────────
def mathml_to_text(html_str):
    """
    <math alttext="..."> taglarini alttext qiymati bilan almashtiradi,
    qolgan HTML taglarini olib tashlaydi.
    """
    if not html_str:
        return ""
    # math taglarini alttext bilan almashtir
    def replace_math(m):
        alttext = re.search(r'alttext="([^"]*)"', m.group(0))
        return f" {alttext.group(1)} " if alttext else " [math] "
    result = re.sub(r'<math[^>]*>.*?</math>', replace_math, html_str,
                    flags=re.DOTALL | re.IGNORECASE)
    # Boshqa HTML taglarni olib tashlash
    result = re.sub(r'<[^>]+>', ' ', result)
    result = re.sub(r'\s+', ' ', result)
    return result.strip()


def html_to_plain(html_str):
    """HTML dan plain text oladi (math alttext saqlagan holda)."""
    return mathml_to_text(html_str)


# ── Format question ────────────────────────────────────────────────────────────
def format_question(meta, detail):
    """SATStation detail -> ExamBridge BankQuestion dict."""
    cv = detail.get("current_version") or {}

    # Savol matni (HTML -> plain text, math alttext saqlaydi)
    content = html_to_plain(cv.get("prompt_html") or "") or cv.get("prompt_text") or ""
    if not content:
        return None

    # Passage (reading savollari uchun)
    passage = html_to_plain(cv.get("stimulus_html") or "") or cv.get("stimulus_text") or ""

    # Javob variantlari
    options = cv.get("options") or []
    choice_a = choice_b = choice_c = choice_d = ""
    for opt in options:
        letter = str(opt.get("id", "")).upper()
        text   = html_to_plain(opt.get("html") or "") or opt.get("text") or ""
        if letter == "A":   choice_a = text
        elif letter == "B": choice_b = text
        elif letter == "C": choice_c = text
        elif letter == "D": choice_d = text

    # To'g'ri javob
    ans_spec = cv.get("answer_spec") or {}
    correct  = ""
    if ans_spec.get("kind") == "multiple_choice":
        ids = ans_spec.get("correct_option_ids") or []
        correct = ids[0] if ids else ""
    else:
        # student_produced_response
        answers = ans_spec.get("accepted_answers") or []
        correct = answers[0] if answers else ""

    # Savol turi
    qtype = "MCQ" if meta.get("question_type") == "multiple_choice" else "INPUT"

    # Izoh
    explanation = html_to_plain(cv.get("rationale_html") or "") or cv.get("rationale_text") or ""

    # Category / topic
    domain_raw = (cv.get("domain") or meta.get("domain") or "").lower().strip()
    category   = DOMAIN_MAP.get(domain_raw, "info_ideas")

    skill_name = (cv.get("skill_name") or meta.get("skill_name") or "").strip()
    topic      = skill_name or category

    # Assessment name (source)
    source = meta.get("assessment_name") or meta.get("source") or "satstation"

    # Difficulty
    diff_raw = (cv.get("difficulty") or meta.get("difficulty") or "medium").lower()
    difficulty = diff_raw if diff_raw in ("easy", "medium", "hard") else "medium"

    return {
        "question_type":  qtype,
        "content":        content,
        "passage":        passage,
        "choice_a":       choice_a,
        "choice_b":       choice_b,
        "choice_c":       choice_c,
        "choice_d":       choice_d,
        "correct_answer": correct,
        "difficulty":     difficulty,
        "explanation":    explanation,
        "source":         source,
        "_category":      category,
        "_topic":         topic,
    }


# ── ExamBridge import ──────────────────────────────────────────────────────────
def import_to_eb(eb_tok, subject, category, topic, questions):
    if not questions:
        return 0
    payload = {
        "subject":  subject,
        "category": category,
        "topics":   [{"topic": topic, "questions": questions}],
    }
    r = requests.post(
        f"{EB_BASE}/import/sat/practice/",
        json=payload,
        headers={"Authorization": f"Bearer {eb_tok}"},
        timeout=60,
    )
    if r.ok:
        created = r.json().get("created", len(questions))
        return created
    print(f"    Import xato {r.status_code}: {r.text[:150]}")
    return 0


# ── Progress save/load ─────────────────────────────────────────────────────────
def load_progress():
    os.makedirs(DATA_DIR, exist_ok=True)
    if os.path.exists(PROGRESS_FILE):
        with open(PROGRESS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"done_ids": [], "imported_total": 0}


def save_progress(progress):
    with open(PROGRESS_FILE, "w", encoding="utf-8") as f:
        json.dump(progress, f, ensure_ascii=False)


# ── Main ───────────────────────────────────────────────────────────────────────
def main():
    print("=" * 60)
    print("SATStation Question Bank -> ExamBridge Import")
    print("=" * 60)

    # Login
    print("\nLogin qilinmoqda...")
    sat_tok = sat_login()
    sat_h   = {"Authorization": f"Bearer {sat_tok}", "Content-Type": "application/json"}
    print("  SATStation: OK")

    eb_tok = eb_login()
    print("  ExamBridge: OK")

    # Progress yuklash (davom ettirish uchun)
    progress  = load_progress()
    done_ids  = set(progress["done_ids"])
    imported  = progress["imported_total"]
    print(f"\nAvvalgi progress: {len(done_ids)} savol bajarilib, {imported} import qilingan.")

    # Hamma savol ID larini olish
    print("\nSavol ro'yxati olinmoqda...")
    all_meta = fetch_question_ids(sat_h)
    print(f"Jami: {len(all_meta)} savol")

    # Qolgan savollar (done_ids da yo'q)
    remaining = [m for m in all_meta if m["id"] not in done_ids]
    print(f"Qolgan: {len(remaining)} savol (allaqachon: {len(done_ids)})")

    if not remaining:
        print("Hamma savol allaqachon import qilingan!")
        return

    # Savollarni category+topic bo'yicha to'plash
    buffer: dict[tuple, list] = defaultdict(list)
    total_created = imported

    for i, meta in enumerate(remaining, 1):
        qid = meta["id"]

        # Detail olish
        detail = fetch_detail(sat_h, qid)
        if not detail:
            done_ids.add(qid)
            continue

        # Format
        fmt = format_question(meta, detail)
        if not fmt:
            done_ids.add(qid)
            continue

        category = fmt.pop("_category")
        topic    = fmt.pop("_topic")
        buffer[(category, topic)].append(fmt)
        done_ids.add(qid)

        # Har IMPORT_BATCH savoldan keyin import qil
        total_buffered = sum(len(v) for v in buffer.values())
        if total_buffered >= IMPORT_BATCH:
            for (cat, top), questions in buffer.items():
                subj = "Math" if cat in MATH_CATS else "English"
                created = import_to_eb(eb_tok, subj, cat, top, questions)
                total_created += created
                print(f"  [{i}/{len(remaining)}] Import: {created} savol | {subj}/{cat}/{top}")
            buffer.clear()

            # Progress saqlash
            progress["done_ids"]       = list(done_ids)
            progress["imported_total"] = total_created
            save_progress(progress)

        elif i % 50 == 0:
            print(f"  [{i}/{len(remaining)}] Olingan: {total_buffered} buffer | Jami import: {total_created}")

        time.sleep(DELAY)

    # Qolgan buffer ni import qil
    if buffer:
        for (cat, top), questions in buffer.items():
            subj = "Math" if cat in MATH_CATS else "English"
            created = import_to_eb(eb_tok, subj, cat, top, questions)
            total_created += created
            print(f"  Final import: {created} savol | {subj}/{cat}/{top}")

    # Progress yakunlash
    progress["done_ids"]       = list(done_ids)
    progress["imported_total"] = total_created
    save_progress(progress)

    print(f"\n{'='*60}")
    print(f"YAKUNLANDI: {total_created} ta savol import qilindi.")
    print(f"Progress saqlandi: {PROGRESS_FILE}")
    print("=" * 60)


if __name__ == "__main__":
    main()
