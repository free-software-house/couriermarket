# -*- coding: utf-8 -*-
"""
courier_ratings.py
Τρέχει αυτόματα (ή χειροκίνητα), φέρνει Google Places data για 6 brands,
χτίζει history.json και report.html, κάνει git push στο GitHub Pages.

Ρυθμίσεις: βλ. CONFIG παρακάτω.
"""

import os, time, json, re as _re, subprocess, unicodedata
from datetime import datetime, timezone
from functools import lru_cache
from typing import Dict, List, Any, Optional

import requests
import pandas as pd

# ─────────────────────────── CONFIG ───────────────────────────────────────
API_KEY     = os.environ.get("PLACES_API_KEY", "")  # Βάλε το key στο GitHub Secret: PLACES_API_KEY
REPO_DIR    = os.path.dirname(os.path.abspath(__file__))   # same dir as this script
HISTORY_FILE = os.path.join(REPO_DIR, "history.json")
REPORT_FILE  = os.path.join(REPO_DIR, "index.html")

RATE_SLEEP   = 0.3
HTTP_TIMEOUT = 25
SEARCH_RADIUS_M = 50000

COMMIT_MSG   = "auto: update ratings {date}"
GIT_PUSH     = False  # Το push γίνεται από το GitHub Actions workflow

# ─────────────────────────── BRANDS & QUERIES ─────────────────────────────
BRANDS: Dict[str, List[str]] = {
    "ACS":                  ["Κατάστημα ACS", "ACS Courier"],
    "Γενική Ταχυδρομική":   ["Κατάστημα Γενική Ταχυδρομική", "Γενική Ταχυδρομική"],
    "ΕΛΤΑ Courier":         ["Κατάστημα ΕΛΤΑ Courier", "Κατάστημα ELTA Courier", "ΕΛΤΑ Courier"],
    "SPEEDEX":              ["Κατάστημα SPEEDEX", "SPEEDEX Courier"],
    "Courier Center":       ["Κατάστημα Courier Center", "Courier Center"],
    "EASYMAIL":             ["Κατάστημα easymail", "Easymail"],
}

GREECE_CENTERS = [
    (37.9838,23.7275),(38.3250,23.3187),(38.4353,22.8764),(38.4371,22.4318),
    (38.9006,22.4338),(38.9182,22.6159),(39.0003,21.7931),(39.3623,22.9427),
    (39.1825,22.7596),(39.6390,22.4196),(39.8897,22.1870),(39.2926,22.3849),
    (39.5550,21.7679),(39.3656,21.9214),(40.2697,22.5061),(40.6401,22.9444),
    (40.5470,23.0213),(40.6757,22.8352),(40.6117,22.9780),(40.5897,22.9507),
    (40.6680,22.9301),(40.6902,22.9004),(40.7868,22.5807),(40.7481,23.0656),
    (40.2414,23.2843),(40.3809,23.4413),(40.2029,23.6645),(40.3953,23.8856),
    (40.5233,22.2033),(40.6293,22.0692),(40.9937,22.8743),(40.0833,21.4275),
    (40.3006,21.7896),(40.5143,21.6786),(40.5200,21.2687),(40.7880,22.4070),
    (40.7858,22.3148),(41.0903,23.5414),(41.1495,24.1474),(40.9396,24.4018),
    (41.1343,24.8877),(41.1169,25.4040),(40.8470,25.8744),(41.5048,26.5297),
    (38.2466,21.7346),(38.2305,21.7371),(38.2523,22.0819),(37.6753,21.4374),
    (37.9381,22.9320),(38.0146,22.7496),(37.5674,22.8069),(37.0738,22.4297),
    (37.5108,22.3735),(37.0387,22.1142),(37.7951,21.3507),(38.3911,21.8277),
    (38.3714,21.4315),(38.6218,21.4074),(39.1585,20.9877),(38.9559,20.7505),
    (35.3387,25.1442),(35.0510,25.7463),(35.1280,25.7308),(35.3655,24.4820),
    (35.4748,23.8044),(35.0514,25.0787),(39.6239,19.9217),(36.4349,28.2176),
    (37.0850,25.1500),(36.3932,25.4615),(37.5379,25.1634),(38.3687,26.1359),
    (36.8928,27.2877),(39.1070,26.5550),(39.6650,20.8537),(36.1400,29.5900),
    # ==== ΝΟΤΙΑ & ΑΝΑΤΟΛΙΚΗ ΑΤΤΙΚΗ ====
    (37.7200,23.9100),   # Παλαιά Φώκαια / Σαρωνικός
    (37.6500,23.8500),   # Λαύριο
    (37.8800,24.0200),   # Μαρκόπουλο
    (37.9500,23.9500),   # Κορωπί / Παιανία
    (38.0500,23.8800),   # Γέρακας / Παλλήνη ανατολικά
]

FIELDS = ["places.name","places.displayName","places.formattedAddress",
          "places.googleMapsUri","places.rating","places.userRatingCount",
          "places.types","places.id","places.location"]

SEARCH_URL   = "https://places.googleapis.com/v1/places:searchText"
DETAILS_BASE = "https://places.googleapis.com/v1/"
SESSION = requests.Session()

# ─────────────────────────── UNICODE HELPERS ──────────────────────────────
def _strip_accents(s):
    return "".join(c for c in unicodedata.normalize("NFD", s or "")
                   if unicodedata.category(c) != "Mn")

def _norm(s):
    return _re.sub(r"\s+", " ", _strip_accents(s or "").lower()).strip()

def _norm_np(s):
    return _re.sub(r"[^\w\s]", " ", _norm(s))

# ─────────────────────────── BRAND RULES ──────────────────────────────────
KEYWORDS_NORM: Dict[str, List[str]] = {
    "ACS":                ["acs"],
    "Courier Center":     ["courier center"],
    "EASYMAIL":           ["easymail", "easy mail", "easy-mail"],
    "SPEEDEX":            ["speedex", "speed ex"],
    "Γενική Ταχυδρομική": ["γενικη ταχυδρομικη","γενικη","ταχυδρομικη",
                           "geniki tachydromiki","geniki","tachydromiki"],
    "ΕΛΤΑ Courier":       ["ελτα courier","elta courier","ελτα κουριερ",
                           "ταχυμεταφορες ελτα","ελτα","elta"],
}

REASSIGN_ALIASES: Dict[str, List[str]] = {
    "SPEEDEX":            ["speedex","speed ex"],
    "ACS":                ["acs","acs courier"],
    "Γενική Ταχυδρομική": ["γενικη ταχυδρομικη","geniki tachydromiki"],
    "ΕΛΤΑ Courier":       ["ελτα","ελτα courier","elta","elta courier"],
    "Courier Center":     ["courier center"],
    "EASYMAIL":           ["easymail","easy mail"],
}

BLACKLIST = [
    "city courier","city express","general courier","sports center","dpd",
    "smartpoint","smart point","dhl","hub","clever point","ups","artcourier",
    "kritiki tahidromiki","ελτα πρακτορειο","ελτα -","ελληνικα ταχυδρομεια",
    "hellenic post","ταχυδρομικο πρακτορειο","ταχυδρομειο","postal agency",
    "ταχυδρομικο ταμιευτηριο","box express","icc","taxydema",
]
_BL_RE = _re.compile("|".join(_re.escape(t) for t in BLACKLIST), _re.I)

# ─────────────────────────── REGION INFERENCE ────────────────────────────
_ISLAND_KW = {
    "ΚΡΗΤΗ": ["κρητη","heraklion","irakleio","chania","rethymno",
               "agios nikolaos","ierapetra","moires"],
    "ΝΗΣΙΑ_ΔΥΤΙΚΑ": ["κερκυρα","corfu","kerkira","kerkyra"],
    "ΝΗΣΙΑ_ΑΛΛΑ":   ["ροδο","rhodes","παρος","paros","σαντορινη","thira",
                     "θηρα","τηνος","tinos","χιος","chios","κως","kos",
                     "μυτιληνη","lesvos","λεσβος","μυκονος","mykonos",
                     "ναξος","naxos","σαμος","samos","ικαρια","ikaria"],
}

def _bbox(lat, lng, r0, r1, c0, c1):
    return lat is not None and lng is not None and r0 <= lat <= r1 and c0 <= lng <= c1

def infer_region(name, addr, lat, lng):
    if lat is None or lng is None:
        return "Κεντρική Ελλάδα"
    # Κρήτη
    if 34.7 <= lat <= 35.9 and 23.3 <= lng <= 26.7: return "ΚΡΗΤΗ"
    # Δωδεκάνησα
    if 35.8 <= lat <= 37.0 and 26.8 <= lng <= 29.7: return "Υπόλοιπα νησιά"
    # Κυκλάδες
    if 36.3 <= lat <= 37.9 and 24.3 <= lng <= 26.5: return "Υπόλοιπα νησιά"
    # Χίος/Σάμος/Ικαρία
    if 37.4 <= lat <= 38.7 and 25.8 <= lng <= 27.2: return "Υπόλοιπα νησιά"
    # Λέσβος/Λήμνος/Θάσος/Σαμοθράκη
    if 38.8 <= lat <= 40.8 and 25.0 <= lng <= 26.5: return "Υπόλοιπα νησιά"
    # Σαρωνικά νησιά
    if 37.1 <= lat <= 37.9 and 23.0 <= lng <= 23.6: return "Υπόλοιπα νησιά"
    # Ιόνια νησιά εκτός Κέρκυρας
    if 37.5 <= lat <= 38.9 and 20.3 <= lng <= 21.1: return "Υπόλοιπα νησιά"
    # Κέρκυρα
    if 39.3 <= lat <= 39.9 and 19.5 <= lng <= 20.3: return "Δυτική Ελλάδα (με Κέρκυρα)"
    # Αττική
    if 37.7 <= lat <= 38.25 and 23.2 <= lng <= 24.2: return "ΑΤΤΙΚΗ"
    # Θεσσαλονίκη
    if 40.4 <= lat <= 40.85 and 22.6 <= lng <= 23.3: return "Θεσσαλονίκη"
    # Βόρεια Ελλάδα
    if lat >= 40.0: return "Βόρεια Ελλάδα"
    # Πάτρα/Αχαΐα/Αίγιο (πριν Ναύπακτο)
    if 37.9 <= lat <= 38.35 and 21.6 <= lng <= 22.3: return "Πελοπόννησος"
    # Ναύπακτος/Μεσολόγγι/Αγρίνιο (Δυτική)
    if 38.2 <= lat <= 38.65 and 21.0 <= lng <= 21.9: return "Δυτική Ελλάδα (με Κέρκυρα)"
    # Κεντρική Ελλάδα: Τρίκαλα, Καρδίτσα, Λάρισα, Βόλος, Λαμία, Χαλκίδα
    if 38.3 <= lat <= 39.9 and 21.5 <= lng <= 24.5: return "Κεντρική Ελλάδα"
    # Πελοπόννησος (νότια)
    if lat <= 38.1 and 21.5 <= lng <= 23.2: return "Πελοπόννησος"
    # Δυτική Ελλάδα (Ήπειρος κλπ)
    if lng <= 22.5 and lat <= 40.0: return "Δυτική Ελλάδα (με Κέρκυρα)"
    return "Κεντρική Ελλάδα"

REGION_ORDER = ["ΑΤΤΙΚΗ","Κεντρική Ελλάδα","Θεσσαλονίκη","Βόρεια Ελλάδα",
                "Δυτική Ελλάδα (με Κέρκυρα)","Πελοπόννησος","ΚΡΗΤΗ","Υπόλοιπα νησιά"]

def _headers():
    return {"Content-Type":"application/json",
            "X-Goog-Api-Key": API_KEY,
            "X-Goog-FieldMask": ",".join(FIELDS)}

def _det_headers():
    return {"Content-Type":"application/json",
            "X-Goog-Api-Key": API_KEY,
            "X-Goog-FieldMask": "rating,userRatingCount,location,formattedAddress,googleMapsUri,displayName"}

def search_text(query):
    results = []
    for clat, clng in GREECE_CENTERS:
        payload = {"textQuery": query, "languageCode": "el", "regionCode": "GR",
                   "locationBias": {"circle": {"center": {"latitude": clat, "longitude": clng},
                                               "radius": SEARCH_RADIUS_M}}}
        r = SESSION.post(SEARCH_URL, headers=_headers(), json=payload, timeout=HTTP_TIMEOUT)
        if r.status_code == 200:
            results.extend(r.json().get("places", []))
        time.sleep(RATE_SLEEP)
    return results

@lru_cache(maxsize=4096)
def fetch_details(resource_name):
    r = SESSION.get(DETAILS_BASE + resource_name, headers=_det_headers(), timeout=HTTP_TIMEOUT)
    return r.json() if r.status_code == 200 else {}

# ─────────────────────────── DATA COLLECTION ──────────────────────────────
def collect():
    rows, seen = [], set()
    for brand, queries in BRANDS.items():
        brand_start = len(rows)
        for q in queries:
            try:
                places = search_text(q)
            except Exception as e:
                print(f"[WARN] {brand} / '{q}': {e}"); continue
            for p in places:
                rid = p.get("name") or p.get("id")
                if not rid or rid in seen: continue
                seen.add(rid)
                det = fetch_details(p["name"]) if not p.get("rating") else {}
                loc = (p.get("location") or det.get("location") or {})
                rows.append({
                    "brand": brand,
                    "place_name": (p.get("displayName") or {}).get("text"),
                    "address": p.get("formattedAddress") or det.get("formattedAddress"),
                    "rating": p.get("rating") or det.get("rating"),
                    "user_rating_count": p.get("userRatingCount") or det.get("userRatingCount"),
                    "maps_url": p.get("googleMapsUri") or det.get("googleMapsUri"),
                    "lat": loc.get("latitude"),
                    "lng": loc.get("longitude"),
                })
        print(f"[INFO] {brand}: {len(rows)-brand_start} places")
    return pd.DataFrame(rows)

# ─────────────────────────── CLEANING ─────────────────────────────────────
def clean(df):
    # Blacklist
    bl = df["place_name"].fillna("").str.contains(_BL_RE, na=False)
    if bl.sum(): print(f"[CLEAN] Blacklist removed: {bl.sum()}")
    df = df[~bl].copy()

    # Reassign
    changed = 0
    for idx, row in df.iterrows():
        txt = _norm(f"{row.get('place_name','')} {row.get('address','')}")
        for canonical, aliases in REASSIGN_ALIASES.items():
            if any(a and a in txt for a in aliases):
                if canonical != row["brand"]:
                    df.at[idx, "brand"] = canonical; changed += 1
                break
    if changed: print(f"[REASSIGN] Changed: {changed}")

    # Enforce brand⇒place_name rule
    def ok(row):
        b = str(row.get("brand",""))
        txt = _norm_np(str(row.get("place_name","")))
        kws = KEYWORDS_NORM.get(b, [])
        return any(kw in txt for kw in kws) if kws else True
    mask = df.apply(ok, axis=1)
    if (~mask).sum(): print(f"[FILTER] Brand-name rule removed: {(~mask).sum()}")
    df = df[mask].copy()

    # Dedup
    def make_key(row):
        url = (row.get("maps_url") or "").strip()
        if url: return ("URL", url)
        return ("PNAD", f"{_norm(str(row.get('place_name','')))}|{_norm(str(row.get('address','')))}") 
    chosen = {}
    for _, row in df.iterrows():
        key = make_key(row)
        cur = chosen.get(key)
        if cur is None: chosen[key] = row; continue
        cur_urc  = float(cur.get("user_rating_count") or 0)
        row_urc  = float(row.get("user_rating_count") or 0)
        if row_urc > cur_urc: chosen[key] = row
    before = len(df)
    df = pd.DataFrame(list(chosen.values())).reset_index(drop=True)
    if before - len(df): print(f"[DEDUP] Removed: {before-len(df)}")
    return df

# ─────────────────────────── SUMMARISE ────────────────────────────────────
def summarize(df):
    out = {}
    for brand, g in df.groupby("brand"):
        g = g.dropna(subset=["rating"]).copy()
        r   = pd.to_numeric(g["rating"],            errors="coerce").dropna()
        urc = pd.to_numeric(g.loc[r.index,"user_rating_count"], errors="coerce").fillna(0)
        total = int(urc.sum())
        out[brand] = {
            "weighted_avg":  round(float((r*urc).sum()/total), 2) if total else None,
            "simple_avg":    round(float(r.mean()), 2) if len(r) else None,
            "total_reviews": total,
            "store_count":   len(g),
        }
    return out

def summarize_regions(df):
    df = df.copy()
    df["region"] = df.apply(
        lambda r: infer_region(str(r.get("place_name","")), str(r.get("address","")),
                               r.get("lat"), r.get("lng")), axis=1)
    out = {}
    for (region, brand), g in df.groupby(["region","brand"]):
        g = g.dropna(subset=["rating"]).copy()
        r   = pd.to_numeric(g["rating"],            errors="coerce").dropna()
        urc = pd.to_numeric(g.loc[r.index,"user_rating_count"], errors="coerce").fillna(0)
        total = int(urc.sum())
        key = f"{region}||{brand}"
        out[key] = {
            "region": region, "brand": brand,
            "weighted_avg":  round(float((r*urc).sum()/total), 2) if total else None,
            "total_reviews": total,
            "store_count":   len(g),
        }
    return out

def places_list(df):
    rows = []
    for _, r in df.dropna(subset=["rating"]).iterrows():
        rows.append({
            "brand":      str(r.get("brand","")),
            "place_name": str(r.get("place_name","")),
            "address":    str(r.get("address","")),
            "rating":     round(float(r["rating"]),1),
            "reviews":    int(r.get("user_rating_count") or 0),
            "maps_url":   str(r.get("maps_url","")),
            "lat":        float(r["lat"]) if pd.notna(r.get("lat")) else None,
            "lng":        float(r["lng"]) if pd.notna(r.get("lng")) else None,
        })
    return rows

# ─────────────────────────── HISTORY ──────────────────────────────────────
def load_history():
    if os.path.exists(HISTORY_FILE):
        with open(HISTORY_FILE, encoding="utf-8") as f:
            return json.load(f)
    return {"snapshots": []}

def save_history(h):
    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(h, f, ensure_ascii=False, indent=2)

def append_snapshot(history, date_str, label, summary, regions, places):
    from datetime import date as _date
    MIN_DAYS = 6

    new_snap = {
        "date": date_str, "label": label,
        "summary": summary, "regions": regions, "places": places,
    }

    if not history["snapshots"]:
        history["snapshots"].append(new_snap)
        return history

    # Always replace any existing snapshot for the same date
    same_date = [s for s in history["snapshots"] if s["date"] == date_str]
    if same_date:
        history["snapshots"] = [s for s in history["snapshots"] if s["date"] != date_str]
        history["snapshots"].append(new_snap)
        history["snapshots"].sort(key=lambda s: s["date"])
        print(f"[HISTORY] Replaced same-day snapshot {date_str}")
        return history

    # Find latest snapshot (excluding same date)
    latest_date = max(s["date"] for s in history["snapshots"])
    d1 = _date.fromisoformat(latest_date)
    d2 = _date.fromisoformat(date_str)
    diff = (d2 - d1).days
    latest_is_sunday = d1.weekday() == 6

    if diff < MIN_DAYS and not latest_is_sunday:
        # Replace latest with newer
        history["snapshots"] = [s for s in history["snapshots"] if s["date"] != latest_date]
        history["snapshots"].append(new_snap)
        print(f"[HISTORY] Replaced {latest_date} → {date_str} ({diff} days, under {MIN_DAYS})")
    else:
        history["snapshots"].append(new_snap)
        print(f"[HISTORY] Added {date_str} ({diff} days since {latest_date})")

    history["snapshots"].sort(key=lambda s: s["date"])
    return history

def build_html(history):
    """Builds a fully self-contained HTML report from history.json"""
    history_json = json.dumps(history, ensure_ascii=False)
    snaps = history["snapshots"]
    latest = snaps[-1]
    prev   = snaps[-2] if len(snaps) >= 2 else None
    now_label = latest["label"]

    html = f"""<!DOCTYPE html>
<html lang="el">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Courier Ratings Dashboard</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.2/dist/chart.umd.min.js"></script>
<style>
  *{{box-sizing:border-box;margin:0;padding:0}}
  body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#f4f6f9;color:#1a1a2e;font-size:14px}}
  .topbar{{background:#1a1a2e;color:#fff;padding:14px 24px;display:flex;align-items:center;justify-content:space-between}}
  .topbar h1{{font-size:17px;font-weight:600;letter-spacing:.3px}}
  .topbar .ts{{font-size:12px;color:#aab;margin-top:2px}}
  .container{{max-width:1200px;margin:0 auto;padding:20px 16px}}
  h2{{font-size:15px;font-weight:600;margin:24px 0 12px;color:#1a1a2e;padding-left:4px;border-left:3px solid #3b82f6}}
  /* Summary cards */
  .cards{{display:grid;grid-template-columns:repeat(auto-fit,minmax(175px,1fr));gap:12px;margin-bottom:8px}}
  .card{{background:#fff;border-radius:10px;padding:14px 16px;box-shadow:0 1px 4px rgba(0,0,0,.08);position:relative}}
  .card .brand{{font-size:12px;color:#666;font-weight:500;margin-bottom:4px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}}
  .card .score{{font-size:28px;font-weight:700;line-height:1}}
  .card .meta{{font-size:11px;color:#999;margin-top:4px}}
  .card .delta{{position:absolute;top:14px;right:14px;font-size:12px;font-weight:600;padding:2px 6px;border-radius:20px}}
  .up{{background:#dcfce7;color:#166534}} .dn{{background:#fee2e2;color:#991b1b}} .nc{{background:#f1f5f9;color:#64748b}}
  .cc{{border-top:3px solid #3b82f6}}
  /* Trend chart */
  .chart-wrap{{background:#fff;border-radius:10px;padding:16px;box-shadow:0 1px 4px rgba(0,0,0,.08);margin-bottom:8px}}
  .chart-wrap canvas{{max-height:280px}}
  /* Regions table */
  .tbl-wrap{{background:#fff;border-radius:10px;box-shadow:0 1px 4px rgba(0,0,0,.08);margin-bottom:8px}}
  table{{width:100%;border-collapse:collapse;font-size:13px}}
  thead th{{background:#1a1a2e;color:#fff;padding:9px 12px;text-align:left;font-weight:500;white-space:nowrap;position:sticky;top:0;z-index:10;box-shadow:0 1px 0 rgba(255,255,255,.1)}}
  tbody tr:hover{{background:#f8fafc}}
  tbody td{{padding:8px 12px;border-bottom:.5px solid #e8ecf0}}
  .region-hdr{{background:#f1f5f9!important;font-weight:600;color:#334155}}
  .brand-cc{{font-weight:700;color:#1d4ed8}}
  .pill{{display:inline-block;padding:2px 7px;border-radius:20px;font-size:11px;font-weight:600}}
  .g4{{background:#dcfce7;color:#166534}} .g35{{background:#d1fae5;color:#065f46}}
  .y3{{background:#fef9c3;color:#854d0e}} .r2{{background:#fee2e2;color:#991b1b}}

  /* Stores table */
  .filter-bar{{display:flex;gap:8px;margin-bottom:10px;flex-wrap:wrap}}
  .filter-bar select,.filter-bar input{{padding:6px 10px;border:.5px solid #d1d5db;border-radius:6px;font-size:13px;background:#fff}}
  .filter-bar input{{flex:1;min-width:150px}}
  @media(max-width:640px){{.movers{{grid-template-columns:1fr}}.cards{{grid-template-columns:repeat(2,1fr)}}}}
</style>
</head>
<body>
<div class="topbar">
  <div>
    <div class="ts">Courier Ratings Dashboard</div>
    <div class="ts" style="font-size:13px;font-weight:600;margin-top:2px" id="topbar-updated">Τελευταία ενημέρωση: {now_label}</div>
  </div>
  <div style="display:flex;align-items:center;gap:12px">
    <div class="ts" id="run-ts"></div>
    <button id="lang-btn" onclick="toggleLang()" style="padding:5px 16px;border-radius:20px;border:1.5px solid rgba(255,255,255,0.4);background:transparent;color:#fff;font-size:12px;font-weight:700;cursor:pointer;letter-spacing:.5px">EN</button>
  </div>
</div>

<div class="container">

  <!-- Summary cards -->
  <h2 id="h-summary">Συνολική Αξιολόγηση (Weighted Average)</h2>
  <div class="cards" id="summary-cards"></div>

  <!-- Trend chart -->
  <h2 id="h-trend">Τάση Weighted Average (Πανελλαδικά)</h2>
  <div class="chart-wrap">
    <div id="brand-filter" style="display:flex;flex-wrap:wrap;gap:6px;margin-bottom:12px"></div>
    <canvas id="trend-chart"></canvas>
  </div>

  <!-- Regions table -->
  <h2 id="h-regions">Αποτελέσματα ανά Περιοχή</h2>
  <div class="tbl-wrap"><table id="region-table">
    <thead><tr>
      <th id="th-region">Περιοχή</th><th id="th-company">Εταιρία</th>
      <th>Weighted Avg</th><th id="th-reviews">Κριτικές</th><th id="th-stores-col">Καταστήματα</th>
      <th id="th-vs-prev">vs {(prev or {}).get('label','προηγ.')}</th>
      <th id="th-vs-first">vs {snaps[0]['label'] if snaps else '01/01/2026'}</th>
    </tr></thead>
    <tbody id="region-tbody"></tbody>
  </table></div>



  <!-- All stores -->
  <h2 id="h-stores">Όλα τα Καταστήματα</h2>
  <div class="filter-bar">
    <select id="f-brand"><option value="" id="opt-all-brands">Όλες οι εταιρίες</option></select>
    <select id="f-region"><option value="" id="opt-all-regions">Όλες οι περιοχές</option></select>
    <input id="f-search" placeholder="Αναζήτηση ονόματος / διεύθυνσης…" data-placeholder-el="Αναζήτηση ονόματος / διεύθυνσης…" data-placeholder-en="Search by name / address…">
    <select id="f-pagesize" style="min-width:80px">
      <option value="50" selected>50</option>
      <option value="100">100</option>
      <option value="200">200</option>
      <option value="999999">Όλα</option>
    </select>
  </div>
  <div id="pagination" style="display:flex;align-items:center;gap:8px;margin-bottom:8px;font-size:13px;color:var(--color-text-secondary,#64748b)"></div>
  <div class="tbl-wrap"><table>
    <thead><tr>
      <th class="sortable" data-col="brand" style="cursor:pointer" id="th-s-company">Εταιρία <span class="sort-icon">↕</span></th>
      <th id="th-address">Διεύθυνση</th>
      <th class="sortable" data-col="rating" style="cursor:pointer">Rating <span class="sort-icon">↕</span></th>
      <th class="sortable" data-col="reviews" style="cursor:pointer" id="th-s-reviews">Κριτικές <span class="sort-icon">↕</span></th>
      <th id="th-s-vs-prev">Δ vs {(prev or {}).get('label','προηγ.')}</th>
      <th id="th-s-vs-first">Δ vs {snaps[0]['label'] if snaps else '01/01/2026'}</th><th></th>
    </tr></thead>
    <tbody id="stores-tbody"></tbody>
  </table></div>

</div>

<script>
const HISTORY = {history_json};
const REGION_ORDER = {json.dumps(REGION_ORDER)};
const BRAND_COLORS = {{
  "Courier Center":     "#F47920",
  "ACS":                "#CC0000",
  "EASYMAIL":           "#7B2D8B",
  "ΕΛΤΑ Courier":       "#C8102E",
  "SPEEDEX":            "#00A650",
  "Γενική Ταχυδρομική": "#003DA5",
}};
const BRAND_LOGOS = {{
  "Courier Center":     "logos/courier_center.png",
  "ACS":                "logos/acs.png",
  "EASYMAIL":           "logos/easymail.png",
  "ΕΛΤΑ Courier":       "logos/elta_courier.png",
  "SPEEDEX":            "logos/speedex.png",
  "Γενική Ταχυδρομική": "logos/geniki.png",
}};

const snaps   = HISTORY.snapshots;
const latest  = snaps[snaps.length-1];
const prev    = snaps.length >= 2 ? snaps[snaps.length-2] : null;
const first   = snaps[0];
const allBrands = Object.keys(BRAND_COLORS).sort((a,b) => {{
  const va = (latest && latest.summary[a]) ? (latest.summary[a].weighted_avg || 0) : 0;
  const vb = (latest && latest.summary[b]) ? (latest.summary[b].weighted_avg || 0) : 0;
  return vb - va;
}});

// ── Utility ────────────────────────────────────────────────────────────
function ratingClass(r){{
  if(r===null||r===undefined) return '';
  if(r >= 4.0) return 'g4';
  if(r >= 3.5) return 'g35';
  if(r >= 3.0) return 'y3';
  return 'r2';
}}
function deltaHtml(cur, prv){{
  if(prv===null||prv===undefined||cur===null||cur===undefined) return '<span class="pill nc">—</span>';
  const d = (cur-prv);
  const cls = d>0.005?'up':d<-0.005?'dn':'nc';
  const sign = d>0.005?'+':'';
  return `<span class="pill ${{cls}}">${{sign}}${{d.toFixed(2)}}</span>`;
}}

// ── Summary cards ──────────────────────────────────────────────────────
// Count stores per brand from latest places
const storeCounts = {{}};
allBrands.forEach(b=>{{ storeCounts[b] = (latest.places||[]).filter(p=>p.brand===b).length; }});

const cardsEl = document.getElementById('summary-cards');
allBrands.forEach(b=>{{
  const cur = latest.summary[b];
  if(!cur) return;
  const prv = prev && prev.summary[b];
  const d   = (prv && cur) ? cur.weighted_avg - prv.weighted_avg : null;
  const dCls = d===null?'nc':d>0.005?'up':d<-0.005?'dn':'nc';
  const dTxt = d===null?'—':(d>0.005?'+':'')+d.toFixed(2);
  const cc = b==='Courier Center' ? ' cc' : '';
  if(cur) cur.store_count = storeCounts[b] || null;
  const logoHtml = BRAND_LOGOS[b] ? `<img src="${{BRAND_LOGOS[b]}}" style="height:32px;object-fit:contain;margin-bottom:6px;display:block">` : `<div class="brand">${{b}}</div>`;
  cardsEl.innerHTML += `
    <div class="card${{cc}}">
      ${{logoHtml}}
      <div class="score" style="color:${{BRAND_COLORS[b]}}">${{cur.weighted_avg !== null ? cur.weighted_avg.toFixed(2) : '—'}}</div>
      <div class="meta">${{cur.total_reviews ? cur.total_reviews.toLocaleString('el-GR') : '—'}} κριτικές · ${{cur.store_count ? cur.store_count + ' καταστήματα' : '—'}}</div>
      <span class="delta ${{dCls}}">${{dTxt}}</span>
    </div>`;
}});

// ── Trend chart ────────────────────────────────────────────────────────
const labels = snaps.map(s=>s.label);

function makeDatasets(activeBrands){{
  return activeBrands.map(b=>{{
    return {{
      label: b,
      data: snaps.map(s => s.summary[b] ? s.summary[b].weighted_avg : null),
      borderColor: BRAND_COLORS[b],
      backgroundColor: BRAND_COLORS[b]+'33',
      tension: 0.3,
      pointRadius: 4,
      borderWidth: b==='Courier Center' ? 3 : 1.5,
      spanGaps: true,
    }};
  }});
}}

let activeBrands = [...allBrands];
const trendChart = new Chart(document.getElementById('trend-chart'),{{
  type:'line',
  data:{{labels, datasets: makeDatasets(activeBrands)}},
  options:{{
    responsive:true, maintainAspectRatio:true,
    interaction:{{mode:'index', intersect:false}},
    plugins:{{legend:{{display:false}},
              tooltip:{{
                callbacks:{{
                  label:ctx=>`${{ctx.dataset.label}}: ${{ctx.raw !== null ? ctx.raw.toFixed(2) : '—'}}`,
                }},
                itemSort: (a,b) => (b.raw||0) - (a.raw||0),
              }}}},
    scales:{{
      y:{{
        min: (ctx) => {{
          const vals = ctx.chart.data.datasets.flatMap(d=>d.data).filter(v=>v!==null&&v!==undefined);
          return vals.length ? Math.floor((Math.min(...vals)-0.2)*10)/10 : 2.0;
        }},
        max: (ctx) => {{
          const vals = ctx.chart.data.datasets.flatMap(d=>d.data).filter(v=>v!==null&&v!==undefined);
          return vals.length ? Math.ceil((Math.max(...vals)+0.2)*10)/10 : 5.0;
        }},
        ticks:{{stepSize:0.1, font:{{size:11}}}},
      }},
      x:{{ticks:{{font:{{size:11}}}}}}
    }}
  }}
}});

// Build toggle buttons
const filterEl = document.getElementById('brand-filter');

// "Όλα" button
const allBtn = document.createElement('button');
allBtn.textContent = 'Όλα';
allBtn.style.cssText = 'padding:4px 12px;border-radius:20px;border:1.5px solid #ccc;background:#1a1a2e;color:#fff;font-size:12px;cursor:pointer;font-weight:600';
allBtn.onclick = () => {{
  activeBrands = [...allBrands];
  updateChart();
  updateButtons();
}};
filterEl.appendChild(allBtn);

// Per-brand buttons
const brandBtns = {{}};
allBrands.forEach(b => {{
  const btn = document.createElement('button');
  btn.textContent = b;
  btn.style.cssText = `padding:4px 12px;border-radius:20px;border:1.5px solid ${{BRAND_COLORS[b]}};background:${{BRAND_COLORS[b]}};color:#fff;font-size:12px;cursor:pointer;font-weight:500`;
  btn.dataset.brand = b;
  btn.onclick = () => {{
    if(activeBrands.includes(b) && activeBrands.length === 1) return; // keep at least 1
    if(activeBrands.includes(b)) {{
      activeBrands = activeBrands.filter(x=>x!==b);
    }} else {{
      activeBrands.push(b);
    }}
    updateChart();
    updateButtons();
  }};
  brandBtns[b] = btn;
  filterEl.appendChild(btn);
}});

function updateButtons(){{
  const allActive = activeBrands.length === allBrands.length;
  allBtn.style.background = allActive ? '#1a1a2e' : '#fff';
  allBtn.style.color = allActive ? '#fff' : '#1a1a2e';
  allBrands.forEach(b => {{
    const btn = brandBtns[b];
    const active = activeBrands.includes(b);
    btn.style.background = active ? BRAND_COLORS[b] : '#fff';
    btn.style.color = active ? '#fff' : BRAND_COLORS[b];
  }});
}}

function updateChart(){{
  trendChart.data.datasets = makeDatasets(activeBrands);
  trendChart.update();
}}

// ── Regions table ──────────────────────────────────────────────────────
function renderRegions() {{
  const tbody = document.getElementById('region-tbody');
  tbody.innerHTML = '';
  let lastRegion = null;
  const regionEntries = Object.values(latest.regions);
  regionEntries.sort((a,b)=>{{
    const ra = REGION_ORDER.indexOf(a.region), rb = REGION_ORDER.indexOf(b.region);
    const ord = (ra===-1?99:ra) - (rb===-1?99:rb);
    if(ord!==0) return ord;
    return (b.weighted_avg||0)-(a.weighted_avg||0);
  }});
  regionEntries.forEach(item=>{{
    const prvVal   = prev  && prev.regions  && prev.regions[`${{item.region}}||${{item.brand}}`];
    const prvAvg   = prvVal  ? prvVal.weighted_avg  : null;
    const firstVal = first && first.regions && first.regions[`${{item.region}}||${{item.brand}}`];
    const firstAvg = firstVal ? firstVal.weighted_avg : null;
    if(item.region !== lastRegion){{
      const regionLabel = translateRegion(item.region);
      tbody.innerHTML += `<tr><td colspan="7" class="region-hdr">📍 ${{regionLabel}}</td></tr>`;
      lastRegion = item.region;
    }}
    const bcc = item.brand==='Courier Center'?' brand-cc':'';
    tbody.innerHTML += `<tr>
      <td></td>
      <td class="${{bcc}}">${{item.brand}}</td>
      <td><span class="pill ${{ratingClass(item.weighted_avg)}}">${{item.weighted_avg !== null ? item.weighted_avg.toFixed(2) : '—'}}</span></td>
      <td>${{item.total_reviews ? item.total_reviews.toLocaleString('el-GR') : '—'}}</td>
      <td>${{item.store_count || '—'}}</td>
      <td>${{deltaHtml(item.weighted_avg, prvAvg)}}</td>
      <td>${{deltaHtml(item.weighted_avg, firstAvg)}}</td>
    </tr>`;
  }});
}}
const TRANSLATIONS = {{
  el: {{
    langBtn: 'EN',
    updated: 'Τελευταία ενημέρωση:',
    hSummary: 'Συνολική Αξιολόγηση (Weighted Average)',
    hTrend: 'Τάση Weighted Average (Πανελλαδικά)',
    hRegions: 'Αποτελέσματα ανά Περιοχή',
    hStores: 'Όλα τα Καταστήματα',
    thRegion: 'Περιοχή', thCompany: 'Εταιρία',
    thReviews: 'Κριτικές', thStoresCol: 'Καταστήματα',
    thAddress: 'Διεύθυνση', thSCompany: 'Εταιρία', thSReviews: 'Κριτικές',
    allBrands: 'Όλες οι εταιρίες', allRegions: 'Όλες οι περιοχές',
    searchPlaceholder: 'Αναζήτηση ονόματος / διεύθυνσης…',
    reviews: 'κριτικές', stores: 'καταστήματα',
    cardReviews: (n) => `${{n}} κριτικές`,
    cardStores: (n) => `${{n}} καταστήματα`,
    mapsLink: 'Maps ↗',
    footerText: 'Τα δεδομένα αντλούνται αυτόματα από το Google Places API και δεν υφίστανται επεξεργασία. Ο κώδικας συλλογής δεδομένων είναι δημόσια διαθέσιμος:',
    regions: {{
      'ΑΤΤΙΚΗ': 'ΑΤΤΙΚΗ',
      'Κεντρική Ελλάδα': 'Κεντρική Ελλάδα',
      'Θεσσαλονίκη': 'Θεσσαλονίκη',
      'Βόρεια Ελλάδα': 'Βόρεια Ελλάδα',
      'Δυτική Ελλάδα (με Κέρκυρα)': 'Δυτική Ελλάδα (με Κέρκυρα)',
      'Πελοπόννησος': 'Πελοπόννησος',
      'ΚΡΗΤΗ': 'ΚΡΗΤΗ',
      'Υπόλοιπα νησιά': 'Υπόλοιπα νησιά',
    }},
  }},
  en: {{
    langBtn: 'ΕΛ',
    updated: 'Last updated:',
    hSummary: 'Overall Rating (Weighted Average)',
    hTrend: 'Weighted Average Trend (Nationwide)',
    hRegions: 'Results by Region',
    hStores: 'All Branches',
    thRegion: 'Region', thCompany: 'Company',
    thReviews: 'Reviews', thStoresCol: 'Branches',
    thAddress: 'Address', thSCompany: 'Company', thSReviews: 'Reviews',
    allBrands: 'All companies', allRegions: 'All regions',
    searchPlaceholder: 'Search by name / address…',
    reviews: 'reviews', stores: 'branches',
    cardReviews: (n) => `${{n}} reviews`,
    cardStores: (n) => `${{n}} branches`,
    mapsLink: 'Maps ↗',
    footerText: 'Data is sourced automatically from the Google Places API and is not processed or filtered. The data collection script is publicly available:',
    regions: {{
      'ΑΤΤΙΚΗ': 'Attica',
      'Κεντρική Ελλάδα': 'Central Greece',
      'Θεσσαλονίκη': 'Thessaloniki',
      'Βόρεια Ελλάδα': 'Northern Greece',
      'Δυτική Ελλάδα (με Κέρκυρα)': 'Western Greece (incl. Corfu)',
      'Πελοπόννησος': 'Peloponnese',
      'ΚΡΗΤΗ': 'CRETE',
      'Υπόλοιπα νησιά': 'Other Islands',
    }},
  }},
}};

let currentLang = 'el';

function translateRegion(r) {{
  return TRANSLATIONS[currentLang].regions[r] || r;
}}

function applyLang(lang) {{
  const t = TRANSLATIONS[lang];
  currentLang = lang;
  // Topbar
  document.getElementById('lang-btn').textContent = t.langBtn;
  const upd = document.getElementById('topbar-updated');
  if(upd) upd.textContent = t.updated + upd.textContent.split(':').slice(1).join(':');
  // Headings
  document.getElementById('h-summary').textContent = t.hSummary;
  document.getElementById('h-trend').textContent = t.hTrend;
  document.getElementById('h-regions').textContent = t.hRegions;
  document.getElementById('h-stores').textContent = t.hStores;
  // Region table headers
  document.getElementById('th-region').textContent = t.thRegion;
  document.getElementById('th-company').textContent = t.thCompany;
  document.getElementById('th-reviews').textContent = t.thReviews;
  document.getElementById('th-stores-col').textContent = t.thStoresCol;
  // Stores table headers
  document.getElementById('th-address').textContent = t.thAddress;
  const thSC = document.getElementById('th-s-company');
  if(thSC) thSC.innerHTML = t.thSCompany + ' <span class="sort-icon">↕</span>';
  const thSR = document.getElementById('th-s-reviews');
  if(thSR) thSR.innerHTML = t.thReviews + ' <span class="sort-icon">↕</span>';
  // Filters
  document.getElementById('opt-all-brands').textContent = t.allBrands;
  document.getElementById('opt-all-regions').textContent = t.allRegions;
  document.getElementById('f-search').placeholder = t.searchPlaceholder;
  // Summary cards - update meta text
  document.querySelectorAll('.card .meta').forEach(el => {{
    const text = el.textContent;
    const nums = text.match(/[\d.,]+/g) || [];
    if(nums.length >= 2) {{
      el.textContent = t.cardReviews(nums[0]) + ' · ' + t.cardStores(nums[1]);
    }}
  }});
  // Redraw region table with translated region names
  renderRegions();
  // Redraw stores
  renderStores();
  // Footer
  const footer = document.querySelector('.footer-text');
  if(footer) footer.textContent = t.footerText;
  // Region filter options
  const fRegion = document.getElementById('f-region');
  Array.from(fRegion.options).forEach(opt => {{
    if(opt.value && TRANSLATIONS[lang].regions[opt.value]) {{
      opt.textContent = TRANSLATIONS[lang].regions[opt.value];
    }}
  }});
}}

function toggleLang() {{
  applyLang(currentLang === 'el' ? 'en' : 'el');
}}

renderRegions();



// ── Region inference (client-side) — bbox-first, then latin keywords ──
function inferRegion(name, address, lat, lng){{
  if(!lat || !lng) return 'Κεντρική Ελλάδα';
  // Κρήτη
  if(lat>=34.7&&lat<=35.9&&lng>=23.3&&lng<=26.7) return 'ΚΡΗΤΗ';
  // Δωδεκάνησα (Ρόδος/Κως)
  if(lat>=35.8&&lat<=37.0&&lng>=26.8&&lng<=29.7) return 'Υπόλοιπα νησιά';
  // Κυκλάδες
  if(lat>=36.3&&lat<=37.9&&lng>=24.3&&lng<=26.5) return 'Υπόλοιπα νησιά';
  // Χίος/Σάμος/Ικαρία
  if(lat>=37.4&&lat<=38.7&&lng>=25.8&&lng<=27.2) return 'Υπόλοιπα νησιά';
  // Λέσβος/Λήμνος/Θάσος/Σαμοθράκη
  if(lat>=38.8&&lat<=40.8&&lng>=25.0&&lng<=26.5) return 'Υπόλοιπα νησιά';
  // Σαρωνικά νησιά
  if(lat>=37.1&&lat<=37.9&&lng>=23.0&&lng<=23.6) return 'Υπόλοιπα νησιά';
  // Ιόνια νησιά εκτός Κέρκυρας
  if(lat>=37.5&&lat<=38.9&&lng>=20.3&&lng<=21.1) return 'Υπόλοιπα νησιά';
  // Κέρκυρα
  if(lat>=39.3&&lat<=39.9&&lng>=19.5&&lng<=20.3) return 'Δυτική Ελλάδα (με Κέρκυρα)';
  // Αττική
  if(lat>=37.7&&lat<=38.25&&lng>=23.2&&lng<=24.2) return 'ΑΤΤΙΚΗ';
  // Θεσσαλονίκη
  if(lat>=40.4&&lat<=40.85&&lng>=22.6&&lng<=23.3) return 'Θεσσαλονίκη';
  // Βόρεια Ελλάδα
  if(lat>=40.0) return 'Βόρεια Ελλάδα';
  // Πάτρα/Αχαΐα/Αίγιο
  if(lat>=37.9&&lat<=38.35&&lng>=21.6&&lng<=22.3) return 'Πελοπόννησος';
  // Ναύπακτος/Μεσολόγγι/Αγρίνιο
  if(lat>=38.2&&lat<=38.65&&lng>=21.0&&lng<=21.9) return 'Δυτική Ελλάδα (με Κέρκυρα)';
  // Κεντρική Ελλάδα: Τρίκαλα, Καρδίτσα, Λάρισα, Βόλος, Λαμία, Χαλκίδα
  if(lat>=38.3&&lat<=39.9&&lng>=21.5&&lng<=24.5) return 'Κεντρική Ελλάδα';
  // Πελοπόννησος
  if(lat<=38.1&&lng>=21.5&&lng<=23.2) return 'Πελοπόννησος';
  // Δυτική Ελλάδα
  if(lng<=22.5&&lat<=40.0) return 'Δυτική Ελλάδα (με Κέρκυρα)';
  return 'Κεντρική Ελλάδα';
}}


// ── Stores setup ─────────────────────────────────────────────────────
const latestPlaces = latest.places || [];
const prevPlaces   = (prev && prev.places) || [];
function keyOf(p){{
  // Use stable CID from maps URL if available
  const url = p.maps_url || '';
  const cid = url.match(/cid=([0-9]+)/);
  if(cid) return 'CID:' + cid[1];
  if(url) return 'URL:' + url;
  return `PNAD:${{p.brand}}|${{(p.place_name||'').toLowerCase().trim()}}|${{(p.address||'').toLowerCase().trim()}}`;
}}
const prevMap = {{}};
prevPlaces.forEach(p=>{{ prevMap[keyOf(p)] = p; }});
const firstPlaces = first ? (first.places || []) : [];
const firstMap = {{}};
firstPlaces.forEach(p=>{{ firstMap[keyOf(p)] = p; }});
latestPlaces.forEach(p=>{{ p._region = inferRegion(p.place_name, p.address, p.lat, p.lng); }});

const fBrand      = document.getElementById('f-brand');
const fRegion     = document.getElementById('f-region');
const fSearch     = document.getElementById('f-search');
const fPageSize   = document.getElementById('f-pagesize');
const storesTbody = document.getElementById('stores-tbody');
const pagination  = document.getElementById('pagination');
let currentPage   = 1;
// Ensure first "Όλα" options have empty value
fBrand.options[0].value = '';
fRegion.options[0].value = '';
allBrands.forEach(b=>{{ fBrand.innerHTML += `<option value="${{b}}">${{b}}</option>`; }});
REGION_ORDER.forEach(r=>{{ fRegion.innerHTML += `<option value="${{r}}">${{r}}</option>`; }});

function renderStores(){{
  const bFilter = fBrand.value;
  const rFilter = fRegion.value;
  const sFilter = fSearch.value.toLowerCase();
  const pageSize = parseInt(fPageSize.value);
  let rows = latestPlaces.filter(p=>{{
    if(bFilter && bFilter !== '' && p.brand !== bFilter) return false;
    if(rFilter && rFilter !== '' && p._region !== rFilter) return false;
    if(sFilter && !smartMatch(sFilter, p.place_name) && !smartMatch(sFilter, p.address)) return false;
    return true;
  }});
  rows.sort((a,b) => {{
    let va, vb;
    if(sortCol === 'brand')  {{ va = a.brand||''; vb = b.brand||''; return va.localeCompare(vb) * sortDir; }}
    if(sortCol === 'rating') {{ va = a.rating||0; vb = b.rating||0; }}
    else                     {{ va = a.reviews||0; vb = b.reviews||0; }}
    return (va - vb) * sortDir;
  }});

  const totalRows  = rows.length;
  const totalPages = pageSize >= 999999 ? 1 : Math.ceil(totalRows / pageSize);
  if(currentPage > totalPages) currentPage = 1;
  const start = (currentPage - 1) * pageSize;
  const pageRows = rows.slice(start, pageSize >= 999999 ? undefined : start + pageSize);

  // Pagination controls
  if(totalPages <= 1) {{
    pagination.innerHTML = `<span style="color:#94a3b8">${{totalRows.toLocaleString()}} αποτελέσματα</span>`;
  }} else {{
    let btns = `<span style="color:#94a3b8;margin-right:4px">${{totalRows.toLocaleString()}} αποτελέσματα</span>`;
    btns += `<button onclick="goPage(${{currentPage-1}})" ${{currentPage===1?'disabled':''}} style="padding:3px 10px;border-radius:6px;border:.5px solid #d1d5db;background:#fff;cursor:pointer;font-size:12px">‹</button>`;
    // Show page numbers with ellipsis
    for(let p=1; p<=totalPages; p++) {{
      if(p===1||p===totalPages||Math.abs(p-currentPage)<=1) {{
        btns += `<button onclick="goPage(${{p}})" style="padding:3px 10px;border-radius:6px;border:.5px solid #d1d5db;background:${{p===currentPage?'#1a1a2e':'#fff'}};color:${{p===currentPage?'#fff':'inherit'}};cursor:pointer;font-size:12px">${{p}}</button>`;
      }} else if(Math.abs(p-currentPage)===2) {{
        btns += `<span style="font-size:12px;color:#94a3b8">…</span>`;
      }}
    }}
    btns += `<button onclick="goPage(${{currentPage+1}})" ${{currentPage===totalPages?'disabled':''}} style="padding:3px 10px;border-radius:6px;border:.5px solid #d1d5db;background:#fff;cursor:pointer;font-size:12px">›</button>`;
    pagination.innerHTML = btns;
  }}

  storesTbody.innerHTML = pageRows.map(p=>{{
    const prv = prevMap[keyOf(p)];
    const dHtml = prv && prv.rating ? deltaHtml(p.rating, prv.rating) : '<span class="pill nc">—</span>';
    const mapLink = p.maps_url ? `<a href="${{p.maps_url}}" target="_blank" style="color:#3b82f6;font-size:11px">Maps ↗</a>` : '';
    const bcc = p.brand==='Courier Center'?' brand-cc':'';
    return `<tr>
      <td class="${{bcc}}">${{p.brand}}</td>
      <td style="max-width:280px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;color:#64748b">${{p.address}}</td>
      <td>${{p.rating !== null && p.rating !== undefined ? `<span class="pill ${{ratingClass(p.rating)}}">${{p.rating.toFixed(1)}}</span>` : '—'}}</td>
      <td>${{p.reviews.toLocaleString('el-GR')}}</td>
      <td>${{dHtml}}</td>
      <td>${{firstMap[keyOf(p)] && firstMap[keyOf(p)].rating ? deltaHtml(p.rating, firstMap[keyOf(p)].rating) : '<span class="pill nc">—</span>'}}</td>
      <td>${{mapLink}}</td>
    </tr>`;
  }}).join('');
}}

function goPage(p) {{
  currentPage = p;
  renderStores();
  document.getElementById('h-stores').scrollIntoView({{behavior:'smooth', block:'start'}});
}}
fBrand.addEventListener('change', () => {{ currentPage=1; renderStores(); }});
fRegion.addEventListener('change', () => {{ currentPage=1; renderStores(); }});
fSearch.addEventListener('input', () => {{ currentPage=1; renderStores(); }});
fPageSize.addEventListener('change', () => {{ currentPage=1; renderStores(); }});

// ── Smart search helper ───────────────────────────────────────────────
function norm(s) {{
  if(!s) return '';
  // lowercase + remove ALL diacritics (τόνοι, διαλυτικά κλπ)
  return s.toLowerCase().normalize('NFD').replace(/\p{{M}}/gu, '').normalize('NFC');
}}

const GL = [
  ['th','θ'],['ps','ψ'],['ch','χ'],['ou','ου'],
  ['tz','τζ'],['ts','τσ'],['mp','μπ'],['nt','ντ'],
  ['gk','γκ'],['ai','αι'],['ei','ει'],['oi','οι'],
  ['au','αυ'],['eu','ευ'],
  ['a','α'],['b','β'],['g','γ'],['d','δ'],['e','ε'],
  ['z','ζ'],['i','ι'],['k','κ'],['l','λ'],['m','μ'],
  ['n','ν'],['x','ξ'],['o','ο'],['p','π'],['r','ρ'],
  ['s','σ'],['t','τ'],['y','υ'],['f','φ'],['h','η'],
  ['v','β'],['w','ω'],['c','κ'],['u','υ'],['q','κ']
];

function toGreek(s) {{
  let r = s;
  for(const [lat,gr] of GL) r = r.replaceAll(lat, gr);
  return r;
}}

function smartMatch(query, text) {{
  if(!query) return true;
  if(!text) return false;
  const q = norm(query);
  const t = norm(text);
  if(t.includes(q)) return true;
  // try greeklish conversion
  const gq = toGreek(q);
  if(gq !== q && t.includes(gq)) return true;
  return false;
}}

// ── Sorting ───────────────────────────────────────────────────────────
let sortCol = 'reviews';
let sortDir = -1; // -1=desc, 1=asc

document.querySelectorAll('th.sortable').forEach(th => {{
  th.addEventListener('click', () => {{
    const col = th.dataset.col;
    if(sortCol === col) {{
      sortDir *= -1;
    }} else {{
      sortCol = col;
      sortDir = col === 'brand' ? 1 : -1;
    }}
    document.querySelectorAll('th.sortable .sort-icon').forEach(el => el.textContent = '↕');
    th.querySelector('.sort-icon').textContent = sortDir === 1 ? '↑' : '↓';
    renderStores();
  }});
}});

// Fix: ensure empty value = "Όλα" works correctly
fBrand.addEventListener('change', () => {{ if(fBrand.value === '') fBrand.selectedIndex = 0; }});
fRegion.addEventListener('change', () => {{ if(fRegion.value === '') fRegion.selectedIndex = 0; }});

renderStores();

document.getElementById('run-ts').textContent = 'Generated: ' + new Date().toLocaleString('el-GR');

// ── i18n ──────────────────────────────────────────────────────────────
</script>

<div style="text-align:center;padding:2rem 1rem 1.5rem;font-size:12px;color:#94a3b8;border-top:0.5px solid #e8ecf0;margin-top:2rem">
  <span class="footer-text">Τα δεδομένα αντλούνται αυτόματα από το Google Places API και δεν υφίστανται επεξεργασία.
  Ο κώδικας συλλογής δεδομένων είναι δημόσια διαθέσιμος:</span><br>
  <a href="https://github.com/couriercenter/courier-ratings/blob/main/courier_ratings.py"
     target="_blank"
     style="color:#3b82f6;text-decoration:none;font-weight:500">
    github.com/couriercenter/courier-ratings/courier_ratings.py
  </a>
</div>

</body>
</html>"""
    return html

# ─────────────────────────── GIT PUSH ─────────────────────────────────────
def git_push(date_str):
    if not GIT_PUSH:
        print("[GIT] Skipped (GIT_PUSH=False)")
        return
    cmds = [
        ["git", "-C", REPO_DIR, "add", "index.html", "history.json"],
        ["git", "-C", REPO_DIR, "commit", "-m", COMMIT_MSG.format(date=date_str)],
        ["git", "-C", REPO_DIR, "push"],
    ]
    for cmd in cmds:
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode != 0:
            print(f"[GIT] Warning: {' '.join(cmd)}\n{r.stderr}")
        else:
            print(f"[GIT] OK: {' '.join(cmd[-2:])}")

# ─────────────────────────── MAIN ─────────────────────────────────────────
def main():
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    label = datetime.now(timezone.utc).strftime("%d/%m/%Y")
    print(f"\n{'='*55}\nCourier Ratings — {label}\n{'='*55}")

    print("\n[1/5] Collecting places from Google API...")
    df = collect()

    print("\n[2/5] Cleaning data...")
    df = clean(df)
    print(f"      → {len(df)} places after cleaning")

    print("\n[3/5] Summarising...")
    summary = summarize(df)
    regions = summarize_regions(df)
    places  = places_list(df)
    for b, v in sorted(summary.items(), key=lambda x: -(x[1]['weighted_avg'] or 0)):
        print(f"      {b:25s} weighted={v['weighted_avg']}  reviews={v['total_reviews']}")

    print("\n[4/5] Updating history.json...")
    history = load_history()
    history = append_snapshot(history, today, label, summary, regions, places)
    save_history(history)

    print("\n[5/5] Building HTML report...")
    html = build_html(history)
    with open(REPORT_FILE, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"      → {REPORT_FILE}")

    git_push(today)
    print(f"\n✅ Done — {label}")

if __name__ == "__main__":
    main()
