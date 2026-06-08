# -*- coding: utf-8 -*-
import requests, json, time, re, io, paramiko
import urllib3
urllib3.disable_warnings()

SAT_BASE = "https://api.satstation.io/api/v1"
EB_BASE  = "https://exambridge.uz/api"
SERVER   = "161.97.107.51"
MEDIA_DIR = "/app/media/sat_images/"

DOMAIN_MAP = {
    'craft_and_structure': 'craft_structure',
    'information_and_ideas': 'info_ideas',
    'standard_english_conventions': 'standard_english',
    'expression_of_ideas': 'expression_ideas',
    'problem_solving_and_data_analysis': 'problem_data',
    'geometry_and_trigonometry': 'geometry',
}

ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect(SERVER, username="root", password="Nodirbek2000", timeout=15)
sftp = ssh.open_sftp()
ssh.exec_command(f'mkdir -p {MEDIA_DIR}')
time.sleep(0.3)

def upload_image(img_path):
    if not img_path:
        return ""
    base = "https://api.satstation.io"
    full_url = img_path if img_path.startswith('http') else base + img_path
    fname = re.sub(r'\?.*$', '', full_url.split('/')[-1])
    if not fname or '.' not in fname:
        return full_url
    server_path = MEDIA_DIR + fname
    media_url = "/media/sat_images/" + fname
    try:
        sftp.stat(server_path)
        return media_url
    except FileNotFoundError:
        pass
    try:
        r = requests.get(full_url, timeout=15)
        if r.status_code == 200:
            sftp.putfo(io.BytesIO(r.content), server_path)
            print(f"      +img: {fname}")
            return media_url
    except Exception as e:
        print(f"      img err: {e}")
    return full_url

def get_submit(h, attempt_id):
    r = requests.get(f"{SAT_BASE}/attempts/{attempt_id}/current-module", headers=h)
    if not r.ok: return None, None
    m = r.json()
    if not m.get('questions'): return None, None
    answers = [{"question_id": q['id'], "selected_option": "A", "time_spent_seconds": 5}
               for q in m['questions']]
    r2 = requests.post(f"{SAT_BASE}/attempts/{attempt_id}/submit-module", headers=h,
        json={"module_id": m['id'], "answers": answers, "time_spent_seconds": 300})
    if not r2.ok: return m, {}
    ans_map = {qr['id']: (qr.get('correct_answer') or ['A'])[0]
               for qr in r2.json().get('question_results', [])}
    return m, ans_map

def to_q(module, ans_map):
    out = []
    for q in module['questions']:
        img = upload_image(q.get('question_image_url') or '')
        passage = ""
        if q.get('passage') and q['passage'].get('content'):
            passage = q['passage']['content']
        qtype = "MCQ" if q.get('question_type') == 'multiple_choice' else 'INPUT'
        choices = [{"option": o['id'], "text": o.get('text','')} for o in (q.get('options') or [])]
        domain = q.get('domain') or 'info_ideas'
        out.append({
            "number": q['question_number'],
            "question_type": qtype,
            "content": q.get('question_text',''),
            "math_equation": img,
            "passage": passage,
            "table_data": None,
            "correct_answer": ans_map.get(q['id'], 'A'),
            "difficulty": "MEDIUM",
            "category": DOMAIN_MAP.get(domain, domain),
            "topic": "",
            "explanation": "",
            "choices": choices,
        })
    return out

# Login SATStation - use new account
email = "nodirbekshukurov382+d01@gmail.com"
requests.post(f"{SAT_BASE}/auth/register", json={
    "email": email, "password": "Test1234567",
    "username": "satuser_d01", "full_name": "SAT User"
})
time.sleep(1)
r = requests.post(f"{SAT_BASE}/auth/login", json={"email": email, "password": "Test1234567"})
print("Login:", r.status_code)
token = r.json().get('access_token')
h = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

# Start attempt for test 99 (free)
r2 = requests.post(f"{SAT_BASE}/attempts", headers=h, json={"test_id": 99})
print("Attempt:", r2.status_code, r2.text[:100])
attempt_id = r2.json()['id']

mods = {}
for i in range(4):
    m, ans_map = get_submit(h, attempt_id)
    if not m: break
    key = f"{m['section']}_{m['module']}"
    mods[key] = to_q(m, ans_map)
    print(f"  {key}: {len(mods[key])} q")
    time.sleep(0.3)

# Import to exambridge
eb = requests.Session()
eb.verify = False
r = eb.post(f"{EB_BASE}/auth/login/", json={"email":"nodirbek.shukurov09q@gmail.com","password":"Nodirbek_2000"})
eb.headers['Authorization'] = f"Bearer {r.json()['access']}"

e1 = mods.get('reading_writing_module_1',[])
e2 = mods.get('reading_writing_module_2',[])
m1 = mods.get('math_module_1',[])
m2 = mods.get('math_module_2',[])

payload = {
    "test_mode": "FULL", "year": 2024, "month": 4, "form": "A",
    "is_international": False, "is_premium": False,
    "english_m1": e1, "english_m2_easy": e2, "english_m2_medium": e2, "english_m2_hard": e2,
    "math_m1": m1, "math_m2_easy": m2, "math_m2_medium": m2, "math_m2_hard": m2,
}
r3 = eb.post(f"{EB_BASE}/import/sat/mock/", json=payload)
print("Import:", r3.status_code, r3.text[:200])

# Check coins/referral for premium tests
r_coins = requests.get(f"{SAT_BASE}/coins/balance", headers=h)
print("Coins:", r_coins.text[:100])
r_ref = requests.get(f"{SAT_BASE}/coins/referral-link", headers=h)
print("Referral:", r_ref.text[:200])

ssh.close()
