"""
bluebookplus.plus saytidan SAT test datalarini scrape qiladi.
Rasmlar bilan birga to'liq savollarni oladi.

Ishlatish:
    python scripts/scrape_bluebook.py
"""

import asyncio
import json
import os
import sys
import re
import django
import requests
from pathlib import Path
from urllib.parse import urljoin, urlparse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

BASE_URL = "https://bluebookplus.plus"
OUTPUT_FILE = "scripts/bluebook_raw.json"
IMAGES_DIR = Path("media/questions/bluebook")
IMAGES_DIR.mkdir(parents=True, exist_ok=True)


async def scrape():
    from playwright.async_api import async_playwright

    print("=" * 60)
    print("📘 BLUEBOOK PLUS — SAT Scraper")
    print("=" * 60)

    captured_api = []
    captured_pages = {}

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        context = await browser.new_context(
            viewport={"width": 1280, "height": 900},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        )
        page = await context.new_page()

        # API interceptor
        async def on_response(response):
            url = response.url
            status = response.status
            ct = response.headers.get("content-type", "")
            if status == 200 and "json" in ct:
                try:
                    data = await response.json()
                    captured_api.append({"url": url, "data": data})
                    print(f"  📦 API: {url[:80]}")
                except Exception:
                    pass

        page.on("response", on_response)

        # Bosh sahifa
        print(f"\n🌐 {BASE_URL} ochilmoqda...")
        await page.goto(BASE_URL, wait_until="networkidle", timeout=30000)
        await page.wait_for_timeout(2000)

        title = await page.title()
        print(f"   Sahifa: {title}")
        captured_pages["home"] = await page.content()

        # Barcha linklar
        links = await page.eval_on_selector_all("a[href]", "els => els.map(e => ({href: e.href, text: e.textContent.trim()}))")
        test_links = [l for l in links if any(k in l["href"].lower() for k in ["test", "sat", "exam", "practice", "question"])]
        print(f"\n🔗 Test linklarni topildi: {len(test_links)}")
        for l in test_links[:15]:
            print(f"   {l['text'][:30]:30} → {l['href'][:60]}")

        # Tests sahifasiga o'tish
        tests_pages = [
            f"{BASE_URL}/tests",
            f"{BASE_URL}/sat",
            f"{BASE_URL}/practice",
            f"{BASE_URL}/exams",
        ]
        for url in tests_pages:
            try:
                await page.goto(url, wait_until="networkidle", timeout=10000)
                await page.wait_for_timeout(1500)
                ct = await page.content()
                if len(ct) > 1000:
                    captured_pages[url] = ct
                    print(f"   ✅ {url}")
            except Exception:
                pass

        print("\n" + "=" * 60)
        print("🖱️  QOLDA SCRAPING REJIMI")
        print("=" * 60)
        print("\nBrauzer ochiq. Iltimos:")
        print("  1. Saytda testlarni oching")
        print("  2. SAT testlarini birma-bir bosib ko'ring")
        print("  3. Savollar, rasmlar, javoblarni ko'ring")
        print("  4. Mümkin qadar ko'p testni oching")
        print("\n  ⚡ Barcha API chaqiruvlar avtomatik saqlanmoqda")
        print("\nTugagach ENTER bosing...")
        input()

        # Sahifa HTML ni ham saqlab ol
        final_html = await page.content()
        final_url = page.url
        captured_pages["final"] = {"url": final_url, "html": final_html[:50000]}

        await browser.close()

    # Natijalarni saqlash
    result = {
        "api_responses": captured_api,
        "pages": {k: v[:5000] if isinstance(v, str) else v for k, v in captured_pages.items()},
    }

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    print(f"\n✅ Saqlandi: {OUTPUT_FILE}")
    print(f"   API responses: {len(captured_api)}")
    print(f"   Pages: {len(captured_pages)}")

    return result


def convert_bluebook_to_sat_format(raw_file=OUTPUT_FILE):
    """Bluebook datani SAT JSON formatga o'girish"""
    try:
        with open(raw_file, "r", encoding="utf-8") as f:
            raw = json.load(f)
    except FileNotFoundError:
        print(f"❌ {raw_file} topilmadi. Avval scrape qiling.")
        return

    api_data = raw.get("api_responses", [])
    print(f"📂 {len(api_data)} API response qayta ishlanmoqda...")

    tests = []
    questions_by_test = {}

    for item in api_data:
        url = item["url"]
        data = item["data"]

        # Test ro'yxati
        if isinstance(data, list) and len(data) > 0:
            first = data[0]
            if isinstance(first, dict):
                keys = set(first.keys())
                # SAT test formatini aniqlash
                if any(k in keys for k in ["year", "month", "form", "test_type"]):
                    for t in data:
                        tests.append({
                            "year": t.get("year", 2024),
                            "month": t.get("month", 3),
                            "form": t.get("form", "A"),
                            "test_type": t.get("test_type", "SAT"),
                            "is_international": t.get("is_international", False),
                            "is_premium": t.get("is_premium", False),
                            "_id": t.get("id", t.get("_id")),
                        })
                    print(f"  🗂  Tests: {len(data)} ta ({url[:60]})")

                # Savollar formatini aniqlash
                elif any(k in keys for k in ["question_type", "content", "choices", "correct_answer", "answer"]):
                    test_id = _extract_test_id(url)
                    if test_id not in questions_by_test:
                        questions_by_test[test_id] = []
                    for q in data:
                        questions_by_test[test_id].append(_normalize_sat_question(q))
                    print(f"  ❓ Questions [{test_id}]: {len(data)} ta ({url[:60]})")

        elif isinstance(data, dict):
            # Bitta test yoki bitta question bo'lishi mumkin
            if "questions" in data:
                test_id = data.get("id", data.get("_id", _extract_test_id(url)))
                questions = [_normalize_sat_question(q) for q in data["questions"]]
                questions_by_test[str(test_id)] = questions
                print(f"  📝 Test [{test_id}]: {len(questions)} savol ({url[:60]})")

            if "sections" in data:
                test_id = data.get("id", _extract_test_id(url))
                for section in data["sections"]:
                    sec_name = section.get("type", section.get("section_type", "MATH"))
                    for module in section.get("modules", [section]):
                        mod_num = module.get("module_number", module.get("number", 1))
                        key = f"{test_id}_{sec_name}_{mod_num}"
                        questions_by_test[key] = [
                            _normalize_sat_question(q) for q in module.get("questions", [])
                        ]

    # Agar hech narsa topilmasa — pages dan HTML parse qilamiz
    if not questions_by_test:
        print("\n⚠️  API dan data topilmadi, HTML dan parse qilinmoqda...")
        _parse_html_fallback(raw.get("pages", {}), questions_by_test, tests)

    # Natijani saqlash
    output = {
        "tests": tests,
        "questions_by_test": questions_by_test,
    }

    out_file = "scripts/bluebook_converted.json"
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    total_q = sum(len(v) for v in questions_by_test.values())
    print(f"\n✅ O'girildi: {len(tests)} test, {total_q} savol")
    print(f"   Saqlandi: {out_file}")
    return output


def _extract_test_id(url):
    """URL dan test ID ni olish"""
    parts = url.rstrip("/").split("/")
    for p in reversed(parts):
        if p.isdigit():
            return p
    return "unknown"


def _normalize_sat_question(q):
    """Har xil formatdagi SAT savolni standart formatga o'girish"""
    if not isinstance(q, dict):
        return {}

    # question_type normalize
    raw_type = str(q.get("question_type", q.get("type", "MCQ"))).upper()
    if "INPUT" in raw_type or "SPR" in raw_type or "GRID" in raw_type or "STUDENT" in raw_type:
        q_type = "INPUT"
    else:
        q_type = "MCQ"

    # difficulty normalize
    raw_diff = str(q.get("difficulty", q.get("level", "MEDIUM"))).upper()
    if "EASY" in raw_diff or "1" == raw_diff:
        difficulty = "EASY"
    elif "HARD" in raw_diff or "3" == raw_diff:
        difficulty = "HARD"
    else:
        difficulty = "MEDIUM"

    # choices normalize
    choices = []
    raw_choices = q.get("choices", q.get("options", q.get("answers", [])))
    for c in raw_choices:
        if isinstance(c, dict):
            choices.append({
                "option": str(c.get("option", c.get("label", c.get("letter", "A")))).upper()[:1],
                "text": str(c.get("text", c.get("content", c.get("value", "")))),
            })
        elif isinstance(c, str):
            choices.append({"option": chr(65 + len(choices)), "text": c})

    # correct answer
    correct = str(q.get("correct_answer", q.get("answer", q.get("correct", "")))).strip().upper()
    if len(correct) > 1 and q_type == "MCQ":
        correct = correct[0]  # Faqat birinchi harf (A, B, C, D)

    # image
    image = q.get("image", q.get("image_url", q.get("img", None)))

    return {
        "number": q.get("number", q.get("order", q.get("question_number", 1))),
        "question_type": q_type,
        "content": q.get("content", q.get("text", q.get("question", q.get("stem", "")))),
        "passage": q.get("passage", q.get("context", q.get("stimulus", ""))),
        "image": image,
        "correct_answer": correct,
        "explanation": q.get("explanation", q.get("rationale", "")),
        "difficulty": difficulty,
        "section": str(q.get("section", q.get("subject", "MATH"))).upper(),
        "module": q.get("module", q.get("module_number", 1)),
        "choices": choices,
    }


def _parse_html_fallback(pages, questions_by_test, tests):
    """HTML dan savollarni parse qilishga urinish"""
    from bs4 import BeautifulSoup
    import re

    for page_key, content in pages.items():
        if isinstance(content, dict):
            content = content.get("html", "")
        if not content:
            continue

        # JSON data ni HTMLdan olish
        json_matches = re.findall(r'(?:window\.__.*?=|var \w+=)(\{.*?\});', content, re.DOTALL)
        for match in json_matches:
            try:
                data = json.loads(match)
                if "questions" in data:
                    questions_by_test[f"html_{page_key}"] = [
                        _normalize_sat_question(q) for q in data["questions"]
                    ]
            except Exception:
                pass


def import_to_database(converted_file="scripts/bluebook_converted.json"):
    """O'girilgan SAT datani database ga import qilish"""
    try:
        with open(converted_file, "r", encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        print(f"❌ {converted_file} topilmadi.")
        return

    from tests_app.models import Test, TestSection, Module, Question, Choice
    from django.db import transaction

    tests_data = data.get("tests", [])
    questions_data = data.get("questions_by_test", {})

    created_tests = {}
    print(f"\n📥 {len(tests_data)} test import qilinmoqda...")

    for t_data in tests_data:
        try:
            test, created = Test.objects.get_or_create(
                test_type=t_data.get("test_type", "SAT"),
                year=t_data.get("year", 2024),
                month=t_data.get("month", 3),
                form=t_data.get("form", "A"),
                is_international=t_data.get("is_international", False),
                defaults={"is_premium": t_data.get("is_premium", False)}
            )
            created_tests[str(t_data.get("_id", test.id))] = test
            print(f"  {'✅ Yaratildi' if created else '⏭  Mavjud'}: {test}")
        except Exception as e:
            print(f"  ❌ Test error: {e}")

    total_q = 0
    print(f"\n📥 Savollar import qilinmoqda...")

    for key, questions in questions_data.items():
        # Key dan test va section/module aniqlash
        parts = key.split("_")
        test_id_str = parts[0]
        section_type = "MATH"
        module_num = 1

        if len(parts) >= 3:
            section_raw = parts[1].upper()
            section_type = "ENGLISH" if any(x in section_raw for x in ["ENG", "READ", "WRIT"]) else "MATH"
            try:
                module_num = int(parts[2])
            except Exception:
                pass

        test = created_tests.get(test_id_str)
        if not test and created_tests:
            test = list(created_tests.values())[0]
        if not test:
            # Default test yaratish
            test, _ = Test.objects.get_or_create(
                test_type="SAT", year=2024, month=3, form="A", is_international=False
            )

        try:
            with transaction.atomic():
                section, _ = TestSection.objects.get_or_create(
                    test=test, section_type=section_type
                )
                time_limit = 35 if section_type == "MATH" else 32
                module, _ = Module.objects.get_or_create(
                    section=section,
                    module_number=module_num,
                    defaults={"time_limit": time_limit}
                )

                for q_data in questions:
                    if not q_data.get("content"):
                        continue

                    q, q_created = Question.objects.update_or_create(
                        module=module,
                        number=q_data.get("number", 1),
                        defaults={
                            "question_type": q_data.get("question_type", "MCQ"),
                            "content": q_data.get("content", ""),
                            "passage": q_data.get("passage", ""),
                            "correct_answer": q_data.get("correct_answer", ""),
                            "explanation": q_data.get("explanation", ""),
                            "difficulty": q_data.get("difficulty", "MEDIUM"),
                        }
                    )

                    # Rasmni saqlash
                    img_url = q_data.get("image")
                    if img_url and q_created:
                        _download_image(q, img_url)

                    # Choices
                    for c_data in q_data.get("choices", []):
                        Choice.objects.update_or_create(
                            question=q,
                            option=c_data["option"],
                            defaults={"text": c_data["text"]}
                        )
                    total_q += 1

                print(f"  ✅ {test} | {section_type} M{module_num}: {len(questions)} savol")

        except Exception as e:
            print(f"  ❌ {key}: {e}")

    print(f"\n🎉 Import tugadi! Jami: {total_q} savol")


def _download_image(question_obj, img_url):
    """Rasmni yuklab olish va modelga biriktirish"""
    try:
        if not img_url.startswith("http"):
            img_url = urljoin(BASE_URL, img_url)

        resp = requests.get(img_url, timeout=10)
        if resp.status_code == 200:
            ext = img_url.split(".")[-1].split("?")[0][:5] or "png"
            filename = f"q_{question_obj.id}.{ext}"
            filepath = IMAGES_DIR / filename
            with open(filepath, "wb") as f:
                f.write(resp.content)
            question_obj.image = f"questions/bluebook/{filename}"
            question_obj.save(update_fields=["image"])
    except Exception as e:
        pass  # Rasm yuklanmasa ham davom etadi


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["scrape", "convert", "import", "all"], default="scrape")
    args = parser.parse_args()

    if args.mode == "scrape":
        asyncio.run(scrape())
    elif args.mode == "convert":
        convert_bluebook_to_sat_format()
    elif args.mode == "import":
        import_to_database()
    elif args.mode == "all":
        asyncio.run(scrape())
        convert_bluebook_to_sat_format()
        import_to_database()
