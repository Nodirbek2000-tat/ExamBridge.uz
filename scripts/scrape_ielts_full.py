"""
otaboyev-prep.uz dan BARCHA Reading va Listening testlarini scrape qilib
bizning DB ga import qiladi.

Ishlatish:
    python scripts/scrape_ielts_full.py --mode reading   # faqat reading
    python scripts/scrape_ielts_full.py --mode listening # faqat listening
    python scripts/scrape_ielts_full.py                  # hammasi

API:
    Reading  : https://api.otaboyev-prep.uz/api/readings
    Listening: https://api.otaboyev-prep.uz/api/listenings
"""

import json
import os
import sys
import re
import time
import argparse
import urllib.request
import urllib.error

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')

BASE_API = "https://api.otaboyev-prep.uz/api"
HEADERS  = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}

RAW_READING   = "scripts/raw_readings.json"
RAW_LISTENING = "scripts/raw_listenings.json"


# ─────────────────────────────────────────────────────────────────────────────
# HTTP helper
# ─────────────────────────────────────────────────────────────────────────────

def _get(url, retries=3):
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=20) as r:
                return json.loads(r.read().decode("utf-8", errors="replace"))
        except urllib.error.HTTPError as e:
            print(f"   HTTP {e.code}: {url}")
            return None
        except Exception as e:
            if attempt < retries - 1:
                time.sleep(2)
            else:
                print(f"   ERR ({attempt+1}/{retries}): {url} -> {e}")
                return None


def _safe(text):
    return str(text).encode("ascii", errors="replace").decode("ascii")


# ─────────────────────────────────────────────────────────────────────────────
# READING SCRAPE
# ─────────────────────────────────────────────────────────────────────────────

def _map_reading_type(raw):
    raw = str(raw).upper()
    m = {
        "TRUE_FALSE_NOT_GIVEN":  "TFNG",
        "YES_NO_NOT_GIVEN":      "YNNG",
        "MULTIPLE_CHOICE":       "MCQ",
        "NOTE_COMPLETION":       "GAP",
        "SENTENCE_COMPLETION":   "SENT",
        "SHORT_ANSWER":          "SHORT",
        "MATCHING_INFORMATION":  "MATCH",
        "MATCHING_HEADINGS":     "MATCH",
        "SUMMARY_COMPLETION":    "GAP",
        "DIAGRAM_COMPLETION":    "GAP",
        "TABLE_COMPLETION":      "GAP",
        "FLOW_CHART_COMPLETION": "GAP",
    }
    for k, v in m.items():
        if k in raw:
            return v
    return "MCQ"


def _extract_options(options):
    """MCQ uchun choices list qaytaradi."""
    choices = []
    for opt in (options or []):
        text = opt.get("optionText", "").strip()
        if text:
            choices.append({"option": opt.get("optionKey", "A"), "text": text})
    return choices


def scrape_readings():
    print("\n===== READING SCRAPE =====")
    meta_list = []
    page = 0
    while True:
        url  = f"{BASE_API}/readings?isPremium=false&isGold=false&page={page}&size=40"
        data = _get(url)
        if not data:
            break
        items = data.get("content", [])
        if not items:
            break
        meta_list.extend(items)
        print(f"  Page {page}: {len(items)} reading ({len(meta_list)} jami)")
        if data.get("last", True):
            break
        page += 1

    print(f"\nJami {len(meta_list)} reading topildi. Detail olinmoqda...\n")
    all_readings = []

    for i, meta in enumerate(meta_list):
        rid    = meta["id"]
        title  = meta["title"].strip()
        parts  = meta.get("availableParts", [1, 2, 3])
        reading_parts = []

        for part_num in parts:
            detail = _get(f"{BASE_API}/readings/{rid}?part={part_num}")
            if not detail:
                continue
            key = f"part{part_num}"
            part_data = detail.get(key)
            if not part_data:
                continue

            # questions oling
            content = part_data.get("content", "").strip()
            if not content:
                continue

            questions = []
            for qg in part_data.get("questions", []):
                qtype_raw = qg.get("type", "MCQ")
                qtype     = _map_reading_type(qtype_raw)
                options   = qg.get("options", [])
                instr     = qg.get("instruction", "") or ""

                sub_qs = qg.get("questions", [])
                if sub_qs:
                    for sub in sub_qs:
                        num    = sub.get("questionNumber", 0)
                        text   = (sub.get("questionText") or qg.get("questionText") or "").strip()
                        answer = (sub.get("correctAnswer") or "").strip()
                        exp    = (sub.get("explanation") or "").strip()
                        frm    = (sub.get("fromPassage") or "").strip()

                        # Fallback: MCQ options dan isCorrect
                        if not answer and qtype == "MCQ":
                            sub_opts = sub.get("options") or options
                            for opt in sub_opts:
                                if opt.get("isCorrect"):
                                    answer = opt.get("optionKey", "A")
                                    break

                        choices = _extract_options(sub.get("options") or (options if qtype == "MCQ" else []))

                        questions.append({
                            "number":         num,
                            "question_type":  qtype,
                            "content":        text,
                            "instruction":    instr,
                            "correct_answer": answer,
                            "explanation":    exp,
                            "from_passage":   frm,
                            "choices":        choices,
                        })
                else:
                    num    = qg.get("questionNumber", len(questions) + 1)
                    text   = (qg.get("questionText") or "").strip()
                    answer = (qg.get("correctAnswer") or "").strip()
                    exp    = (qg.get("explanation") or "").strip()
                    choices = _extract_options(options if qtype == "MCQ" else [])

                    questions.append({
                        "number":         num,
                        "question_type":  qtype,
                        "content":        text,
                        "instruction":    instr,
                        "correct_answer": answer,
                        "explanation":    exp,
                        "choices":        choices,
                    })

            if questions:
                reading_parts.append({
                    "part":            part_num,
                    "title":           part_data.get("title", f"Part {part_num}"),
                    "content":         content,
                    "time_limit":      meta.get("timeLimitMinutes", 20),
                    "questions":       questions,
                })

        if reading_parts:
            all_readings.append({
                "id":    rid,
                "title": title,
                "parts": reading_parts,
            })
            print(f"  [{i+1}/{len(meta_list)}] {_safe(title)} ({len(reading_parts)} part, "
                  f"{sum(len(p['questions']) for p in reading_parts)} savol)")
        else:
            print(f"  [{i+1}/{len(meta_list)}] SKIP {_safe(title)} (part yo'q)")

        time.sleep(0.3)

    with open(RAW_READING, "w", encoding="utf-8") as f:
        json.dump(all_readings, f, ensure_ascii=False, indent=2)

    total_q = sum(sum(len(p["questions"]) for p in r["parts"]) for r in all_readings)
    print(f"\nReading saqlandi: {RAW_READING}")
    print(f"  {len(all_readings)} reading, {total_q} savol\n")
    return all_readings


# ─────────────────────────────────────────────────────────────────────────────
# LISTENING SCRAPE
# ─────────────────────────────────────────────────────────────────────────────

def _map_listening_type(raw):
    raw = str(raw).upper()
    if "NOTE_COMPLETION" in raw or "SUMMARY" in raw or "DIAGRAM" in raw \
            or "TABLE" in raw or "FLOW" in raw or "SENTENCE_COMPLETION" in raw \
            or "FILL" in raw:
        return "GAP"
    if "MULTIPLE_CHOICE" in raw or "MCQ" in raw:
        return "MCQ"
    if "MATCHING" in raw:
        return "MATCH"
    if "SHORT" in raw:
        return "SHORT"
    if "PLAN" in raw or "MAP" in raw or "LABEL" in raw:
        return "MAP"
    return "GAP"


def scrape_listenings():
    print("\n===== LISTENING SCRAPE =====")
    meta_list = []
    page = 0
    while True:
        url  = f"{BASE_API}/listenings?isPremium=false&isGold=false&page={page}&size=40"
        data = _get(url)
        if not data:
            break
        items = data.get("content", [])
        if not items:
            break
        meta_list.extend(items)
        print(f"  Page {page}: {len(items)} listening ({len(meta_list)} jami)")
        if data.get("last", True):
            break
        page += 1

    print(f"\nJami {len(meta_list)} listening topildi. Detail olinmoqda...\n")
    all_listenings = []

    for i, meta in enumerate(meta_list):
        lid       = meta["id"]
        title     = meta["title"].strip()
        sections  = meta.get("availableSections", [1, 2, 3, 4])
        audio_url = ""
        test_sections = []

        for sec_num in sections:
            detail = _get(f"{BASE_API}/listenings/{lid}?section={sec_num}")
            if not detail:
                continue

            # Audio URL faqat bir marta
            if not audio_url and detail.get("audioUrl"):
                audio_url = detail["audioUrl"]

            key = f"part{sec_num}"
            sec_data = detail.get(key)
            if not sec_data:
                continue

            questions = []
            for qg in sec_data.get("questions", []):
                qtype_raw = qg.get("type", "GAP")
                qtype     = _map_listening_type(qtype_raw)
                options   = qg.get("options", [])
                instr     = (qg.get("instruction") or "").strip()
                parent_text = (qg.get("questionText") or "").strip()

                sub_qs = qg.get("questions", [])
                if sub_qs:
                    for sub in sub_qs:
                        num    = sub.get("questionNumber", 0)
                        text   = (sub.get("questionText") or "").strip()
                        # sub text "----" bo'lsa parent textni ishlatamiz
                        if not text or text == "----":
                            text = parent_text

                        sub_opts = sub.get("options") or []
                        choices  = []
                        answer   = ""

                        # MCQ: isCorrect ni tekshir
                        if qtype == "MCQ":
                            use_opts = sub_opts if sub_opts else options
                            for opt in use_opts:
                                if opt.get("isCorrect"):
                                    answer = opt.get("optionKey", "")
                                    break
                            choices = _extract_options(use_opts)

                        questions.append({
                            "number":         num,
                            "question_type":  qtype,
                            "content":        text,
                            "instruction":    instr,
                            "correct_answer": answer,
                            "choices":        choices,
                        })
                else:
                    num    = qg.get("questionNumber", len(questions) + 1)
                    text   = parent_text
                    answer = ""
                    choices = []

                    if qtype == "MCQ":
                        for opt in options:
                            if opt.get("isCorrect"):
                                answer = opt.get("optionKey", "")
                                break
                        choices = _extract_options(options)

                    questions.append({
                        "number":         num,
                        "question_type":  qtype,
                        "content":        text,
                        "instruction":    instr,
                        "correct_answer": answer,
                        "choices":        choices,
                    })

            if questions:
                test_sections.append({
                    "section_number": sec_num,
                    "title":          sec_data.get("title", f"Section {sec_num}"),
                    "audio_url":      audio_url,
                    "questions":      questions,
                })

        if test_sections:
            all_listenings.append({
                "id":        lid,
                "title":     title,
                "audio_url": audio_url,
                "sections":  test_sections,
            })
            total_q = sum(len(s["questions"]) for s in test_sections)
            print(f"  [{i+1}/{len(meta_list)}] {_safe(title)} ({len(test_sections)} section, {total_q} savol)")
        else:
            print(f"  [{i+1}/{len(meta_list)}] SKIP {_safe(title)}")

        time.sleep(0.3)

    with open(RAW_LISTENING, "w", encoding="utf-8") as f:
        json.dump(all_listenings, f, ensure_ascii=False, indent=2)

    total_q = sum(sum(len(s["questions"]) for s in ls["sections"]) for ls in all_listenings)
    print(f"\nListening saqlandi: {RAW_LISTENING}")
    print(f"  {len(all_listenings)} listening, {total_q} savol\n")
    return all_listenings


# ─────────────────────────────────────────────────────────────────────────────
# DB IMPORT — Reading
# ─────────────────────────────────────────────────────────────────────────────

def import_readings(raw_file=RAW_READING):
    import django
    django.setup()
    from ielts.models import ReadingPassage, ReadingQuestion, ReadingChoice
    from django.db import transaction

    with open(raw_file, encoding="utf-8") as f:
        readings = json.load(f)

    print(f"\n[DB] {len(readings)} reading import qilinmoqda...")
    ok = skip = err = 0

    for reading in readings:
        for part in reading["parts"]:
            title = f"{reading['title']} — Part {part['part']}"
            if ReadingPassage.objects.filter(title=title).exists():
                skip += 1
                continue

            try:
                with transaction.atomic():
                    passage = ReadingPassage.objects.create(
                        title          = title,
                        content        = part["content"],
                        passage_number = part["part"],
                        time_limit     = part.get("time_limit", 20),
                        is_standalone  = True,
                        is_premium     = False,
                    )
                    for q in part["questions"]:
                        rq = ReadingQuestion.objects.create(
                            passage        = passage,
                            number         = q["number"],
                            question_type  = q["question_type"],
                            content        = q.get("content", ""),
                            correct_answer = q.get("correct_answer", ""),
                            explanation    = q.get("explanation", ""),
                        )
                        for c in q.get("choices", []):
                            ReadingChoice.objects.create(
                                question=rq, option=c["option"], text=c["text"]
                            )
                ok += 1
                print(f"  OK: {_safe(title)} ({len(part['questions'])} savol)")
            except Exception as e:
                err += 1
                print(f"  ERR: {_safe(title)}: {_safe(str(e))}")

    print(f"\nReading import: {ok} yangi, {skip} skip, {err} xato\n")


# ─────────────────────────────────────────────────────────────────────────────
# DB IMPORT — Listening
# ─────────────────────────────────────────────────────────────────────────────

def import_listenings(raw_file=RAW_LISTENING):
    import django
    django.setup()
    from ielts.models import ListeningSection, ListeningQuestion, ListeningChoice
    from django.db import transaction

    with open(raw_file, encoding="utf-8") as f:
        listenings = json.load(f)

    print(f"\n[DB] {len(listenings)} listening import qilinmoqda...")
    ok = skip = err = 0

    for lst in listenings:
        for section in lst["sections"]:
            title = f"{lst['title']} — Section {section['section_number']}"
            if ListeningSection.objects.filter(title=title).exists():
                skip += 1
                continue

            try:
                with transaction.atomic():
                    sec = ListeningSection.objects.create(
                        title          = title,
                        section_number = section["section_number"],
                        audio_url      = section.get("audio_url", ""),
                        is_standalone  = True,
                        is_premium     = False,
                    )
                    for q in section["questions"]:
                        lq = ListeningQuestion.objects.create(
                            section        = sec,
                            number         = q["number"],
                            question_type  = q["question_type"],
                            content        = q.get("content", ""),
                            correct_answer = q.get("correct_answer", ""),
                        )
                        for c in q.get("choices", []):
                            ListeningChoice.objects.create(
                                question=lq, option=c["option"], text=c["text"]
                            )
                ok += 1
                print(f"  OK: {_safe(title)} ({len(section['questions'])} savol)")
            except Exception as e:
                err += 1
                print(f"  ERR: {_safe(title)}: {_safe(str(e))}")

    print(f"\nListening import: {ok} yangi, {skip} skip, {err} xato\n")


# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="IELTS Reading + Listening scraper")
    parser.add_argument("--mode", choices=["reading", "listening", "all", "import-reading",
                                           "import-listening", "import-all"],
                        default="all")
    args = parser.parse_args()

    if args.mode in ("reading", "all"):
        scrape_readings()

    if args.mode in ("listening", "all"):
        scrape_listenings()

    if args.mode in ("reading", "all", "import-reading", "import-all"):
        import django; django.setup()
        import_readings()

    if args.mode in ("listening", "all", "import-listening", "import-all"):
        import django
        try:
            django.setup()
        except RuntimeError:
            pass
        import_listenings()
