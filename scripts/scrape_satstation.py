# -*- coding: utf-8 -*-
import requests, json, time, re, io, paramiko
import urllib3
urllib3.disable_warnings()

SAT_BASE     = "https://api.satstation.io/api/v1"
SAT_IMG      = "https://api.satstation.io"
EB_BASE      = "https://exambridge.uz/api"
SERVER       = "161.97.107.51"
SSH_PASS     = "Nodirbek2000"
MEDIA_DIR    = "/app/media/sat_images/"
MAIN_EMAIL   = "nodirbekshukurov382@gmail.com"
MAIN_PASS    = "Nodirbek2000"
REFERRAL_CODE = "cs1zpmgj"

DOMAIN_MAP = {
    'craft_and_structure':           'craft_structure',
    'information_and_ideas':         'info_ideas',
    'standard_english_conventions':  'standard_english',
    'expression_of_ideas':           'expression_ideas',
    'problem_solving_and_data_analysis': 'problem_data',
    'geometry_and_trigonometry':     'geometry',
}

# ── SSH/SFTP ────────────────────────────────────────────────────────────────
print("Connecting to server...")
ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect(SERVER, username="root", password=SSH_PASS, timeout=15)
sftp = ssh.open_sftp()
ssh.exec_command(f'mkdir -p {MEDIA_DIR}')
time.sleep(0.5)
print("SSH connected.")

def upload_image(img_path):
    if not img_path:
        return ""
    full_url = img_path if img_path.startswith('http') else SAT_IMG + img_path
    fname = re.sub(r'\?.*$', '', full_url.split('/')[-1])
    if not fname or '.' not in fname:
        return full_url
    server_path = MEDIA_DIR + fname
    media_url   = "/media/sat_images/" + fname
    try:
        sftp.stat(server_path)
        return media_url
    except FileNotFoundError:
        pass
    try:
        r = requests.get(full_url, timeout=15)
        if r.status_code == 200:
            sftp.putfo(io.BytesIO(r.content), server_path)
            print(f"      uploaded: {fname}")
            return media_url
    except Exception as e:
        print(f"      img fail: {e}")
    return full_url

# ── SATStation auth ──────────────────────────────────────────────────────────
def sat_register(email, pwd="Test1234567", ref_code=None):
    payload = {
        "email": email, "password": pwd,
        "username": re.sub(r'[^a-zA-Z0-9]', '', email.split('@')[0])[:20],
        "full_name": "SAT User",
    }
    if ref_code:
        payload["referral_code"] = ref_code
    r = requests.post(f"{SAT_BASE}/auth/register", json=payload)
    return r.status_code, r.json() if r.ok else r.text[:100]

def sat_login(email, pwd="Test1234567"):
    r = requests.post(f"{SAT_BASE}/auth/login", json={"email": email, "password": pwd})
    if r.ok:
        return r.json().get('access_token')
    return None

def sat_login_full(email, pwd="Test1234567", ref_code=None):
    sat_register(email, pwd, ref_code)
    time.sleep(1)
    return sat_login(email, pwd)

def get_profile(token):
    r = requests.get(f"{SAT_BASE}/auth/me", headers={"Authorization": f"Bearer {token}"})
    return r.json() if r.ok else {}

# ── Phase 1: Earn coins via referrals ───────────────────────────────────────
def sat_login_retry(email, pwd, retries=3, delay=5):
    for attempt in range(retries):
        tok = sat_login(email, pwd)
        if tok:
            return tok
        print(f"    login retry {attempt+1}/{retries}...")
        time.sleep(delay)
    return None

def earn_coins():
    print("\n=== PHASE 1: Earning coins via referrals ===")
    time.sleep(5)
    main_tok = sat_login_retry(MAIN_EMAIL, MAIN_PASS)
    if not main_tok:
        print("Main account login failed!")
        return None
    profile = get_profile(main_tok)
    coins = profile.get('coins', 0)
    print(f"Current coins: {coins}")
    print(f"Referral code: {REFERRAL_CODE}")

    # Need 9 premium tests × 2 coins = 18 coins, 2 referrals per coin → 36 referrals
    needed_coins = 18
    # Count already-registered referral accounts (ref001-ref045 partially registered)
    # Resume from ref046
    batch_emails = [
        f"nodirbekshukurov382+ref{i:03d}@gmail.com"
        for i in range(46, 200)
    ]
    print(f"Need {needed_coins} coins, have {coins}.")
    if coins >= needed_coins:
        print("Already have enough coins!")
        return main_tok

    print(f"Registering referral accounts with 3s delay...")
    registered = 0
    for i, email in enumerate(batch_emails):
        sc, resp = sat_register(email, "Test1234567", REFERRAL_CODE)
        ok = False
        if sc in (200, 201):
            print(f"  [{i+1}] {email} -> registered (201)")
            ok = True
        elif sc == 400:
            print(f"  [{i+1}] {email} -> already exists")
            ok = True
        elif sc == 429:
            print(f"  [{i+1}] 429 rate-limit, sleeping 15s...")
            time.sleep(15)
            # retry once
            sc2, resp2 = sat_register(email, "Test1234567", REFERRAL_CODE)
            if sc2 in (200, 201):
                print(f"    retry OK")
                ok = True
        else:
            print(f"  [{i+1}] {email} -> {sc}")

        if ok:
            # Login with referred account to trigger referral credit
            time.sleep(2)
            tok = sat_login(email, "Test1234567")
            if tok:
                print(f"    logged in -> referral triggered")
            registered += 1

        time.sleep(3)

        # Check coins every 4 successful registrations
        if registered > 0 and registered % 4 == 0:
            time.sleep(3)
            main_tok = sat_login_retry(MAIN_EMAIL, MAIN_PASS)
            if main_tok:
                profile = get_profile(main_tok)
                coins = profile.get('coins', 0)
                print(f"  >>> Coins so far: {coins} (after {registered} referrals)")
                if coins >= needed_coins:
                    print("  >>> Enough coins! Stopping referrals.")
                    return main_tok

    # Final check
    time.sleep(3)
    main_tok = sat_login_retry(MAIN_EMAIL, MAIN_PASS)
    if main_tok:
        profile = get_profile(main_tok)
        coins = profile.get('coins', 0)
        print(f"Final coins: {coins}")
    return main_tok

# ── SATStation scraping ──────────────────────────────────────────────────────
def get_submit_module(h, attempt_id):
    r = requests.get(f"{SAT_BASE}/attempts/{attempt_id}/current-module", headers=h)
    if not r.ok:
        return None, None
    m = r.json()
    if not m.get('questions'):
        return None, None
    answers = [{"question_id": q['id'], "selected_option": "A", "time_spent_seconds": 5}
               for q in m['questions']]
    r2 = requests.post(f"{SAT_BASE}/attempts/{attempt_id}/submit-module", headers=h,
        json={"module_id": m['id'], "answers": answers, "time_spent_seconds": 300})
    if not r2.ok:
        return m, {}
    ans_map = {qr['id']: (qr.get('correct_answer') or ['A'])[0]
               for qr in r2.json().get('question_results', [])}
    return m, ans_map

def to_questions(module, ans_map):
    out = []
    for q in module['questions']:
        q_img   = upload_image(q.get('question_image_url') or '')
        passage = ""
        if q.get('passage'):
            passage = q['passage'].get('content') or ''
        qtype   = "MCQ" if q.get('question_type') == 'multiple_choice' else 'INPUT'
        choices = [{"option": o['id'], "text": o.get('text', '')}
                   for o in (q.get('options') or [])]
        domain  = q.get('domain') or 'info_ideas'
        out.append({
            "number":        q['question_number'],
            "question_type": qtype,
            "content":       q.get('question_text', ''),
            "math_equation": q_img,
            "passage":       passage,
            "table_data":    None,
            "correct_answer": ans_map.get(q['id'], 'A'),
            "difficulty":    "MEDIUM",
            "category":      DOMAIN_MAP.get(domain, domain),
            "topic":         "",
            "explanation":   "",
            "choices":       choices,
        })
    return out

def scrape_test(h, test_id):
    r = requests.post(f"{SAT_BASE}/attempts", headers=h, json={"test_id": test_id})
    if not r.ok:
        print(f"      attempt fail: {r.status_code} {r.text[:150]}")
        return None
    attempt_id = r.json()['id']
    mods = {}
    for _ in range(4):
        m, ans_map = get_submit_module(h, attempt_id)
        if not m:
            break
        key = f"{m['section']}_{m['module']}"
        mods[key] = to_questions(m, ans_map)
        print(f"      {key}: {len(mods[key])} q")
        time.sleep(0.3)
    return mods if mods else None

# ── Exambridge import ────────────────────────────────────────────────────────
eb = requests.Session()
eb.verify = False
_lr = eb.post(f"{EB_BASE}/auth/login/",
              json={"email": "nodirbek.shukurov09q@gmail.com", "password": "Nodirbek_2000"})
eb.headers['Authorization'] = f"Bearer {_lr.json()['access']}"

def parse_title(title):
    months = {'january':1,'february':2,'march':3,'april':4,'may':5,'june':6,
               'july':7,'august':8,'september':9,'october':10,'november':11,'december':12}
    tl = title.lower()
    month = next((v for k,v in months.items() if k in tl), 1)
    ym = re.search(r'20\d\d', title)
    year = int(ym.group()) if ym else 2024
    intl = 'intl' in tl or 'international' in tl
    return year, month, intl

def import_test(mods, test_info):
    year, month, intl = parse_title(test_info['title'])
    e1 = mods.get('reading_writing_module_1', [])
    e2 = mods.get('reading_writing_module_2', [])
    m1 = mods.get('math_module_1', [])
    m2 = mods.get('math_module_2', [])
    payload = {
        "test_mode": "FULL", "year": year, "month": month, "form": "A",
        "is_international": intl, "is_premium": False,
        "english_m1":      e1,
        "english_m2_easy": e2, "english_m2_medium": e2, "english_m2_hard": e2,
        "math_m1":         m1,
        "math_m2_easy":    m2, "math_m2_medium":    m2, "math_m2_hard":    m2,
    }
    r = eb.post(f"{EB_BASE}/import/sat/mock/", json=payload)
    return r.status_code, r.json() if r.ok else r.text[:200]

# ── Phase 2: Scrape all tests ────────────────────────────────────────────────
def scrape_all():
    print("\n=== PHASE 2: Scraping all tests ===")
    main_tok = sat_login(MAIN_EMAIL, MAIN_PASS)
    if not main_tok:
        print("Main account login failed!")
        return
    main_h = {"Authorization": f"Bearer {main_tok}", "Content-Type": "application/json"}

    profile = get_profile(main_tok)
    print(f"Main account coins: {profile.get('coins', 0)}")

    all_tests = requests.get(f"{SAT_BASE}/tests", headers=main_h).json().get('items', [])
    print(f"Total tests on SATStation: {len(all_tests)}")

    already_imported = {
        "SAT Practice - November 2024 (Adaptive)",
        "SAT Practice - December 2025",
    }
    test_queue = [t for t in all_tests if t['title'] not in already_imported]
    print(f"Tests to import: {len(test_queue)}")
    for t in test_queue:
        is_p = t.get('is_premium') or t.get('coin_cost', 0) > 0
        print(f"  [{t['id']}] {t['title']} {'(PREMIUM)' if is_p else '(FREE)'}")

    results = []
    fallback_emails = [f"nodirbekshukurov382+f{i:02d}@gmail.com" for i in range(1, 30)]
    fb_idx = 0

    for test in test_queue:
        title = test['title']
        test_id = test['id']
        is_premium = test.get('is_premium') or test.get('coin_cost', 0) > 0
        print(f"\n  [{test_id}] {title} {'[PREMIUM]' if is_premium else '[FREE]'}")

        # Try with main account first (works for all if coins available)
        mods = scrape_test(main_h, test_id)

        # If main failed and test is free, try with a fresh account
        if not mods and not is_premium:
            while fb_idx < len(fallback_emails):
                email = fallback_emails[fb_idx]
                fb_idx += 1
                print(f"    Trying fallback account: {email}")
                tok = sat_login_full(email)
                if not tok:
                    continue
                fh = {"Authorization": f"Bearer {tok}", "Content-Type": "application/json"}
                mods = scrape_test(fh, test_id)
                if mods:
                    break

        if not mods:
            print(f"  SKIP - no access")
            results.append({"test": title, "status": "SKIP"})
            continue

        sc, resp = import_test(mods, test)
        if sc == 200:
            tq = resp.get('total_questions', '?')
            tn = resp.get('test_name', '?')
            print(f"  IMPORTED: {tn} - {tq} questions")
            results.append({"test": title, "status": "OK", "q": tq})
        else:
            print(f"  IMPORT FAIL ({sc}): {resp}")
            results.append({"test": title, "status": "FAIL", "resp": str(resp)[:100]})
        time.sleep(0.5)

    return results

# ── Main ─────────────────────────────────────────────────────────────────────
main_tok = earn_coins()
results  = scrape_all()

ssh.close()

print("\n\n=== SUMMARY ===")
if results:
    ok = sum(1 for r in results if r['status'] == 'OK')
    print(f"Imported: {ok}/{len(results)}")
    for r in results:
        icon = "OK" if r['status'] == 'OK' else "XX"
        print(f"  [{icon}] {r['test']} - {r.get('q', r.get('resp', ''))}")
