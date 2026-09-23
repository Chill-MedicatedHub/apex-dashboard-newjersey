"""
Pull the customer list out of LeafLink and send it to Chill Desk.

LeafLink is the CRM for these accounts, and each customer record carries the
store's address, terms and the people who work there. This copies that across
so the store profile has real contacts instead of an empty tab.

    python pull-leaflink-contacts.py            # show what it found, send nothing
    python pull-leaflink-contacts.py --go       # send it
    python pull-leaflink-contacts.py --only MI  # just one account

Michigan and New Jersey are separate LeafLink accounts with their own keys, so
name them and give each its own:

    LEAFLINK_ACCOUNTS=MI,NJ
    LEAFLINK_API_KEY_MI=...
    LEAFLINK_SELLER_ID_MI=9105
    LEAFLINK_API_KEY_NJ=...
    LEAFLINK_SELLER_ID_NJ=...

One account on its own still works with the plain names:

    LEAFLINK_API_KEY      the key from your scraper's .env or Actions secrets
    LEAFLINK_SELLER_ID    9105 for Michigan

And either way:

    CHILL_DESK_URL        https://chill-desk.onrender.com
    CHILL_INGEST_KEY      from Partner sales -> Generate ingest key

Safe to re-run: existing stores are filled in rather than overwritten, and a
person already on file is never added twice.
"""
import json
import os
import sys
import time

import requests

GO = "--go" in sys.argv
ONLY = None
if "--only" in sys.argv:
    i = sys.argv.index("--only")
    if i + 1 < len(sys.argv):
        ONLY = sys.argv[i + 1].strip().upper()

DESK = os.getenv("CHILL_DESK_URL", "https://chill-desk.onrender.com").rstrip("/")
INGEST = os.getenv("CHILL_INGEST_KEY", "")
PAGE = 100
SLEEP = 0.4


def accounts():
    """Each LeafLink account to pull from. Michigan and New Jersey are separate
    logins with separate keys, so they are listed by name; a single unnamed
    account still works the old way."""
    named = [a.strip().upper() for a in os.getenv("LEAFLINK_ACCOUNTS", "").split(",") if a.strip()]
    if named:
        out = []
        for label in named:
            key = os.getenv(f"LEAFLINK_API_KEY_{label}", "")
            if not key:
                print(f"  {label}: no LEAFLINK_API_KEY_{label} set, skipping.")
                continue
            out.append({
                "label": label,
                "key": key,
                "seller": os.getenv(f"LEAFLINK_SELLER_ID_{label}", ""),
                "base": os.getenv(f"LEAFLINK_BASE_{label}",
                                  os.getenv("LEAFLINK_BASE", "https://app.leaflink.com/api/v2")),
            })
        return out

    key = os.getenv("LEAFLINK_API_KEY", "")
    if not key:
        return []
    return [{
        "label": os.getenv("LEAFLINK_LABEL", "LeafLink"),
        "key": key,
        "seller": os.getenv("LEAFLINK_SELLER_ID", ""),
        "base": os.getenv("LEAFLINK_BASE", "https://app.leaflink.com/api/v2"),
    }]


ACCOUNTS = accounts()
if ONLY:
    ACCOUNTS = [a for a in ACCOUNTS if a["label"] == ONLY]
if not ACCOUNTS:
    print("""
  No LeafLink accounts configured.

  For two accounts:
      set LEAFLINK_ACCOUNTS=MI,NJ
      set LEAFLINK_API_KEY_MI=...
      set LEAFLINK_API_KEY_NJ=...

  For one:
      set LEAFLINK_API_KEY=...
""")
    sys.exit(1)

session = requests.Session()
# LeafLink has used two header styles; whichever your scraper sends is the one
# that works here. Both are tried once and the winner is kept.
def HEADERS(key):
    # LeafLink has used several token styles; whichever your scraper sends is
    # the one that works here.
    return [
        {"Authorization": f"App {key}"},
        {"Authorization": f"Token {key}"},
        {"Authorization": f"Bearer {key}"},
    ]


def pick_header(acct):
    """Each account may have been issued a different style of token."""
    for h in HEADERS(acct["key"]):
        try:
            r = session.get(f"{acct['base']}/customers/", headers=h,
                            params={"limit": 1, "include_children": "contacts"}, timeout=60)
        except requests.RequestException as e:
            print(f"  {acct['label']}: could not reach LeafLink ({e})")
            return None
        if r.status_code == 200:
            return h
        if r.status_code not in (401, 403):
            print(f"  {acct['label']}: LeafLink replied {r.status_code} — {r.text[:160]}")
            return None
    print(f"  {acct['label']}: key rejected with every header style. Copy the "
          f"Authorization line from that repo's scraper.py.")
    return None


def fetch(acct):
    head = pick_header(acct)
    if not head:
        return []
    print(f"  {acct['label']}: signed in using {list(head.values())[0].split()[0]}")

    got, offset = [], 0
    while True:
        params = {
            "limit": PAGE,
            "offset": offset,
            # Without this the API returns bare ids for managers and leaves
            # contacts off entirely — which is why an earlier run found
            # 1,720 customers and nobody to call.
            "include_children": "contacts,managers",
        }
        if acct["seller"]:
            params["seller"] = acct["seller"]
        r = session.get(f"{acct['base']}/customers/", headers=head, params=params, timeout=120)
        if r.status_code == 429:
            print(f"  {acct['label']}: rate limited, waiting 10s")
            time.sleep(10)
            continue
        if r.status_code != 200:
            print(f"  {acct['label']}: stopped at offset {offset} ({r.status_code})")
            break
        body = r.json()
        batch = body.get("results", body) if isinstance(body, dict) else body
        if not batch:
            break
        got.extend(batch)
        total = body.get("count") if isinstance(body, dict) else None
        print(f"  {acct['label']}: page {offset // PAGE + 1}, {len(batch)} customers"
              + (f" (of {total})" if total else ""))
        if len(batch) < PAGE:
            break
        offset += PAGE
        time.sleep(SLEEP)
    return got


print()
customers = []
per_account = {}
for acct in ACCOUNTS:
    got = fetch(acct)
    per_account[acct["label"]] = got
    customers.extend(got)

if not customers:
    print("\n  No customers came back from any account.\n")
    sys.exit(0)


BRAND_DOMAINS = [d.strip().lower() for d in
                 os.getenv("BRAND_DOMAINS", "medfarms.com,chillmedicated.com").split(",") if d.strip()]


def ours(email):
    at = str(email or "").split("@")
    return len(at) > 1 and at[1].lower() in BRAND_DOMAINS


def people(c):
    """Everyone at the store named on a customer record.

    `contacts` are the store's people. `managers` are our own reps assigned to
    the account, and `owner` is a seller id — neither belongs in a store's
    contact list, so neither is counted. A preview that promises more people
    than arrive is worse than no preview.
    """
    out = []
    for m in (c.get("contacts") or []):
        if isinstance(m, dict):
            name = (m.get("display_name") or m.get("name") or m.get("full_name")
                    or " ".join(filter(None, [m.get("first_name"), m.get("last_name")])).strip())
            if name or m.get("email"):
                out.append(name or m.get("email"))
    owner = c.get("owner")
    if isinstance(owner, str) and owner and not ours(c.get("owner_email") or c.get("email")):
        out.append(owner)
    return out


with_licence = [c for c in customers if c.get("license_number")]
with_people = [c for c in customers if people(c)]
named = sum(len(people(c)) for c in customers)

for label, got in per_account.items():
    lic = sum(1 for c in got if c.get("license_number"))
    ppl = sum(len(people(c)) for c in got)
    print(f"\n  {label}: {len(got)} customers, {lic} with a licence, {ppl} people")

print(f"""
  {len(customers)} customer(s) across {len(per_account)} account(s)
  {len(with_licence)} have a licence number — only these can be matched
  {len(with_people)} have at least one named person
  {named} people in total
""")
sample = next((c for c in customers if people(c)), customers[0])
print("  First record with people on it:" if people(sample)
      else "  No record had a contact on it. Here is the first one:")
print(json.dumps({k: sample.get(k) for k in
                  ("name", "license_number", "city", "state", "phone", "email",
                   "owner", "managers", "contacts")},
                 indent=2)[:1100])

if not any(people(c) for c in customers):
    print("""
  LeafLink returned no store contacts.

  Either nobody has been added under Contacts on these customer records, or
  this key cannot see them. Open one account in LeafLink and check whether
  Contacts are filled in on the customer — if they are blank there, they are
  blank here, and the addresses and phone numbers below are still worth
  importing on their own.
""")

if not GO:
    print("\n  Nothing sent. Re-run with --go once that looks right.\n")
    sys.exit(0)
if not INGEST:
    print("\n  Set CHILL_INGEST_KEY before sending.\n")
    sys.exit(1)

sent = matched = created = added = skipped = 0
for i in range(0, len(customers), 400):
    chunk = customers[i:i + 400]
    try:
        r = requests.post(
            f"{DESK}/api/ingest/stores",
            json={"customers": chunk},
            headers={"X-Ingest-Key": INGEST, "Content-Type": "application/json"},
            timeout=300,
        )
    except requests.RequestException as e:
        print(f"  batch {i // 400 + 1}: could not reach Chill Desk ({e})")
        continue
    if r.status_code == 401:
        print("\n  Ingest key rejected. Generate a new one in Partner sales.\n")
        sys.exit(1)
    if r.status_code != 200:
        print(f"  batch {i // 400 + 1}: {r.status_code} — {r.text[:200]}")
        continue
    out = r.json()
    sent += len(chunk)
    matched += out.get("matched", 0)
    created += out.get("created", 0)
    added += out.get("contacts", 0)
    skipped += out.get("skipped", 0)
    print(f"  batch {i // 400 + 1}: {out.get('contacts', 0)} contacts added")

print(f"""
  {sent} customer(s) sent
  {matched} existing store(s) filled in
  {created} new store record(s)
  {added} contact(s) added
  {skipped} had no licence number and were left out
""")
