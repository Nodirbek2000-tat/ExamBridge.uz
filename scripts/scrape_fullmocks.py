# -*- coding: utf-8 -*-
"""
SATStation Full Mock Tests -> ExamBridge
Barcha full testlarni satstation.io dan olib, exambridge.uz ga import qiladi.
- Har bir test uchun yangi fresh akkount ishlatiladi (limit muammosi hal)
- Rasmlar SSH/SFTP orqali serverga yuklanadi (URL emas, fayl sifatida)
- Progress data/fullmock_progress.json ga saqlanadi

Ishlatish:
  cd E:\SAT_plus\sat
  venv\Scripts\python scripts\scrape_fullmocks.py
"""

import requests
import json
import time
import re
import io
import os
import sys
import paramiko
import urllib3

urllib3.disable_warnings()

# ── CONFIG ─────────────────────────────────────────────────────────────────
SAT_BASE  = "https://api.satstation.io/api/v1"
SAT_EMAIL = "nodirbekshukurov382@gmail.com"
SAT_PASS  = "Nodirbek2000"

# Fresh akkountlar: har biri 1 ta test uchun ishlatiladi
FRESH_BASE = "nodirbekshukurov382+mock{:02d}@gmail.com"
FRESH_PASS = "Test1234567"
FRESH_COUNT = 30  # 30 ta akkount zaxirasi

EB_BASE  = "https://exambridge.uz/api"
EB_EMAIL = "nodirbek.shukurov09q@gmail.com"
EB_PASS  = "Nodirbek_2000"

SERVER    = "161.97.107.51"
SSH_USER  = "root"
SSH_PASS  = "Nodirbek2000"
MEDIA_DIR = "/app/media/sat_images/"

DATA_DIR      = os.path.join(os.path.dirname(__file__), "..", "data")
PROGRESS_FILE = os.path.join(DATA_DIR, "fullmock_progress.json")

DELAY = 0.3

DOMAIN_MAP = {
    'craft_and_structure':               'craft_structure',
    'information_and_ideas':             'info_ideas',
    'standard_english_conventions':      'standard_english',
    'expression_of_ideas':               'expression_ideas',
    'problem_solving_and_data_analysis': 'problem_data',
    'geometry_and_trigonometry':         'geometry',
    'algebra':                           'algebra',
    'advanced_math':                     'advanced_math',
}

MONTHS = {
    'january': 1, 'february': 2, 'march': 3, 'april': 4,
    'may': 5, 'june': 6, 'july': 7, 'august': 8,
    'september': 9, 'october': 10, 'november': 11, 'december': 12,
}

# ── Progress ────────────────────────────────────────────────────────────────
def load_progress():
    if os.path.exists(PROGRESS_FILE):
        with open(PROGRESS_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {"done_ids": [], "fresh_idx": 1}

def save_progress(prog):
    with open(PROGRESS_FILE, 'w', encoding='utf-8') as f:
        json.dump(prog, f, indent=2, ensure_ascii=False)

# ── SSH/SFTP ─────────────────────────────────────────────────────────────
def connect_ssh():
    print("SSH ulanmoqda...")
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(SERVER, username=SSH_USER, password=SSH_PASS, timeout=20)
    sftp = client.open_sftp()
    client.exec_command(f'mkdir -p {MEDIA_DIR}')
    time.sleep(0.3)
    print("SSH ulandi.\n")
    return client, sftp

_uploaded_cache = set()

def upload_image(sftp, img_path):
    if not img_path:
        return ""
    full_url = img_path if img_path.startswith('http') else "https://api.satstation.io" + img_path
    fname = re.sub(r'\?.*$', '', full_url.split('/')[-1])
    if not fname or '.' not in fname:
        return full_url
    server_path = MEDIA_DIR + fname
    media_url   = "/media/sat_images/" + fname

    if fname in _uploaded_cache:
        return media_url
    try:
        sftp.stat(server_path)
        _uploaded_cache.add(fname)
        return media_url
    except FileNotFoundError:
        pass

    for attempt in range(3):
        try:
            r = requests.get(full_url, timeout=20)
            if r.status_code == 200:
                sftp.putfo(io.BytesIO(r.content), server_path)
                _uploaded_cache.add(fname)
                print(f"          +rasm: {fname}")
                return media_url
        except Exception as e:
            print(f"          rasm xato ({attempt+1}/3): {e}")
            time.sleep(2)
    return full_url

# ── SATStation akkount ────────────────────────────────────────────────────
def sat_register(email, pwd=FRESH_PASS):
    username = re.sub(r'[^a-z0-9]', '', email.split('@')[0].replace('+', ''))[:20]
    r = requests.post(f"{SAT_BASE}/auth/register", json={
        "email": email, "password": pwd,
        "username": username, "full_name": "SAT User",
    }, timeout=20)
    return r.status_code in (200, 201, 400)  # 400 = allaqachon bor

def sat_login_account(email, pwd=FRESH_PASS):
    for i in range(3):
        try:
            r = requests.post(f"{SAT_BASE}/auth/login",
                              json={"email": email, "password": pwd}, timeout=20)
            if r.ok:
                token = r.json().get("access_token")
                return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
        except Exception as e:
            print(f"    login xato ({i+1}/3): {e}")
            time.sleep(3)
    return None

def get_fresh_headers(prog):
    """Yangi akkount yaratib, uning headerlarini qaytaradi."""
    idx = prog.get('fresh_idx', 1)
    while idx <= FRESH_COUNT:
        email = FRESH_BASE.format(idx)
        print(f"    Fresh akkount: {email}")
        sat_register(email)
        time.sleep(1)
        h = sat_login_account(email)
        if h:
            prog['fresh_idx'] = idx + 1
            save_progress(prog)
            return h
        idx += 1
    return None

def get_all_tests(sat_h):
    r = requests.get(f"{SAT_BASE}/tests", headers=sat_h, timeout=20)
    r.raise_for_status()
    data = r.json()
    if isinstance(data, list):
        return data
    return data.get('items', data.get('results', []))

# ── ExamBridge ──────────────────────────────────────────────────────────
def eb_login():
    s = requests.Session()
    s.verify = False
    r = s.post(f"{EB_BASE}/auth/login/",
               json={"email": EB_EMAIL, "password": EB_PASS}, timeout=20)
    r.raise_for_status()
    token = r.json().get("access") or r.json().get("token")
    s.headers["Authorization"] = f"Bearer {token}"
    print(f"ExamBridge login: OK\n")
    return s

# ── Scraping ─────────────────────────────────────────────────────────────
def scrape_module(sat_h, attempt_id, sftp):
    for attempt in range(3):
        try:
            r = requests.get(
                f"{SAT_BASE}/attempts/{attempt_id}/current-module",
                headers=sat_h, timeout=25)
            if not r.ok:
                return None, None
            break
        except Exception as e:
            print(f"      current-module xato ({attempt+1}/3): {e}")
            time.sleep(3)
    else:
        return None, None

    m = r.json()
    if not m.get('questions'):
        return None, None

    answers = [{"question_id": q['id'], "selected_option": "A", "time_spent_seconds": 5}
               for q in m['questions']]

    for attempt in range(3):
        try:
            r2 = requests.post(
                f"{SAT_BASE}/attempts/{attempt_id}/submit-module",
                headers=sat_h,
                json={"module_id": m['id'], "answers": answers, "time_spent_seconds": 300},
                timeout=30)
            break
        except Exception as e:
            print(f"      submit xato ({attempt+1}/3): {e}")
            time.sleep(3)
    else:
        return m, {}

    ans_map = {}
    if r2.ok:
        for qr in r2.json().get('question_results', []):
            ca = qr.get('correct_answer') or ['A']
            ans_map[qr['id']] = ca[0] if isinstance(ca, list) else str(ca)

    return m, ans_map

def parse_question(q, ans_map, sftp):
    img = upload_image(sftp, q.get('question_image_url') or '')

    passage = ""
    p = q.get('passage')
    if p and isinstance(p, dict):
        passage = p.get('content') or p.get('text') or ''

    qtype = "MCQ" if q.get('question_type') == 'multiple_choice' else 'INPUT'
    choices = []
    for o in (q.get('options') or []):
        choice_img = upload_image(sftp, o.get('image_url') or '')
        text = o.get('text') or ''
        if choice_img and not text:
            text = choice_img
        choices.append({"option": o['id'], "text": text})

    domain = q.get('domain') or 'info_ideas'
    correct = ans_map.get(q['id'], 'A')
    if isinstance(correct, list):
        correct = correct[0]

    return {
        "number":         q.get('question_number', 1),
        "question_type":  qtype,
        "content":        q.get('question_text') or q.get('content') or '',
        "math_equation":  img,
        "passage":        passage,
        "table_data":     None,
        "correct_answer": correct,
        "difficulty":     (q.get('difficulty') or 'medium').upper(),
        "category":       DOMAIN_MAP.get(domain, domain),
        "topic":          q.get('skill') or q.get('topic') or '',
        "explanation":    q.get('explanation') or '',
        "choices":        choices,
    }

def scrape_test(sat_h, test_id, sftp, prog):
    """Test uchun attempt yaratadi. 402 bo'lsa fresh akkount ishlatadi."""
    for retry in range(5):
        for attempt in range(3):
            try:
                r = requests.post(f"{SAT_BASE}/attempts", headers=sat_h,
                                  json={"test_id": test_id}, timeout=20)
                break
            except Exception as e:
                print(f"    attempt create xato ({attempt+1}/3): {e}")
                time.sleep(5)
        else:
            return None, sat_h

        if r.status_code == 402:
            print(f"    402 limit to'lgan — yangi akkount olinmoqda... (urinish {retry+1})")
            new_h = get_fresh_headers(prog)
            if not new_h:
                print("    Fresh akkount topilmadi!")
                return None, sat_h
            sat_h = new_h
            time.sleep(2)
            continue

        if not r.ok:
            print(f"    Attempt fail: {r.status_code} {r.text[:150]}")
            return None, sat_h

        attempt_id = r.json()['id']
        print(f"    Attempt ID: {attempt_id}")

        mods = {}
        for i in range(4):
            m, ans_map = scrape_module(sat_h, attempt_id, sftp)
            if not m:
                break
            key = f"{m['section']}_{m['module']}"
            questions = [parse_question(q, ans_map, sftp) for q in m['questions']]
            mods[key] = questions
            print(f"    {key}: {len(questions)} savol")
            time.sleep(DELAY)

        return (mods if mods else None), sat_h

    return None, sat_h

# ── Title parsing ─────────────────────────────────────────────────────────
def parse_title(title):
    tl = title.lower()
    month = next((v for k, v in MONTHS.items() if k in tl), 1)
    ym = re.search(r'20\d\d', title)
    year = int(ym.group()) if ym else 2024
    intl = 'intl' in tl or 'international' in tl
    fm = re.search(r'form[_\s]+([a-z])', tl)
    form = fm.group(1).upper() if fm else 'A'
    return year, month, intl, form

# ── Import to ExamBridge ──────────────────────────────────────────────────
def import_test(eb, mods, test_info):
    title = test_info['title']
    year, month, intl, form = parse_title(title)

    e1 = mods.get('reading_writing_module_1', [])
    e2 = mods.get('reading_writing_module_2', [])
    m1 = mods.get('math_module_1', [])
    m2 = mods.get('math_module_2', [])

    if not (e1 and m1):
        print(f"    Modullar yetarli emas: e1={len(e1)} e2={len(e2)} m1={len(m1)} m2={len(m2)}")
        return False

    print(f"    Import: {year}/{month:02d} Form={form} Intl={intl} | "
          f"e1={len(e1)} e2={len(e2)} m1={len(m1)} m2={len(m2)}")

    payload = {
        "test_mode":        "INDIVIDUAL",
        "year":             year,
        "month":            month,
        "form":             form,
        "is_international": intl,
        "is_premium":       False,
        "english_m1":       e1,
        "english_m2":       e2,
        "math_m1":          m1,
        "math_m2":          m2,
    }

    for attempt in range(3):
        try:
            r = eb.post(f"{EB_BASE}/import/sat/mock/", json=payload, timeout=120)
            break
        except Exception as e:
            print(f"    import xato ({attempt+1}/3): {e}")
            time.sleep(5)
    else:
        return False

    if r.ok:
        d = r.json()
        print(f"    IMPORT OK: test_id={d.get('test_id')} | '{d.get('test_name')}' | {d.get('total_questions')} savol")
        return True
    else:
        print(f"    IMPORT FAIL ({r.status_code}): {r.text[:250]}")
        return False

# ── Main ──────────────────────────────────────────────────────────────────
def main():
    prog = load_progress()
    done_ids = set(prog.get('done_ids', []))

    ssh, sftp = connect_ssh()

    try:
        # Test ro'yxatini olish uchun asosiy akkount
        main_h = sat_login_account(SAT_EMAIL, SAT_PASS)
        if not main_h:
            sys.exit("SATStation login muvaffaqiyatsiz!")
        print("SATStation login: OK")

        eb = eb_login()

        all_tests = get_all_tests(main_h)
        print(f"SATStation da jami testlar: {len(all_tests)}")
        for t in all_tests:
            done = t['id'] in done_ids
            print(f"  [{t['id']:3d}] {t['title']:<55} {'[DONE]' if done else ''}")

        to_import = [t for t in all_tests if t['id'] not in done_ids]
        print(f"\nQolgan: {len(to_import)} ta test\n{'='*65}")

        # Har test uchun fresh akkount ishlatamiz
        current_h = get_fresh_headers(prog)
        if not current_h:
            sys.exit("Fresh akkount yaratib bo'lmadi!")

        results = []
        for test in to_import:
            title   = test['title']
            test_id = test['id']
            print(f"\n[{test_id}] {title}")

            mods, current_h = scrape_test(current_h, test_id, sftp, prog)

            if not mods:
                print("  => SKIP")
                results.append({"id": test_id, "title": title, "status": "SKIP"})
                continue

            print(f"    Modullar: {list(mods.keys())}")
            ok = import_test(eb, mods, test)
            if ok:
                done_ids.add(test_id)
                prog['done_ids'] = list(done_ids)
                save_progress(prog)
                results.append({"id": test_id, "title": title, "status": "OK"})
            else:
                results.append({"id": test_id, "title": title, "status": "FAIL"})

            time.sleep(0.5)

    finally:
        sftp.close()
        ssh.close()
        print("\nSSH yopildi.")

    print(f"\n{'='*65}\nNATIJA:")
    ok_c   = sum(1 for r in results if r['status'] == 'OK')
    skip_c = sum(1 for r in results if r['status'] == 'SKIP')
    fail_c = sum(1 for r in results if r['status'] == 'FAIL')
    print(f"  OK: {ok_c}  |  SKIP: {skip_c}  |  FAIL: {fail_c}  |  Jami: {len(results)}")
    for r in results:
        icon = "OK" if r['status'] == 'OK' else ("--" if r['status'] == 'SKIP' else "XX")
        print(f"  [{icon}] [{r['id']:3d}] {r['title']}")

if __name__ == '__main__':
    main()

