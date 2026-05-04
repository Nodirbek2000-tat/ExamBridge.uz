"""
BluebookPlus SAT Test Scraper
Accessible quizzes only - HTTP requests based (no Playwright needed)
"""
import re
import sys
import io
import json
import time
import requests
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

EMAIL      = "nodirbekshukurov382"
PASSWORD   = "Nodirbek_2000"
BASE_URL   = "https://bluebookplus.plus"
OUTPUT_DIR = Path(__file__).parent.parent / "data" / "scraped"
LETTERS    = ["A", "B", "C", "D", "E"]

# ── ALL ACCESSIBLE QUIZ SLUGS (verified from sitemap) ──────────────────────
# Format: (date_key, region, form, section, module, slug)
QUIZ_SLUGS = [
    # DEC 2024 US Form A (only accessible form from 2024)
    ("dec2024","us","a","MATH",    1,"dec2024-us-form-a-math-m1"),
    ("dec2024","us","a","MATH",    2,"dec2024-us-form-a-math-m2"),
    ("dec2024","us","a","ENGLISH", 1,"dec2024-us-form-a-english-m1-2"),
    ("dec2024","us","a","ENGLISH", 2,"dec2024-us-form-a-english-m2"),

    # DEC 2025 US Form A
    ("dec2025","us","a","MATH",    1,"dec2025-us-form-a-math-m1"),
    ("dec2025","us","a","MATH",    2,"dec2025-us-form-a-math-m2"),
    ("dec2025","us","a","ENGLISH", 1,"dec2025-us-form-a-english-m1"),
    ("dec2025","us","a","ENGLISH", 2,"dec2025-us-form-a-english-m2"),

    # DEC 2025 US Form B
    ("dec2025","us","b","MATH",    1,"dec2025-us-form-b-math-m1"),
    ("dec2025","us","b","MATH",    2,"dec2025-us-form-b-math-m2"),
    ("dec2025","us","b","ENGLISH", 1,"dec2025-us-form-b-english-m1"),
    ("dec2025","us","b","ENGLISH", 2,"dec2025-us-form-b-english-m2"),

    # DEC 2025 INTL Form A
    ("dec2025","intl","a","MATH",    1,"dec2025-intl-form-a-math-m1"),
    ("dec2025","intl","a","MATH",    2,"dec2025-intl-form-a-math-m2"),
    ("dec2025","intl","a","ENGLISH", 1,"dec2025-intl-form-a-english-m1"),
    ("dec2025","intl","a","ENGLISH", 2,"dec2025-intl-form-a-english-m2"),

    # DEC 2025 INTL Form B
    ("dec2025","intl","b","MATH",    1,"dec2025-intl-form-b-math-m1"),
    ("dec2025","intl","b","MATH",    2,"dec2025-intl-form-b-math-m2"),
    ("dec2025","intl","b","ENGLISH", 1,"dec2025-intl-form-b-english-m1"),
    ("dec2025","intl","b","ENGLISH", 2,"dec2025-intl-form-b-english-m2"),

    # DEC 2025 INTL Form C
    ("dec2025","intl","c","MATH",    1,"dec2025-intl-form-c-math-m1"),
    ("dec2025","intl","c","MATH",    2,"dec2025-intl-form-c-math-m2"),
    ("dec2025","intl","c","ENGLISH", 1,"dec2025-intl-form-c-english-m1"),
    ("dec2025","intl","c","ENGLISH", 2,"dec2025-intl-form-c-english-m2"),

    # DEC 2025 INTL Form D
    ("dec2025","intl","d","MATH",    1,"dec2025-intl-form-d-math-m1"),
    ("dec2025","intl","d","MATH",    2,"dec2025-intl-form-d-math-m2"),
]


def parse_date_key(date_key):
    MONTHS = {
        "jan":1,"feb":2,"mar":3,"apr":4,"may":5,"jun":6,
        "jul":7,"aug":8,"sep":9,"oct":10,"nov":11,"dec":12
    }
    m = re.match(r"([a-z]+)(\d{4})", date_key)
    return (MONTHS.get(m.group(1), 1), int(m.group(2))) if m else (1, 2024)


def login():
    s = requests.Session()
    s.headers["User-Agent"] = "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
    r = s.get(f"{BASE_URL}/my-account/", timeout=20)
    nm = re.search(r'woocommerce-login-nonce[^\>]+value=["\'](\w+)', r.text)
    nonce = nm.group(1) if nm else ""
    s.post(f"{BASE_URL}/my-account/", data={
        "username": EMAIL, "password": PASSWORD, "login": "Log in",
        "woocommerce-login-nonce": nonce, "_wp_http_referer": "/my-account/",
    }, allow_redirects=True, timeout=20)
    ok = any("wordpress_logged_in" in c.name for c in s.cookies)
    print(f"  Login: {'OK' if ok else 'FAILED'}")
    return s, ok


def clean_text(html):
    """Strip HTML, clean whitespace"""
    t = re.sub(r'<script[^>]*>.*?</script>', ' ', html, flags=re.DOTALL)
    t = re.sub(r'<style[^>]*>.*?</style>', ' ', t, flags=re.DOTALL)
    t = re.sub(r'<[^>]+>', ' ', t)
    t = re.sub(r'\s+', ' ', t)
    return t.strip()


def get_quiz_page(s, slug):
    url = f"{BASE_URL}/quizzes/{slug}/"
    r = s.get(url, timeout=20, allow_redirects=True)
    if r.status_code == 200 and "quizId" in r.text:
        return r.text, url
    return None, url


def parse_quiz_meta(html):
    """Extract quiz metadata for AJAX calls"""
    nm = re.search(r"quiz_nonce:\s*'([\w]+)'", html)
    qi = re.search(r"quizId:\s*(\d+)", html)
    qp = re.search(r"\bquiz:\s*(\d+)", html)
    jm = re.search(r"json:\s*(\{[^;]+?\})\s*,\s*\n", html, re.DOTALL)
    questions = {}
    if jm:
        try:
            questions = json.loads(jm.group(1))
        except:
            pass
    return {
        "nonce":     nm.group(1) if nm else "",
        "quiz_id":   qi.group(1) if qi else "",
        "quiz_post": qp.group(1) if qp else "",
        "questions": questions,
    }


def parse_questions_html(html):
    """Parse question blocks from server-rendered HTML"""
    questions = []

    # Split into question blocks at each wpProQuiz_questionList section
    # Strategy: find each data-question_id block
    blocks = re.split(r'(?=data-question_id=")', html)

    for block in blocks:
        qid_m = re.search(r'data-question_id="(\d+)"', block)
        if not qid_m:
            continue
        qid = qid_m.group(1)
        number = len(questions) + 1

        # Question type
        type_m = re.search(r'data-type="([^"]+)"', block)
        q_type_raw = type_m.group(1) if type_m else "single"

        # Question text - from the nearest legend BEFORE this block
        # We need to look backwards in the HTML for the legend...
        # Instead, match the full question via wpProQuiz_listItem
        q_type = "INPUT" if q_type_raw == "cloze_answer" else "MCQ"

        # Find choice items
        items = re.findall(
            r'data-pos="(\d+)"[^>]*>.*?<input[^>]+>([^<]{0,500}?)(?=<div class="ld-quiz-question-item)',
            block, re.DOTALL
        )
        choices = []
        for pos_str, choice_content in items:
            pos = int(pos_str)
            letter = LETTERS[pos] if pos < len(LETTERS) else str(pos)
            choice_text = clean_text(choice_content)
            choice_img = re.search(r'<img[^>]+src="([^"]+)"', choice_content)
            choices.append({
                "option": letter,
                "text":   choice_text,
                "image":  choice_img.group(1) if choice_img else None,
            })

        if not choices and q_type == "MCQ":
            q_type = "INPUT"

        questions.append({
            "_qid":    qid,
            "number":  number,
            "type_raw": q_type_raw,
            "question_type": q_type,
            "choices": choices,
        })

    return questions


def parse_question_texts(html):
    """Extract question text from legend elements"""
    texts = []
    legends = re.findall(
        r'<legend[^>]*wpProQuiz_question_text[^>]*>(.*?)</legend>',
        html, re.DOTALL
    )
    for leg in legends:
        # Image in question
        img_m = re.search(r'<img[^>]+src="([^"]+)"', leg)
        # Clean text
        txt = clean_text(leg)
        texts.append({
            "content": txt,
            "image":   img_m.group(1) if img_m else None,
        })
    return texts


def find_correct_answers(s, meta):
    """3 AJAX calls to determine correct answers for MCQ questions"""
    questions = meta["questions"]
    if not questions:
        return {}

    mcq_ids  = [qid for qid, qi in questions.items() if qi["type"] == "single"]
    inp_ids  = [qid for qid, qi in questions.items() if qi["type"] != "single"]

    correct = {qid: None for qid in inp_ids}
    remaining = set(mcq_ids)

    for choice_i, letter in enumerate(LETTERS[:3]):
        if not remaining:
            break
        responses = {}
        for qid, qi in questions.items():
            if qi["type"] == "cloze_answer":
                responses[qid] = {
                    "question_pro_id":   qi["id"],
                    "question_post_id":  qi["question_post_id"],
                    "response":          {"0": ""},
                }
            else:
                resp = {str(k): (k == choice_i) for k in range(4)}
                responses[qid] = {
                    "question_pro_id":  qi["id"],
                    "question_post_id": qi["question_post_id"],
                    "response":         resp,
                }

        try:
            r = s.post(f"{BASE_URL}/wp-admin/admin-ajax.php", data={
                "action":           "ld_adv_quiz_pro_ajax",
                "func":             "checkAnswers",
                "data[course_id]":  "0",
                "data[quiz_nonce]": meta["nonce"],
                "data[quiz]":       meta["quiz_post"],
                "data[quizId]":     meta["quiz_id"],
                "data[responses]":  json.dumps(responses),
            }, timeout=20)
            result = r.json()
        except Exception as e:
            print(f"    AJAX err: {e}")
            break

        for qid in list(remaining):
            if result.get(qid, {}).get("c", False):
                correct[qid] = letter
                remaining.remove(qid)

    for qid in remaining:
        correct[qid] = "D"

    return correct


def scrape_quiz(s, slug):
    """Full quiz scrape pipeline"""
    html, url = get_quiz_page(s, slug)
    if not html:
        print(f"    Not accessible: {url}")
        return []

    meta = parse_quiz_meta(html)
    if not meta["questions"]:
        print(f"    No quiz meta found")
        return []

    # Parse question texts and choices from HTML
    texts = parse_question_texts(html)
    q_blocks = parse_questions_html(html)

    print(f"    {len(meta['questions'])} meta Q | {len(texts)} texts | {len(q_blocks)} blocks")

    # Get correct answers via AJAX
    correct_map = find_correct_answers(s, meta)

    # Skip keywords — instruction blocks, not real questions
    SKIP_PATTERNS = [
        "student-produced response entry directions",
        "student produced response",
        "entry directions",
        "for student-produced response questions",
    ]

    # Combine
    questions = []
    num = 1
    for qblock, qtext in zip(q_blocks, texts):
        content = qtext["content"]
        # Skip instruction/direction blocks
        if any(p in content.lower() for p in SKIP_PATTERNS):
            continue

        qid = qblock["_qid"]
        correct = correct_map.get(qid)
        q_type = qblock["question_type"]
        if correct is None:
            q_type = "INPUT"

        choices = qblock["choices"]
        if not choices and q_type == "MCQ":
            q_type = "INPUT"

        questions.append({
            "number":         num,
            "question_type":  q_type,
            "difficulty":     "MEDIUM",
            "content":        content,
            "passage":        None,
            "image":          qtext["image"],
            "correct_answer": correct or "?",
            "explanation":    None,
            "choices":        choices,
        })
        num += 1

    return questions


def build_json(date_key, region, form, section_questions):
    month_num, year = parse_date_key(date_key)
    sections = []
    for sec_type, modules in section_questions.items():
        if not any(v for v in modules.values()):
            continue
        tl = 35 if sec_type == "MATH" else 32
        mods = [
            {"module_number": mn, "time_limit": tl, "questions": qs}
            for mn, qs in sorted(modules.items()) if qs
        ]
        if mods:
            sections.append({"section_type": sec_type, "modules": mods})
    return {
        "test_type":       "SAT",
        "year":            year,
        "month":           month_num,
        "form":            form.upper(),
        "is_international": region == "intl",
        "sections":        sections,
    }


def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    print("Login...")
    s, ok = login()
    if not ok:
        print("Login failed!")
        return

    test_groups = {}
    for (dk, reg, frm, sec, mn, slug) in QUIZ_SLUGS:
        key = (dk, reg, frm)
        if key not in test_groups:
            test_groups[key] = {"MATH": {1: [], 2: []}, "ENGLISH": {1: [], 2: []}}

    for (dk, reg, frm, sec, mn, slug) in QUIZ_SLUGS:
        key = (dk, reg, frm)
        label = f"{dk}_{reg}_form_{frm} | {sec} M{mn}"
        print(f"\n{label}")

        qs = scrape_quiz(s, slug)
        test_groups[key][sec][mn] = qs
        print(f"    => {len(qs)} questions")
        time.sleep(0.3)

    # Save all
    print("\n--- Saving ---")
    for key, sections in test_groups.items():
        dk, reg, frm = key
        data = build_json(dk, reg, frm, sections)
        if data["sections"]:
            fname = f"{dk}_{reg}_form_{frm}.json"
            fpath = OUTPUT_DIR / fname
            with open(fpath, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            total = sum(len(m["questions"]) for s2 in data["sections"] for m in s2["modules"])
            print(f"  {fname}: {total} Q")

    print(f"\nDone! Dir: {OUTPUT_DIR}")


if __name__ == "__main__":
    main()
