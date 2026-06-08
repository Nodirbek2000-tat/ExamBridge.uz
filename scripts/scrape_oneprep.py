"""
OnePrep.xyz SAT Question Scraper
Playwright bilan login + savollarni olish + exambridge.uz ga import
"""
import asyncio
import json
import re
import time
import requests
from pathlib import Path
from playwright.async_api import async_playwright

# ── CONFIG ─────────────────────────────────────────────────────────────────────
ONEPREP_EMAIL    = "nodirbek.shukurov09q@gmail.com"
ONEPREP_PASSWORD = "Nodirbek_2000"
ONEPREP_BASE     = "https://www.oneprep.xyz"

EXAMBRIDGE_BASE  = "https://exambridge.uz"
EXAMBRIDGE_EMAIL = "admin@sat.com"
EXAMBRIDGE_PASS  = "Admin1234!"

OUTPUT_DIR = Path(__file__).parent.parent / "data" / "oneprep"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# oneprep skill → exambridge category
MATH_CATEGORY_MAP = {
    "H.A": "algebra",
    "H.B": "advanced_math",
    "H.C": "problem_data",
    "H.D": "geometry",
    "H.E": "advanced_math",
}
ENGLISH_CATEGORY_MAP = {
    "INF": "info_ideas",
    "CAS": "craft_structure",
    "EOI": "expression_ideas",
    "SEC": "standard_english",
    "WIC": "craft_structure",
    "CTE": "info_ideas",
    "TSP": "craft_structure",
    "SYN": "expression_ideas",
    "RWE": "standard_english",
}

DIFFICULTY_MAP = {
    "Easy": "easy",
    "Medium": "medium",
    "Hard": "hard",
    "easy": "easy",
    "medium": "medium",
    "hard": "hard",
}


# ── EXAMBRIDGE LOGIN ───────────────────────────────────────────────────────────
def get_exambridge_token():
    r = requests.post(f"{EXAMBRIDGE_BASE}/api/auth/login/", json={
        "email": EXAMBRIDGE_EMAIL,
        "password": EXAMBRIDGE_PASS,
    }, timeout=20)
    if r.status_code == 200:
        token = r.json().get("access") or r.json().get("token", "")
        print(f"  ExamBridge login: OK (token: {token[:20]}...)")
        return token
    print(f"  ExamBridge login FAILED: {r.status_code} {r.text[:200]}")
    return None


def import_to_exambridge(token, subject, category, topic, questions):
    """Import questions to exambridge.uz via bank import API."""
    if not questions:
        return 0
    payload = {
        "subject": subject,
        "category": category,
        "topics": [{"topic": topic, "questions": questions}],
    }
    r = requests.post(
        f"{EXAMBRIDGE_BASE}/api/import/sat/practice/",
        json=payload,
        headers={"Authorization": f"Bearer {token}"},
        timeout=30,
    )
    if r.status_code == 200:
        created = r.json().get("created", 0)
        print(f"    ✓ Import: {created} savol qo'shildi ({subject} / {category} / {topic})")
        return created
    print(f"    ✗ Import xato: {r.status_code} {r.text[:200]}")
    return 0


# ── ONEPREP DOM PARSER ─────────────────────────────────────────────────────────
def parse_question_page(html: str, question_id: str) -> dict | None:
    """Parse question data from oneprep question page HTML."""
    # Try to find question JSON in __NEXT_DATA__
    nd = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.DOTALL)
    if nd:
        try:
            next_data = json.loads(nd.group(1))
            # Walk down to find question data
            props = next_data.get("props", {})
            page_props = props.get("pageProps", {})
            q_data = page_props.get("question") or page_props.get("questionData")
            if q_data:
                return extract_from_json(q_data)
        except Exception:
            pass

    # Fallback: regex parse from HTML
    return extract_from_html(html, question_id)


def extract_from_json(q: dict) -> dict | None:
    """Extract question fields from parsed JSON object."""
    content = q.get("stem") or q.get("question") or q.get("body") or q.get("text") or ""
    if not content:
        return None

    choices_raw = q.get("choices") or q.get("options") or q.get("answers") or []
    choices = []
    for i, c in enumerate(choices_raw[:4]):
        if isinstance(c, dict):
            letter = c.get("id") or c.get("key") or c.get("letter") or chr(65 + i)
            text = c.get("text") or c.get("content") or c.get("body") or str(c)
        else:
            letter = chr(65 + i)
            text = str(c)
        choices.append({"option": letter.upper(), "text": clean_text(text)})

    correct_raw = (
        q.get("correct_answer") or q.get("correctAnswer") or
        q.get("answer") or q.get("correct") or ""
    )
    correct = str(correct_raw).strip().upper()[:1] if correct_raw else ""

    explanation = q.get("explanation") or q.get("rationale") or q.get("solution") or ""
    difficulty_raw = q.get("difficulty") or q.get("level") or "medium"
    difficulty = DIFFICULTY_MAP.get(str(difficulty_raw).strip(), "medium")

    skill = q.get("skill") or q.get("primary_class") or ""
    return {
        "content": clean_text(content),
        "choice_a": next((c["text"] for c in choices if c["option"] == "A"), ""),
        "choice_b": next((c["text"] for c in choices if c["option"] == "B"), ""),
        "choice_c": next((c["text"] for c in choices if c["option"] == "C"), ""),
        "choice_d": next((c["text"] for c in choices if c["option"] == "D"), ""),
        "correct_answer": correct,
        "difficulty": difficulty,
        "explanation": clean_text(str(explanation)) if explanation else "",
        "question_type": "INPUT" if not choices else "MCQ",
        "_skill": skill,
    }


def extract_from_html(html: str, question_id: str) -> dict | None:
    """Fallback: extract question fields from raw HTML."""
    # Remove scripts/styles
    cleaned = re.sub(r'<script[^>]*>.*?</script>', ' ', html, flags=re.DOTALL)
    cleaned = re.sub(r'<style[^>]*>.*?</style>', ' ', cleaned, flags=re.DOTALL)
    text = re.sub(r'<[^>]+>', ' ', cleaned)
    text = re.sub(r'\s+', ' ', text).strip()

    if len(text) < 50:
        return None

    return {
        "content": text[:1000],
        "choice_a": "", "choice_b": "", "choice_c": "", "choice_d": "",
        "correct_answer": "",
        "difficulty": "medium",
        "explanation": "",
        "question_type": "MCQ",
        "_skill": "",
    }


def clean_text(t: str) -> str:
    if not t:
        return ""
    t = re.sub(r'<[^>]+>', ' ', str(t))
    t = re.sub(r'\s+', ' ', t)
    return t.strip()


# ── MAIN SCRAPER ───────────────────────────────────────────────────────────────
async def scrape_oneprep():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False, slow_mo=100)
        context = await browser.new_context(
            viewport={"width": 1280, "height": 800},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        )
        page = await context.new_page()

        # ── Step 1: Login ──────────────────────────────────────────────────
        print("\n[1] OnePrep ga login qilinmoqda...")
        await page.goto(f"{ONEPREP_BASE}/sign-in", wait_until="domcontentloaded", timeout=60000)
        await asyncio.sleep(2)

        try:
            await page.fill('input[type="email"]', ONEPREP_EMAIL, timeout=5000)
            await page.fill('input[type="password"]', ONEPREP_PASSWORD, timeout=5000)
            await page.click('button[type="submit"]', timeout=5000)
            await asyncio.sleep(3)
            print("  Login: avtomatik yuborildi")
        except Exception as e:
            print(f"  Avtomatik login ishlamadi: {e}")
            print("  Iltimos, brauzerda qo'lda login qiling va Enter bosing...")
            input("  Login qildingizmi? Enter bosing: ")

        # Verify login — wait until user is actually logged in
        print("  Login tekshirilmoqda...")
        await page.goto(f"{ONEPREP_BASE}/question-bank?module=math", wait_until="domcontentloaded", timeout=60000)
        await asyncio.sleep(4)
        current_url = page.url
        print(f"  Hozirgi URL: {current_url}")

        # If redirected to login, ask user to login manually
        if "sign-in" in current_url or "login" in current_url or "authorize" in current_url:
            print("\n  *** Brauzerda oneprep.xyz ga LOGIN QILING ***")
            print("  Login qilib bo'lgach bu terminalga qaytib Enter bosing...")
            input("  Enter bosing: ")
            await asyncio.sleep(2)

        # Double check
        await page.goto(f"{ONEPREP_BASE}/question-bank?module=math", wait_until="domcontentloaded", timeout=60000)
        await asyncio.sleep(5)
        print(f"  Login URL: {page.url}")

        # ── Step 2: Collect all question IDs ──────────────────────────────
        print("\n[2] Savol IDlari yig'ilmoqda...")
        all_ids = {"math": {}, "english": {}}  # {module: {id: skill}}

        for module, url_param, cat_map in [
            ("math", "module=math", MATH_CATEGORY_MAP),
            ("english", "module=en", ENGLISH_CATEGORY_MAP),
        ]:
            print(f"\n  {module.upper()} savollar...")
            # Go through each skill/category
            skills = list(cat_map.keys())

            for skill in skills:
                skill_url = f"{ONEPREP_BASE}/question-bank?question_set=question-bank&{url_param}&skill={skill}"
                print(f"    Skill: {skill} ({skill_url})")
                await page.goto(skill_url, wait_until="domcontentloaded", timeout=60000)
                await asyncio.sleep(5)

                # Scroll to load all content
                for _ in range(8):
                    await page.evaluate("window.scrollBy(0, 500)")
                    await asyncio.sleep(0.8)

                # Collect IDs
                ids = await page.evaluate("""() => {
                    const links = [...document.querySelectorAll('a[href*="/questions/"]')];
                    return [...new Set(links.map(a => {
                        const m = a.href.match(/questions\\/([0-9]+)/);
                        return m ? m[1] : null;
                    }).filter(Boolean))];
                }""")

                # Filter out fake test IDs
                real_ids = [i for i in ids if not i.startswith("987654321")]
                print(f"    Topildi: {len(real_ids)} ID")
                for qid in real_ids:
                    all_ids[module][qid] = skill

                # Try pagination — click "Next" or load more
                for page_num in range(2, 20):
                    next_btn = await page.query_selector('[data-question-nav="next"], a[href*="page="], button:has-text("Next")')
                    if not next_btn:
                        break
                    await next_btn.click()
                    await asyncio.sleep(1.5)
                    new_ids = await page.evaluate("""() => {
                        const links = [...document.querySelectorAll('a[href*="/questions/"]')];
                        return [...new Set(links.map(a => {
                            const m = a.href.match(/questions\\/([0-9]+)/);
                            return m ? m[1] : null;
                        }).filter(Boolean))];
                    }""")
                    new_real = [i for i in new_ids if not i.startswith("987654321")]
                    added = [i for i in new_real if i not in all_ids[module]]
                    if not added:
                        break
                    for qid in added:
                        all_ids[module][qid] = skill
                    print(f"      Sahifa {page_num}: +{len(added)} ID")

        total_ids = sum(len(v) for v in all_ids.values())
        print(f"\n  Jami topilgan IDlar: {total_ids}")

        # Save IDs for recovery
        with open(OUTPUT_DIR / "question_ids.json", "w") as f:
            json.dump(all_ids, f, indent=2)
        print(f"  IDlar saqlandi: {OUTPUT_DIR / 'question_ids.json'}")

        # ── Step 3: Fetch each question ────────────────────────────────────
        print("\n[3] Savollar yuklanmoqda...")
        collected = {"math": {}, "english": {}}
        total_fetched = 0
        errors = 0

        for module in ["math", "english"]:
            ids_map = all_ids[module]
            print(f"\n  {module.upper()} ({len(ids_map)} savol)...")

            for i, (qid, skill) in enumerate(ids_map.items()):
                try:
                    q_url = f"{ONEPREP_BASE}/questions/{qid}"
                    resp = await page.goto(q_url, wait_until="domcontentloaded", timeout=15000)
                    await asyncio.sleep(0.8)

                    # Try JSON from __NEXT_DATA__
                    q_data = await page.evaluate("""() => {
                        const el = document.getElementById('__NEXT_DATA__');
                        if (!el) return null;
                        try { return JSON.parse(el.textContent); } catch(e) { return null; }
                    }""")

                    parsed = None
                    if q_data:
                        try:
                            pp = q_data.get("props", {}).get("pageProps", {})
                            qobj = pp.get("question") or pp.get("questionData") or pp.get("data")
                            if qobj:
                                parsed = extract_from_json(qobj)
                        except Exception:
                            pass

                    if not parsed:
                        # Fallback: extract from visible DOM
                        dom_data = await page.evaluate("""() => {
                            const getEl = (sel) => document.querySelector(sel)?.innerText?.trim() || '';

                            // Try to find question text
                            const questionSelectors = [
                                '[data-testid="question-stem"]',
                                '.question-text', '.question-body',
                                'h1', '[class*="question"]',
                                'main p:first-of-type'
                            ];
                            let content = '';
                            for (const sel of questionSelectors) {
                                const el = document.querySelector(sel);
                                if (el && el.innerText.length > 20) {
                                    content = el.innerText.trim();
                                    break;
                                }
                            }

                            // Try to find choices
                            const choiceEls = [
                                ...document.querySelectorAll('[data-testid*="choice"], [class*="choice"], [class*="option"], [role="radio"]')
                            ];
                            const choices = choiceEls.slice(0, 4).map((el, i) => ({
                                option: String.fromCharCode(65 + i),
                                text: el.innerText.trim()
                            }));

                            // Correct answer
                            const correctEl = document.querySelector('[data-testid="correct-answer"], [class*="correct"], [aria-checked="true"]');

                            return { content, choices, correct: correctEl?.innerText?.trim() || '' };
                        }""")

                        if dom_data and dom_data.get("content"):
                            choices = dom_data.get("choices", [])
                            parsed = {
                                "content": dom_data["content"],
                                "choice_a": next((c["text"] for c in choices if c["option"] == "A"), ""),
                                "choice_b": next((c["text"] for c in choices if c["option"] == "B"), ""),
                                "choice_c": next((c["text"] for c in choices if c["option"] == "C"), ""),
                                "choice_d": next((c["text"] for c in choices if c["option"] == "D"), ""),
                                "correct_answer": dom_data.get("correct", "")[:1].upper(),
                                "difficulty": "medium",
                                "explanation": "",
                                "question_type": "MCQ" if choices else "INPUT",
                                "_skill": skill,
                            }

                    if parsed:
                        parsed["_skill"] = skill
                        if skill not in collected[module]:
                            collected[module][skill] = []
                        collected[module][skill].append(parsed)
                        total_fetched += 1
                    else:
                        errors += 1

                    if (i + 1) % 10 == 0:
                        print(f"    {i+1}/{len(ids_map)} ({errors} xato)")

                except Exception as e:
                    errors += 1
                    if errors <= 5:
                        print(f"    Xato [{qid}]: {e}")

                # Rate limit
                await asyncio.sleep(0.3)

        print(f"\n  Jami olindi: {total_fetched} savol ({errors} xato)")

        # Save raw data
        raw_path = OUTPUT_DIR / "questions_raw.json"
        with open(raw_path, "w", encoding="utf-8") as f:
            json.dump(collected, f, ensure_ascii=False, indent=2)
        print(f"  Raw data saqlandi: {raw_path}")

        await browser.close()

    return collected


# ── IMPORT TO EXAMBRIDGE ───────────────────────────────────────────────────────
def import_all(collected: dict):
    print("\n[4] ExamBridge ga import qilinmoqda...")
    token = get_exambridge_token()
    if not token:
        print("  ExamBridge token olinmadi!")
        return

    total_imported = 0

    # Math
    for skill, questions in collected.get("math", {}).items():
        category = MATH_CATEGORY_MAP.get(skill, "algebra")
        topic = {
            "H.A": "Algebra",
            "H.B": "Advanced Math",
            "H.C": "Problem Solving & Data Analysis",
            "H.D": "Geometry & Trigonometry",
            "H.E": "Additional Topics",
        }.get(skill, skill)
        # Remove internal fields
        clean_qs = [{k: v for k, v in q.items() if not k.startswith("_")} for q in questions]
        n = import_to_exambridge(token, "Matematika", category, topic, clean_qs)
        total_imported += n

    # English
    for skill, questions in collected.get("english", {}).items():
        category = ENGLISH_CATEGORY_MAP.get(skill, "craft_structure")
        topic = {
            "INF": "Information and Ideas",
            "CAS": "Craft and Structure",
            "EOI": "Expression of Ideas",
            "SEC": "Standard English Conventions",
            "WIC": "Words in Context",
            "CTE": "Command of Evidence (Textual)",
            "TSP": "Text Structure and Purpose",
            "SYN": "Rhetorical Synthesis",
            "RWE": "Transitions",
        }.get(skill, skill)
        clean_qs = [{k: v for k, v in q.items() if not k.startswith("_")} for q in questions]
        n = import_to_exambridge(token, "Ingliz tili", category, topic, clean_qs)
        total_imported += n

    print(f"\n  Jami import qilindi: {total_imported} savol")


# ── ENTRY POINT ────────────────────────────────────────────────────────────────
async def main():
    print("=" * 60)
    print("  OnePrep SAT Scraper → ExamBridge Importer")
    print("=" * 60)

    # Check if raw data already exists
    raw_path = OUTPUT_DIR / "questions_raw.json"
    if raw_path.exists():
        print(f"\nMavjud raw data topildi: {raw_path}")
        choice = input("Qayta scrape qilasizmi? (y/N): ").strip().lower()
        if choice != "y":
            with open(raw_path, encoding="utf-8") as f:
                collected = json.load(f)
            import_all(collected)
            return

    collected = await scrape_oneprep()
    import_all(collected)
    print("\nDone!")


if __name__ == "__main__":
    asyncio.run(main())
