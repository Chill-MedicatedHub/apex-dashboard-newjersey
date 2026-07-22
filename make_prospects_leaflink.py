"""
make_prospects_leaflink.py — build prospects.json for the Prospecting tab
(New / potential buyers), sourced from LeafLink.

Runs next to the LeafLink scraper.py and uses the SAME credentials (reads
LEAFLINK_API_KEY from .env). It pulls your LeafLink customer directory
(/api/v2/customers/) — every retailer connected to your storefront, WITH their
state license number — and writes prospects.json.

The dashboard then compares that list against your sales book by license number
and shows only the retailers you have NOT yet invoiced. Because the customer
directory includes accounts that are connected but have never ordered, this
surfaces warm prospects (already on LeafLink, already linked to you, no order
yet) — the most actionable kind.

Usage:
    python make_prospects_leaflink.py            # writes prospects.json here
    python make_prospects_leaflink.py out.json   # custom output path

Requires (already present for the scraper): a .env file with LEAFLINK_API_KEY.
"""

import json, os, sys, time
from datetime import datetime, timezone
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv()

# Auth auto-detects which LeafLink scheme this repo's scraper uses:
#   - Michigan: an App key   -> LEAFLINK_API_KEY, host www.leaflink.com, "App <key>"
#   - New Jersey: a Token    -> LEAFLINK_TOKEN,   host app.leaflink.com, "Token <key>"
API_KEY = os.getenv("LEAFLINK_API_KEY", "").strip()
TOKEN   = os.getenv("LEAFLINK_TOKEN", "").strip()
if API_KEY:
    AUTH, _DEFAULT_BASE = f"App {API_KEY}", "https://www.leaflink.com"
elif TOKEN:
    AUTH, _DEFAULT_BASE = f"Token {TOKEN}", "https://app.leaflink.com"
else:
    AUTH, _DEFAULT_BASE = None, "https://www.leaflink.com"

API_BASE   = os.getenv("LEAFLINK_API_BASE", _DEFAULT_BASE)
CUSTOMERS  = os.getenv("LEAFLINK_CUSTOMERS_ENDPOINT", "/api/v2/customers/")
STATE      = os.getenv("LEAFLINK_STATE", "MI").strip().upper()   # keep this state (blank = keep all)
PAGE_SIZE  = int(os.getenv("LEAFLINK_PAGE_SIZE", "500"))
OUT        = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).parent / "prospects.json"


def headers():
    return {"Authorization": AUTH, "Accept": "application/json",
            "User-Agent": "chill-prospects"}


def _get(url, params):
    last = None
    for attempt in range(6):
        try:
            r = requests.get(url, headers=headers(), params=params, timeout=120)
        except requests.RequestException as e:
            last = e; time.sleep(5 * (attempt + 1)); continue
        if r.status_code == 429 or 500 <= r.status_code < 600:
            time.sleep(5 * (attempt + 1)); last = r; continue
        return r
    if isinstance(last, requests.Response):
        return last
    raise RuntimeError(f"request failed after retries: {last}")


# --- field extraction (mirrors scraper.py so licenses match the sales feed) ---
def _first(d, *keys):
    for k in keys:
        if isinstance(d, dict) and d.get(k) not in (None, "", []):
            return d.get(k)
    return None

def _name_of(v):
    if isinstance(v, dict):
        return _first(v, "name", "display_name", "company_name", "title", "full_name") or ""
    return v.strip() if isinstance(v, str) else ""

def _sub_field(c, keys, subs=("buyer", "company", "address", "billing_address",
                              "shipping_address", "default_address", "location",
                              "delivery_address", "corporate_address")):
    for k in keys:
        v = c.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
    for sk in subs:
        sub = c.get(sk)
        if isinstance(sub, dict):
            for k in keys:
                v = sub.get(k)
                if isinstance(v, str) and v.strip():
                    return v.strip()
    return ""

def _license_of(c): return _sub_field(c, ("license", "license_number", "license_no"))
def _state_of(c):   return _sub_field(c, ("state", "state_code", "region"))
def _city_of(c):    return _sub_field(c, ("city",))

def _name_full(c):
    return (_name_of(c) or _name_of(c.get("buyer")) or _name_of(c.get("company"))
            or _first(c, "display_name", "name", "company_name") or "")


def fetch_customers():
    if not AUTH:
        sys.exit("ERROR: no LeafLink credential found. Set LEAFLINK_API_KEY (Michigan) "
                 "or LEAFLINK_TOKEN (New Jersey) — the same one the scraper uses.")
    url = f"{API_BASE}{CUSTOMERS}"
    resp = _get(url, {"page_size": PAGE_SIZE, "limit": PAGE_SIZE, "page": 1})
    if resp.status_code == 401:
        sys.exit("ERROR: 401 Unauthorized — LeafLink key missing/invalid/revoked.")
    if resp.status_code == 403:
        sys.exit("ERROR: 403 Forbidden — the App token lacks 'Customers' read permission.\n"
                 "Enable it in LeafLink → Settings → Applications, then rerun.")
    if resp.status_code != 200:
        sys.exit(f"ERROR: customers endpoint returned {resp.status_code}\n{resp.text[:300]}")
    out = []
    while True:
        data = resp.json()
        out.extend(data.get("results", data if isinstance(data, list) else []))
        nxt = data.get("next") if isinstance(data, dict) else None
        if not nxt:
            break
        resp = _get(nxt, None)
        if resp.status_code != 200:
            break
    return out


def main():
    print(f"Pulling customer directory from {CUSTOMERS} ...")
    customers = fetch_customers()
    print(f"  {len(customers)} customers returned.")

    seen, records = set(), []
    kept_no_state = 0
    for c in customers:
        if not isinstance(c, dict):
            continue
        lic = _license_of(c)
        if not lic:
            continue                      # need a license to match the sales book
        st = _state_of(c).upper()
        if STATE and st and st != STATE:
            continue                      # wrong state
        if STATE and not st:
            kept_no_state += 1            # no state on record — keep (token is state-scoped)
        key = lic.strip().upper()
        if key in seen:
            continue
        seen.add(key)
        records.append({
            "name": _name_full(c) or "—",
            "license": key,
            "type": "",                   # left blank so the dashboard keeps all
            "status": "",                 # (its filter only excludes non-active/non-retail)
            "city": _city_of(c),
            "state": st or STATE,
        })

    records.sort(key=lambda x: (x["city"], x["name"]))
    payload = {
        "as_of": datetime.now(timezone.utc).date().isoformat(),
        "source": "LeafLink customer directory (/api/v2/customers/)",
        "state": STATE,
        "count": len(records),
        "records": records,
    }
    OUT.write_text(json.dumps(payload, indent=2))
    print(f"Wrote {OUT}: {len(records)} licensed customers "
          f"({kept_no_state} had no state on record and were kept).")
    print("The dashboard will drop the ones you've already invoiced and show the rest "
          "as New / potential buyers.")


if __name__ == "__main__":
    main()
