# -*- coding: utf-8 -*-
"""Debug: satstation coins va bitta test attempt ni tekshiradi."""
import requests, json
import urllib3
urllib3.disable_warnings()

SAT_BASE  = "https://api.satstation.io/api/v1"
SAT_EMAIL = "nodirbekshukurov382@gmail.com"
SAT_PASS  = "Nodirbek2000"

r = requests.post(f"{SAT_BASE}/auth/login",
                  json={"email": SAT_EMAIL, "password": SAT_PASS}, timeout=20)
print("Login:", r.status_code)
token = r.json().get("access_token")
h = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

# Coin balans
r2 = requests.get(f"{SAT_BASE}/coins/balance", headers=h, timeout=10)
print("Coins balance:", r2.status_code, r2.text[:200])

# Profile
r3 = requests.get(f"{SAT_BASE}/auth/me", headers=h, timeout=10)
me = r3.json()
print("Profile coins:", me.get('coins'), "| premium:", me.get('is_premium'))

# Test list
r4 = requests.get(f"{SAT_BASE}/tests", headers=h, timeout=20)
tests = r4.json()
if isinstance(tests, list):
    items = tests
else:
    items = tests.get('items', tests.get('results', []))
print(f"\nTestlar ({len(items)} ta):")
for t in items:
    print(f"  [{t['id']:3d}] coin_cost={t.get('coin_cost',0)} is_premium={t.get('is_premium')} | {t['title']}")

# Birinchi test uchun attempt yaratib ko'r
if items:
    test_id = items[0]['id']
    print(f"\n--- Test {test_id} attempt yaratish ---")
    r5 = requests.post(f"{SAT_BASE}/attempts", headers=h,
                       json={"test_id": test_id}, timeout=20)
    print("Attempt:", r5.status_code)
    print(r5.text[:500])

    if r5.ok:
        attempt_id = r5.json()['id']
        r6 = requests.get(f"{SAT_BASE}/attempts/{attempt_id}/current-module",
                          headers=h, timeout=20)
        print("\ncurrent-module:", r6.status_code)
        d = r6.json()
        print("questions count:", len(d.get('questions', [])))
        print("keys:", list(d.keys()))
        if not d.get('questions'):
            print("FULL response:", json.dumps(d, indent=2)[:600])
