# -*- coding: utf-8 -*-
"""
realprep.plus saytidan barcha SAT mock testlarini scrape qiladi.
Playwright orqali API interceptsiya ishlatadi.

Ishlatish:
    python scripts/scrape_realprep.py --mode scrape    # Brauzer ochib data yig'
    python scripts/scrape_realprep.py --mode import    # DB ga import qil
    python scripts/scrape_realprep.py --mode all       # Ikkalasini ham qil
"""

import asyncio
import json
import os
import sys
import re
import time
import argparse
import requests
import django
from pathlib import Path
from urllib.parse import urljoin

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

BASE_URL    = "https://realprep.plus"
RAW_FILE    = "scripts/realprep_raw.json"
IMAGES_DIR  = Path("media/questions/realprep")
IMAGES_DIR.mkdir(parents=True, exist_ok=True)

# Exambridge (bizning sayt) API
EB_BASE    = "https://exambridge.uz/api"
EB_EMAIL   = "nodirbek.shukurov09q@gmail.com"
EB_PASS    = "Nodirbek_2000"

DOMAIN_MAP = {
    'craft_and_structure':               'craft_structure',
    'craft_structure':                   'craft_structure',
    'information_and_ideas':             'info_ideas',
    'info_ideas':                        'info_ideas',
    'standard_english_conventions':      'standard_english',
    'standard_english':                  'standard_english',
    'expression_of_ideas':               'expression_ideas',
    'expression_ideas':                  'expression_ideas',
    'problem_solving_and_data_analysis': 'problem_data',
    'problem_data':                      'problem_data',
    'geometry_and_trigonometry':         'geometry',
    'geometry':                          'geometry',
    'algebra':                           'algebra',
    'advanced_math':                     'advanced_math',
}


# ─── SCRAPING ─────────────────────────────────────────────────────────────────

async def scrape():
    from playwright.async_api import async_playwright

    print("=" * 60)
    print("REALPREP.PLUS — SAT Mock Scraper")
    print("=" * 60)

    captured_api   = []
    captured_pages = {}
    seen_urls      = set()

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        context = await browser.new_context(
            viewport={"width": 1400, "height": 900},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
        )
        page = await context.new_page()

        # Barcha JSON API javoblarini ushlash
        async def on_response(response):
            url  = response.url
            ct   = response.headers.get("content-type", "")
            if response.status != 200:
                return
            if "json" not in ct:
                return
            if url in seen_urls:
                return
            seen_urls.add(url)
            try:
                data = await response.json()
                captured_api.append({"url": url, "data": data})
                short = url.replace(BASE_URL, "")
                print(f"  [API] {short[:100]}")
            except Exception:
                pass

        page.on("response", on_response)

        # Saytni ochish
        print(f"\nOchilmoqda: {BASE_URL}")
        await page.goto(BASE_URL, wait_until="networkidle", timeout=30000)
        await page.wait_for_timeout(2000)
        captured_pages["home"] = await page.content()
        print(f"  Sahifa: {await page.title()}")

        # Login sahifasini topish
        login_urls = [
            f"{BASE_URL}/login",
            f"{BASE_URL}/auth/login",
            f"{BASE_URL}/signin",
            f"{BASE_URL}/auth",
        ]
        for lurl in login_urls:
            try:
                await page.goto(lurl, wait_until="networkidle", timeout=8000)
                content = await page.content()
                if "password" in content.lower() or "email" in content.lower():
                    captured_pages["login"] = content
                    print(f"  Login sahifasi topildi: {lurl}")
                    break
            except Exception:
                pass

        print("\n" + "=" * 60)
        print("QOLDA LOGIN REJIMI")
        print("=" * 60)
        print("\nBrauzer ochiq. Iltimos:")
        print("  1. Saytga login qiling (agar kirilmagan bo'lsa)")
        print("  2. Tests/Mocks sahifasiga o'ting")
        print("  3. Har bir testni bosib oching (savollar yuklanadi)")
        print("  4. Testni biroz aylanib chiqing (barcha savollar yuklansin)")
        print("  5. Orqaga qaytib keyingi testni oching")
        print("  6. Barcha testlarni aylanib chiqqach ENTER bosing")
        print("\n  Barcha API chaqiruvlar avtomatik saqlanmoqda...")
        print("=" * 60)
        input("\nTugagach ENTER bosing: ")

        # Oxirgi sahifa holati
        final_url  = page.url
        final_html = await page.content()
        captured_pages["final"] = {"url": final_url, "html": final_html[:100000]}

        await browser.close()

    # Natijani saqlash
    result = {
        "api_responses": captured_api,
        "pages": {k: (v[:10000] if isinstance(v, str) else v) for k, v in captured_pages.items()},
    }
    with open(RAW_FILE, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    print(f"\nSaqlandi: {RAW_FILE}")
    print(f"  API responses: {len(captured_api)}")
    print(f"  Pages: {len(captured_pages)}")
    return result


# ─── PARSE & NORMALIZE ────────────────────────────────────────────────────────

def normalize_question(q, default_section="MATH"):
    """Har xil formatdagi savolni standart formatga o'girish"""
    if not isinstance(q, dict):
        return None

    # question_type
    raw_type = str(q.get("question_type", q.get("type", q.get("qtype", "MCQ")))).upper()
    if any(x in raw_type for x in ["INPUT", "SPR", "GRID", "STUDENT", "FREE", "NUMERIC"]):
        q_type = "INPUT"
    else:
        q_type = "MCQ"

    # difficulty
    raw_diff = str(q.get("difficulty", q.get("level", "MEDIUM"))).upper()
    if "EASY" in raw_diff or raw_diff == "1" or raw_diff == "E":
        difficulty = "EASY"
    elif "HARD" in raw_diff or raw_diff == "3" or raw_diff == "H":
        difficulty = "HARD"
    else:
        difficulty = "MEDIUM"

    # choices
    choices = []
    raw_choices = q.get("choices", q.get("options", q.get("answers", q.get("variants", []))))
    for c in (raw_choices or []):
        if isinstance(c, dict):
            opt = str(c.get("option", c.get("label", c.get("letter", c.get("id", "A"))))).upper()[:1]
            txt = str(c.get("text", c.get("content", c.get("value", c.get("body", "")))))
            choices.append({"option": opt, "text": txt})
        elif isinstance(c, str):
            choices.append({"option": chr(65 + len(choices)), "text": c})

    # correct answer
    correct = str(q.get("correct_answer",
                   q.get("answer",
                   q.get("correct",
                   q.get("right_answer",
                   q.get("solution", "")))))).strip().upper()
    if q_type == "MCQ" and len(correct) > 1:
        correct = correct[0]

    # passage
    passage = q.get("passage",
               q.get("context",
               q.get("stimulus",
               q.get("reading",
               q.get("text_passage", "")))))
    if isinstance(passage, dict):
        passage = passage.get("content", passage.get("text", ""))
    passage = str(passage or "")

    # content
    content = q.get("content",
               q.get("question",
               q.get("stem",
               q.get("question_text",
               q.get("body", "")))))
    if isinstance(content, dict):
        content = content.get("text", content.get("html", ""))
    content = str(content or "")

    # section
    raw_sec = str(q.get("section",
                   q.get("subject",
                   q.get("section_type", default_section)))).upper()
    if any(x in raw_sec for x in ["ENG", "READ", "WRIT", "RW", "VERBAL"]):
        section = "ENGLISH"
    else:
        section = "MATH"

    # module
    module_num = q.get("module", q.get("module_number", q.get("module_num", 1)))
    try:
        module_num = int(module_num)
    except Exception:
        module_num = 1

    # difficulty variant
    diff_var = str(q.get("difficulty_variant", q.get("variant", "STANDARD"))).upper()
    if diff_var not in ("EASY", "MEDIUM", "HARD"):
        diff_var = "STANDARD"

    # category/domain
    raw_domain = q.get("domain",
                  q.get("category",
                  q.get("content_domain",
                  q.get("skill", ""))))
    if isinstance(raw_domain, dict):
        raw_domain = raw_domain.get("name", raw_domain.get("slug", ""))
    raw_domain = re.sub(r'[\s-]+', '_', str(raw_domain or "").lower())
    category = DOMAIN_MAP.get(raw_domain, raw_domain)

    # image
    image = q.get("image", q.get("image_url", q.get("img_url", q.get("img", None))))

    # explanation
    expl = q.get("explanation", q.get("rationale", q.get("solution_text", "")))
    if isinstance(expl, dict):
        expl = expl.get("text", expl.get("content", ""))

    return {
        "number":           q.get("number", q.get("order", q.get("question_number", q.get("position", 1)))),
        "question_type":    q_type,
        "content":          content,
        "passage":          passage,
        "image":            image,
        "correct_answer":   correct,
        "explanation":      str(expl or ""),
        "difficulty":       difficulty,
        "difficulty_variant": diff_var,
        "section":          section,
        "module":           module_num,
        "category":         category,
        "choices":          choices,
    }


def parse_raw(raw_file=RAW_FILE):
    """Raw JSON'dan testlar va savollarni olish"""
    try:
        with open(raw_file, "r", encoding="utf-8") as f:
            raw = json.load(f)
    except FileNotFoundError:
        print(f"Fayl topilmadi: {raw_file}")
        return None

    api_data = raw.get("api_responses", [])
    print(f"\n{len(api_data)} ta API response qayta ishlanmoqda...")

    # Testlar va savollarni saqlash
    tests_found     = []
    # key: "test_id__SECTION__module_num__variant"  value: [questions]
    questions_map   = {}

    def add_questions(test_id, section, module_num, variant, qs):
        key = f"{test_id}__{section}__{module_num}__{variant}"
        if key not in questions_map:
            questions_map[key] = []
        normalized = [normalize_question(q, section) for q in qs]
        normalized = [q for q in normalized if q and q.get("content")]
        questions_map[key].extend(normalized)
        if normalized:
            print(f"  [Q] test={test_id} {section} M{module_num} ({variant}): {len(normalized)} savol")

    for item in api_data:
        url  = item["url"]
        data = item["data"]

        # ── Test ro'yxati ────────────────────────────────────────────────────
        if isinstance(data, list) and len(data) > 0:
            first = data[0] if isinstance(data[0], dict) else {}
            keys  = set(first.keys())

            # Test ro'yxati
            if any(k in keys for k in ["year", "month", "form", "test_type"]):
                for t in data:
                    tests_found.append({
                        "year":           t.get("year", 2024),
                        "month":          t.get("month", 3),
                        "form":           t.get("form", "A"),
                        "test_type":      t.get("test_type", "SAT"),
                        "is_international": t.get("is_international", False),
                        "is_premium":     t.get("is_premium", False),
                        "_id":            str(t.get("id", t.get("_id", ""))),
                        "title":          t.get("title", t.get("name", "")),
                    })
                print(f"  [TESTS] {len(data)} ta test topildi")

            # To'g'ridan-to'g'ri savol ro'yxati
            elif any(k in keys for k in ["question_type", "content", "choices", "correct_answer", "answer", "stem"]):
                test_id = _id_from_url(url)
                section = _section_from_url(url)
                mod     = _module_from_url(url)
                add_questions(test_id, section, mod, "STANDARD", data)

        # ── Dict response ─────────────────────────────────────────────────────
        elif isinstance(data, dict):

            # Test meta-data
            if any(k in data for k in ["year", "month", "form", "test_type", "title", "name"]):
                if any(k in data for k in ["year", "month", "form", "test_type"]):
                    tests_found.append({
                        "year":           data.get("year", 2024),
                        "month":          data.get("month", 3),
                        "form":           data.get("form", "A"),
                        "test_type":      data.get("test_type", "SAT"),
                        "is_international": data.get("is_international", False),
                        "is_premium":     data.get("is_premium", False),
                        "_id":            str(data.get("id", data.get("_id", ""))),
                        "title":          data.get("title", data.get("name", "")),
                    })

            # questions field mavjud
            if "questions" in data:
                test_id = str(data.get("id", data.get("test_id", _id_from_url(url))))
                section = _section_from_dict(data)
                mod     = _module_from_dict(data)
                var     = str(data.get("difficulty_variant", data.get("variant", "STANDARD"))).upper()
                if var not in ("EASY", "MEDIUM", "HARD"):
                    var = "STANDARD"
                add_questions(test_id, section, mod, var, data["questions"])

            # sections field mavjud
            if "sections" in data:
                test_id = str(data.get("id", data.get("_id", _id_from_url(url))))
                for sec in (data["sections"] or []):
                    sec_type = _section_type_str(sec)
                    for mod in (sec.get("modules", [sec])):
                        mod_num = int(mod.get("module_number", mod.get("number", mod.get("module", 1))))
                        var     = str(mod.get("difficulty_variant", mod.get("variant", "STANDARD"))).upper()
                        if var not in ("EASY", "MEDIUM", "HARD"):
                            var = "STANDARD"
                        qs = mod.get("questions", [])
                        add_questions(test_id, sec_type, mod_num, var, qs)

            # modules field mavjud (section wrapper yo'q)
            if "modules" in data and "sections" not in data:
                test_id = str(data.get("id", data.get("_id", _id_from_url(url))))
                for mod in (data["modules"] or []):
                    sec_type = _section_type_str(mod)
                    mod_num  = int(mod.get("module_number", mod.get("number", 1)))
                    var      = str(mod.get("difficulty_variant", mod.get("variant", "STANDARD"))).upper()
                    if var not in ("EASY", "MEDIUM", "HARD"):
                        var = "STANDARD"
                    qs = mod.get("questions", [])
                    add_questions(test_id, sec_type, mod_num, var, qs)

    # Takrorlangan testlarni olib tashlash
    seen_ids = set()
    unique_tests = []
    for t in tests_found:
        tid = t["_id"]
        if tid and tid not in seen_ids:
            seen_ids.add(tid)
            unique_tests.append(t)

    total_q = sum(len(v) for v in questions_map.values())
    print(f"\nJami: {len(unique_tests)} test, {total_q} savol topildi")

    result = {"tests": unique_tests, "questions_map": questions_map}
    parsed_file = RAW_FILE.replace("_raw.json", "_parsed.json")
    with open(parsed_file, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print(f"Saqlandi: {parsed_file}")
    return result


def _id_from_url(url):
    parts = url.rstrip("/").split("/")
    for p in reversed(parts):
        p2 = p.split("?")[0]
        if p2.isdigit():
            return p2
    return "unknown"

def _module_from_url(url):
    m = re.search(r'module[_\-/]?(\d)', url, re.I)
    return int(m.group(1)) if m else 1

def _section_from_url(url):
    ul = url.lower()
    if any(x in ul for x in ["math", "math"]):
        return "MATH"
    if any(x in ul for x in ["english", "reading", "writing", "rw", "verbal"]):
        return "ENGLISH"
    return "MATH"

def _section_from_dict(d):
    raw = str(d.get("section", d.get("section_type", d.get("subject", "")))).upper()
    return _section_type_str({"section_type": raw})

def _module_from_dict(d):
    v = d.get("module_number", d.get("module", d.get("number", 1)))
    try:
        return int(v)
    except Exception:
        return 1

def _section_type_str(obj):
    raw = str(obj.get("section_type", obj.get("type", obj.get("section", obj.get("subject", ""))))).upper()
    if any(x in raw for x in ["ENG", "READ", "WRIT", "RW", "VERBAL"]):
        return "ENGLISH"
    return "MATH"


# ─── IMPORT ───────────────────────────────────────────────────────────────────

def import_to_db(parsed_file=None):
    if parsed_file is None:
        parsed_file = RAW_FILE.replace("_raw.json", "_parsed.json")

    try:
        with open(parsed_file, "r", encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        print(f"Fayl topilmadi: {parsed_file}. Avval --mode scrape yoki parse qiling.")
        return

    from tests_app.models import Test, TestSection, Module, Question, Choice
    from django.db import transaction

    tests_data    = data.get("tests", [])
    questions_map = data.get("questions_map", {})

    print(f"\n{len(tests_data)} ta test import qilinmoqda...")

    # ── Testlarni yaratish ────────────────────────────────────────────────────
    created_tests = {}

    # Testlar explicit ro'yxatda bo'lsa
    for t in tests_data:
        try:
            test, created = Test.objects.get_or_create(
                test_type=t.get("test_type", "SAT"),
                year=t.get("year", 2024),
                month=t.get("month", 3),
                form=t.get("form", "A"),
                is_international=t.get("is_international", False),
                defaults={"is_premium": t.get("is_premium", False)}
            )
            tid = t.get("_id", str(test.id))
            created_tests[tid] = test
            status = "YARATILDI" if created else "MAVJUD"
            print(f"  [{status}] {test}")
        except Exception as e:
            print(f"  [XATO] {e}")

    # ── Savollarni import qilish ─────────────────────────────────────────────
    total_q = 0
    print(f"\nSavollar import qilinmoqda...")

    for key, questions in questions_map.items():
        if not questions:
            continue

        # Key: "test_id__SECTION__module_num__variant"
        parts       = key.split("__")
        test_id_str = parts[0] if len(parts) > 0 else "unknown"
        section_raw = parts[1].upper() if len(parts) > 1 else "MATH"
        section_type = "ENGLISH" if any(x in section_raw for x in ["ENG", "READ", "WRIT"]) else "MATH"
        try:
            module_num  = int(parts[2]) if len(parts) > 2 else 1
        except Exception:
            module_num = 1
        variant     = parts[3].upper() if len(parts) > 3 else "STANDARD"
        if variant not in ("EASY", "MEDIUM", "HARD"):
            variant = "STANDARD"

        # Test topish
        test = created_tests.get(test_id_str)
        if not test:
            # questions içindeki meta-datadan topish
            q0 = questions[0] if questions else {}
            # Fallback: yil/oy
            year  = int(q0.get("year",  2024))
            month = int(q0.get("month", 3))
            test, _ = Test.objects.get_or_create(
                test_type="SAT", year=year, month=month, form="A", is_international=False
            )

        try:
            with transaction.atomic():
                section, _ = TestSection.objects.get_or_create(
                    test=test, section_type=section_type
                )
                time_limit = 35 if section_type == "MATH" else 32

                # Module 2 uchun difficulty_variant
                diff_variant = "STANDARD"
                if module_num == 2:
                    if variant in ("EASY", "MEDIUM", "HARD"):
                        diff_variant = variant

                module, _ = Module.objects.get_or_create(
                    section=section,
                    module_number=module_num,
                    difficulty_variant=diff_variant,
                    defaults={"time_limit": time_limit}
                )

                for q_data in questions:
                    if not q_data or not q_data.get("content"):
                        continue
                    try:
                        q, q_created = Question.objects.update_or_create(
                            module=module,
                            number=int(q_data.get("number", 1)),
                            defaults={
                                "question_type":  q_data.get("question_type", "MCQ"),
                                "content":        q_data.get("content", ""),
                                "passage":        q_data.get("passage", ""),
                                "correct_answer": q_data.get("correct_answer", ""),
                                "explanation":    q_data.get("explanation", ""),
                                "difficulty":     q_data.get("difficulty", "MEDIUM"),
                                "category":       q_data.get("category", ""),
                            }
                        )
                        # Rasm yuklab olish
                        img_url = q_data.get("image")
                        if img_url and q_created:
                            _download_image(q, img_url)

                        # Choices
                        for c in q_data.get("choices", []):
                            if not c.get("option") or not c.get("text"):
                                continue
                            Choice.objects.update_or_create(
                                question=q,
                                option=c["option"],
                                defaults={"text": c["text"]}
                            )
                        total_q += 1
                    except Exception as e:
                        print(f"    [savol xato] {e}")

                print(f"  {test} | {section_type} M{module_num} ({diff_variant}): {len(questions)} savol")

        except Exception as e:
            print(f"  [XATO] {key}: {e}")

    print(f"\nImport tugadi! Jami: {total_q} savol")


def _download_image(question_obj, img_url):
    try:
        if not img_url.startswith("http"):
            img_url = urljoin(BASE_URL, img_url)
        resp = requests.get(img_url, timeout=10)
        if resp.status_code == 200:
            ext = img_url.split(".")[-1].split("?")[0][:5] or "png"
            fname = f"realprep_q{question_obj.id}.{ext}"
            fpath = IMAGES_DIR / fname
            with open(fpath, "wb") as f:
                f.write(resp.content)
            question_obj.image = f"questions/realprep/{fname}"
            question_obj.save(update_fields=["image"])
    except Exception:
        pass


# ─── EXAMBRIDGE ORQALI IMPORT (OPTIONAL) ──────────────────────────────────────

def import_via_exambridge(parsed_file=None):
    """Exambridge.uz API orqali import (muqobil yo'l)"""
    import requests as req
    if parsed_file is None:
        parsed_file = RAW_FILE.replace("_raw.json", "_parsed.json")

    with open(parsed_file, "r", encoding="utf-8") as f:
        data = json.load(f)

    eb = req.Session()
    eb.verify = False
    lr = eb.post(f"{EB_BASE}/auth/login/", json={"email": EB_EMAIL, "password": EB_PASS})
    eb.headers['Authorization'] = f"Bearer {lr.json()['access']}"
    print("Exambridge ga ulandi.")

    tests_data    = data.get("tests", [])
    questions_map = data.get("questions_map", {})

    for t in tests_data:
        tid = t.get("_id", "")
        # Bu test uchun modullarni topish
        e1 = questions_map.get(f"{tid}__ENGLISH__1__STANDARD", [])
        e2 = questions_map.get(f"{tid}__ENGLISH__2__STANDARD", [])
        e2h = questions_map.get(f"{tid}__ENGLISH__2__HARD", e2)
        e2e = questions_map.get(f"{tid}__ENGLISH__2__EASY", e2)
        m1 = questions_map.get(f"{tid}__MATH__1__STANDARD", [])
        m2 = questions_map.get(f"{tid}__MATH__2__STANDARD", [])
        m2h = questions_map.get(f"{tid}__MATH__2__HARD", m2)
        m2e = questions_map.get(f"{tid}__MATH__2__EASY", m2)

        if not (e1 or m1):
            continue

        payload = {
            "test_mode":        "FULL",
            "year":             t.get("year", 2024),
            "month":            t.get("month", 3),
            "form":             t.get("form", "A"),
            "is_international": t.get("is_international", False),
            "is_premium":       t.get("is_premium", False),
            "english_m1":       e1,
            "english_m2_easy":  e2e or e2,
            "english_m2_medium": e2,
            "english_m2_hard":  e2h or e2,
            "math_m1":          m1,
            "math_m2_easy":     m2e or m2,
            "math_m2_medium":   m2,
            "math_m2_hard":     m2h or m2,
        }
        r = eb.post(f"{EB_BASE}/import/sat/mock/", json=payload)
        status = "OK" if r.ok else "FAIL"
        print(f"  [{status}] {t.get('title', tid)}: {r.status_code}")


# ─── MAIN ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="realprep.plus SAT scraper")
    parser.add_argument("--mode", choices=["scrape", "parse", "import", "all"], default="scrape",
                        help="scrape=brauzer ochib yig', parse=raw dan savollarni ajrat, import=DB ga yoz, all=hammasi")
    parser.add_argument("--file", default=None, help="Raw yoki parsed JSON fayl yo'li")
    args = parser.parse_args()

    if args.mode == "scrape":
        asyncio.run(scrape())
        parse_raw(args.file or RAW_FILE)

    elif args.mode == "parse":
        parse_raw(args.file or RAW_FILE)

    elif args.mode == "import":
        import_to_db(args.file)

    elif args.mode == "all":
        asyncio.run(scrape())
        parse_raw(RAW_FILE)
        import_to_db()
