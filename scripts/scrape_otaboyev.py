"""
otaboyev-prep.uz saytidan IELTS Reading testlarini scrape qilib
bizning JSON formatga o'giradi va database ga import qiladi.

Ishlatish:
    python scripts/scrape_otaboyev.py --mode list     # Mavjud testlarni ko'r
    python scripts/scrape_otaboyev.py --mode scrape   # Hammasi: fetch + convert + save
    python scripts/scrape_otaboyev.py --mode import   # DB ga import qil
    python scripts/scrape_otaboyev.py                 # Hammasi ketma-ket
"""

import asyncio
import json
import os
import re
import sys
import argparse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')

BASE_API    = "https://api.otaboyev-prep.uz/api"
OUTPUT_RAW  = "scripts/otaboyev_raw.json"
OUTPUT_CONV = "scripts/otaboyev_converted.json"


# ─────────────────────────────────────────────────────────────────────────────
# STEP 1: API dan ma'lumot olish
# ─────────────────────────────────────────────────────────────────────────────

async def fetch_all():
    from playwright.async_api import async_playwright

    print("[1] Reading testlar ro'yxati olinmoqda...")
    readings_meta = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()

        # Barcha bepul readinglarni ol (page by page)
        for pg in range(0, 10):
            url = f"{BASE_API}/readings?isPremium=false&isGold=false&page={pg}&size=40"
            try:
                resp = await page.goto(url, timeout=15000)
                data = await resp.json()
                items = data.get("content", [])
                if not items:
                    break
                readings_meta.extend(items)
                print(f"   Page {pg}: {len(items)} test ({len(readings_meta)} jami)")
            except Exception as e:
                print(f"   ERR page {pg}: {e}")
                break

        print(f"\n[2] Jami {len(readings_meta)} ta reading topildi. Savollar olinmoqda...\n")

        all_readings = []
        for i, meta in enumerate(readings_meta):
            rid   = meta["id"]
            title = meta["title"].strip()
            parts = meta.get("availableParts", [1, 2, 3])

            reading_parts = []
            for part_num in parts:
                try:
                    resp = await page.goto(
                        f"{BASE_API}/readings/{rid}?part={part_num}", timeout=15000
                    )
                    part_data = await resp.json()
                    key = f"part{part_num}"
                    if key in part_data:
                        reading_parts.append(part_data[key])
                except Exception as e:
                    print(f"   SKIP {title} part{part_num}: {e}")

            if reading_parts:
                all_readings.append({
                    "id":    rid,
                    "title": title,
                    "parts": reading_parts,
                    "timeLimitMinutes": meta.get("timeLimitMinutes", 60),
                })
                safe_title = title.encode('ascii', errors='replace').decode('ascii')
                print(f"   [{i+1}/{len(readings_meta)}] {safe_title} ({len(reading_parts)} part)")

        await browser.close()

    with open(OUTPUT_RAW, "w", encoding="utf-8") as f:
        json.dump(all_readings, f, ensure_ascii=False, indent=2)

    print(f"\nRaw data saqlandi: {OUTPUT_RAW}  ({len(all_readings)} reading)\n")
    return all_readings


# ─────────────────────────────────────────────────────────────────────────────
# STEP 2: Javoblarni extraction + bizning formatga o'girish
# ─────────────────────────────────────────────────────────────────────────────

def _extract_answer(q_type, sub_questions, options, explanation, from_passage):
    """
    Har bir savol turidan correct_answer ni olish.
    """
    q_type = q_type.upper()

    # MCQ — options ichida isCorrect: true
    if q_type in ("MULTIPLE_CHOICE", "MCQ"):
        for opt in (options or []):
            if opt.get("isCorrect"):
                return opt.get("optionKey", "A").upper()
        return "A"

    # TRUE_FALSE_NOT_GIVEN — explanation dan
    if q_type in ("TRUE_FALSE_NOT_GIVEN", "TFNG", "YES_NO_NOT_GIVEN"):
        exp = (explanation or "").upper()
        if "NOT GIVEN" in exp or "NO INFORMATION" in exp or "NOT STATED" in exp:
            return "NOT GIVEN"
        if "FALSE" in exp or "INCORRECT" in exp or "CONTRADICT" in exp:
            return "FALSE"
        if "TRUE" in exp or "CORRECT" in exp or "CONFIRMS" in exp or "AGREES" in exp:
            return "TRUE"
        return "NOT GIVEN"

    # NOTE_COMPLETION / SENTENCE_COMPLETION / SHORT_ANSWER
    # fromPassage dan eng muhim so'zni olish
    if q_type in ("NOTE_COMPLETION", "SENTENCE_COMPLETION", "SHORT_ANSWER", "GAP_FILL", "FILL_BLANK"):
        exp = explanation or ""
        # Explanation dagi birinchi bold/key word
        match = re.search(r'"([^"]{1,30})"', exp)
        if match:
            return match.group(1)
        # fromPassage dan birinchi muhim so'z
        if from_passage:
            words = re.findall(r"\b[A-Z][a-z]+\b|\b[a-z]{4,}\b", from_passage)
            if words:
                return words[0]
        # explanation birinchi gap
        if exp:
            first_sentence = exp.split(".")[0]
            words = first_sentence.split()
            # Oxirgi muhim so'z
            for w in reversed(words):
                w = re.sub(r"[^a-zA-Z]", "", w)
                if len(w) > 3:
                    return w
        return ""

    # MATCHING_INFORMATION — paragraph harfini ol
    if q_type in ("MATCHING_INFORMATION", "MATCHING_HEADINGS", "MATCHING"):
        exp = explanation or ""
        match = re.search(r"[Pp]aragraph\s+([A-H])", exp)
        if match:
            return match.group(1)
        match = re.search(r"[Ss]ection\s+([A-H])", exp)
        if match:
            return match.group(1)
        match = re.search(r"\b([A-H])\b", exp)
        if match:
            return match.group(1)
        return "A"

    return ""


def _map_type(raw_type):
    raw = str(raw_type).upper()
    mapping = {
        "TRUE_FALSE_NOT_GIVEN": "TFNG",
        "YES_NO_NOT_GIVEN":     "YNNG",
        "MULTIPLE_CHOICE":      "MCQ",
        "NOTE_COMPLETION":      "GAP",
        "SENTENCE_COMPLETION":  "SENT",
        "SHORT_ANSWER":         "SHORT",
        "MATCHING_INFORMATION": "MATCH",
        "MATCHING_HEADINGS":    "MATCH",
        "SUMMARY_COMPLETION":   "GAP",
        "DIAGRAM_COMPLETION":   "GAP",
        "TABLE_COMPLETION":     "GAP",
        "FLOW_CHART_COMPLETION":"GAP",
    }
    for k, v in mapping.items():
        if k in raw:
            return v
    return "MCQ"


def convert_raw(raw_file=OUTPUT_RAW):
    try:
        with open(raw_file, encoding="utf-8") as f:
            raw_readings = json.load(f)
    except FileNotFoundError:
        print(f"ERR: {raw_file} topilmadi. Avval --mode scrape qiling.")
        return []

    print(f"[3] {len(raw_readings)} ta reading o'girilmoqda...")
    converted = []
    q_counter = 0

    for reading in raw_readings:
        for part in reading["parts"]:
            passage_title = f"{reading['title']} — Part {part['part']}"
            content = part.get("content", "").strip()
            if not content:
                continue

            questions = []
            for q_group in part.get("questions", []):
                q_type_raw  = q_group.get("type", "MCQ")
                q_type      = _map_type(q_type_raw)
                options     = q_group.get("options", [])
                instruction = q_group.get("instruction", "")

                # Sub-questions (har bir individuel savol)
                sub_qs = q_group.get("questions", [])
                if sub_qs:
                    for sub in sub_qs:
                        exp  = sub.get("explanation", "") or ""
                        frm  = sub.get("fromPassage", "") or ""
                        text = sub.get("questionText", "") or q_group.get("questionText", "")

                        answer = _extract_answer(q_type_raw, [], options, exp, frm)

                        choices = []
                        if q_type == "MCQ":
                            for opt in options:
                                if opt.get("optionText", "").strip():
                                    choices.append({
                                        "option": opt["optionKey"],
                                        "text":   opt["optionText"]
                                    })

                        questions.append({
                            "number":         sub["questionNumber"],
                            "question_type":  q_type,
                            "content":        text.strip(),
                            "instruction":    instruction,
                            "correct_answer": answer,
                            "explanation":    exp,
                            "from_passage":   frm,
                            "choices":        choices,
                        })
                        q_counter += 1
                else:
                    # Savol group o'zi (headings matching etc.)
                    exp  = q_group.get("explanation", "") or ""
                    frm  = q_group.get("fromPassage", "") or ""
                    text = q_group.get("questionText", "")
                    answer = _extract_answer(q_type_raw, sub_qs, options, exp, frm)

                    choices = []
                    if q_type == "MCQ":
                        for opt in options:
                            if opt.get("optionText", "").strip():
                                choices.append({
                                    "option": opt["optionKey"],
                                    "text":   opt["optionText"]
                                })

                    questions.append({
                        "number":         q_group.get("questionNumber", len(questions)+1),
                        "question_type":  q_type,
                        "content":        text.strip(),
                        "instruction":    instruction,
                        "correct_answer": answer,
                        "explanation":    exp,
                        "choices":        choices,
                    })
                    q_counter += 1

            if questions:
                converted.append({
                    "title":         passage_title,
                    "content":       content,
                    "passage_number": part.get("part", 1),
                    "time_limit":    part.get("timeLimitMinutes", 20),
                    "is_standalone": True,
                    "is_premium":    False,
                    "questions":     questions,
                })

    with open(OUTPUT_CONV, "w", encoding="utf-8") as f:
        json.dump(converted, f, ensure_ascii=False, indent=2)

    print(f"   {len(converted)} passage, {q_counter} savol")
    print(f"   Saqlandi: {OUTPUT_CONV}")
    return converted


# ─────────────────────────────────────────────────────────────────────────────
# STEP 3: Database ga import
# ─────────────────────────────────────────────────────────────────────────────

def import_to_db(conv_file=OUTPUT_CONV):
    import django
    django.setup()
    from ielts.models import ReadingPassage, ReadingQuestion, ReadingChoice
    from django.db import transaction

    try:
        with open(conv_file, encoding="utf-8") as f:
            passages = json.load(f)
    except FileNotFoundError:
        print(f"ERR: {conv_file} topilmadi.")
        return

    print(f"[4] {len(passages)} ta passage DB ga import qilinmoqda...")
    ok = skip = 0

    for p_data in passages:
        title = p_data["title"]
        safe = title.encode('ascii', errors='replace').decode('ascii')
        if ReadingPassage.objects.filter(title=title).exists():
            print(f"   SKIP: {safe}")
            skip += 1
            continue

        try:
            with transaction.atomic():
                passage = ReadingPassage.objects.create(
                    title          = title,
                    content        = p_data["content"],
                    passage_number = p_data.get("passage_number", 1),
                    time_limit     = p_data.get("time_limit", 20),
                    is_standalone  = True,
                    is_premium     = False,
                )
                for q_data in p_data.get("questions", []):
                    q = ReadingQuestion.objects.create(
                        passage        = passage,
                        number         = q_data["number"],
                        question_type  = q_data.get("question_type", "MCQ"),
                        content        = q_data.get("content", ""),
                        correct_answer = q_data.get("correct_answer", ""),
                        explanation    = q_data.get("explanation", ""),
                    )
                    for c in q_data.get("choices", []):
                        ReadingChoice.objects.create(
                            question=q, option=c["option"], text=c["text"]
                        )
            ok += 1
            print(f"   OK: {safe} ({len(p_data['questions'])} savol)")
        except Exception as e:
            err = str(e).encode('ascii', errors='replace').decode('ascii')
            print(f"   ERR: {safe}: {err}")

    print(f"\nImport tugadi: {ok} yangi, {skip} o'tkazib yuborildi.")


# ─────────────────────────────────────────────────────────────────────────────
# STEP 4: Tekshirish (list mode)
# ─────────────────────────────────────────────────────────────────────────────

async def list_tests():
    from playwright.async_api import async_playwright
    print("Mavjud bepul reading testlar:")
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        resp = await page.goto(
            f"{BASE_API}/readings?isPremium=false&isGold=false&page=0&size=40"
        )
        data = await resp.json()
        await browser.close()

    for i, item in enumerate(data.get("content", []), 1):
        print(f"  [{i:3}] id={item['id']:5}  parts={item.get('availableParts',[])}  {item['title']}")


# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────

async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["list", "scrape", "convert", "import", "all"],
                        default="all")
    args = parser.parse_args()

    if args.mode == "list":
        await list_tests()

    elif args.mode == "scrape":
        await fetch_all()
        convert_raw()

    elif args.mode == "convert":
        convert_raw()

    elif args.mode == "import":
        # synchronous — run outside asyncio
        pass

    else:  # all
        await fetch_all()
        convert_raw()


def run_import():
    import django; django.setup()
    import_to_db()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["list", "scrape", "convert", "import", "all"],
                        default="all")
    args = parser.parse_args()

    if args.mode == "import":
        run_import()
    elif args.mode in ("list", "scrape", "convert", "all"):
        asyncio.run(main())
        if args.mode in ("all",):
            run_import()
