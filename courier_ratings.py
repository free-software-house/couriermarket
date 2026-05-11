# -*- coding: utf-8 -*-
"""
courier_ratings.py

Collects Google Places ratings for selected courier brands in Greece and updates
history.json only. It does not generate or modify index.html.

Required GitHub Actions secret:
- PLACES_API_KEY
"""

import json
import os
import re as _re
import time
import unicodedata
from datetime import datetime, timezone
from functools import lru_cache
from typing import Any, Dict, List

import pandas as pd
import requests

# ─────────────────────────── CONFIG ───────────────────────────────────────
API_KEY = os.environ.get("PLACES_API_KEY", "")
REPO_DIR = os.path.dirname(os.path.abspath(__file__))
HISTORY_FILE = os.path.join(REPO_DIR, "history.json")

RATE_SLEEP = 0.3
HTTP_TIMEOUT = 25
SEARCH_RADIUS_M = 50000

if not API_KEY:
    raise RuntimeError("Missing PLACES_API_KEY environment variable.")

# ─────────────────────────── BRANDS & QUERIES ─────────────────────────────
BRANDS: Dict[str, List[str]] = {
    "ACS": ["Κατάστημα ACS", "ACS Courier"],
    "Γενική Ταχυδρομική": ["Κατάστημα Γενική Ταχυδρομική", "Γενική Ταχυδρομική"],
    "ΕΛΤΑ Courier": ["Κατάστημα ΕΛΤΑ Courier", "Κατάστημα ELTA Courier", "ΕΛΤΑ Courier"],
    "SPEEDEX": ["Κατάστημα SPEEDEX", "SPEEDEX Courier"],
    "Courier Center": ["Κατάστημα Courier Center", "Courier Center"],
    "EASYMAIL": ["Κατάστημα easymail", "Easymail"],
}

GREECE_CENTERS = [
    (37.9838, 23.7275), (38.3250, 23.3187), (38.4353, 22.8764), (38.4371, 22.4318),
    (38.9006, 22.4338), (38.9182, 22.6159), (39.0003, 21.7931), (39.3623, 22.9427),
    (39.1825, 22.7596), (39.6390, 22.4196), (39.8897, 22.1870), (39.2926, 22.3849),
    (39.5550, 21.7679), (39.3656, 21.9214), (40.2697, 22.5061), (40.6401, 22.9444),
    (40.5470, 23.0213), (40.6757, 22.8352), (40.6117, 22.9780), (40.5897, 22.9507),
    (40.6680, 22.9301), (40.6902, 22.9004), (40.7868, 22.5807), (40.7481, 23.0656),
    (40.2414, 23.2843), (40.3809, 23.4413), (40.2029, 23.6645), (40.3953, 23.8856),
    (40.5233, 22.2033), (40.6293, 22.0692), (40.9937, 22.8743), (40.0833, 21.4275),
    (40.3006, 21.7896), (40.5143, 21.6786), (40.5200, 21.2687), (40.7880, 22.4070),
    (40.7858, 22.3148), (41.0903, 23.5414), (41.1495, 24.1474), (40.9396, 24.4018),
    (41.1343, 24.8877), (41.1169, 25.4040), (40.8470, 25.8744), (41.5048, 26.5297),
    (38.2466, 21.7346), (38.2305, 21.7371), (38.2523, 22.0819), (37.6753, 21.4374),
    (37.9381, 22.9320), (38.0146, 22.7496), (37.5674, 22.8069), (37.0738, 22.4297),
    (37.5108, 22.3735), (37.0387, 22.1142), (37.7951, 21.3507), (38.3911, 21.8277),
    (38.3714, 21.4315), (38.6218, 21.4074), (39.1585, 20.9877), (38.9559, 20.7505),
    (35.3387, 25.1442), (35.0510, 25.7463), (35.1280, 25.7308), (35.3655, 24.4820),
    (35.4748, 23.8044), (35.0514, 25.0787), (39.6239, 19.9217), (36.4349, 28.2176),
    (37.0850, 25.1500), (36.3932, 25.4615), (37.5379, 25.1634), (38.3687, 26.1359),
    (36.8928, 27.2877), (39.1070, 26.5550), (39.6650, 20.8537), (36.1400, 29.5900),
    (37.7200, 23.9100), (37.6500, 23.8500), (37.8800, 24.0200), (37.9500, 23.9500),
    (38.0500, 23.8800),
]

FIELDS = [
    "places.name", "places.displayName", "places.formattedAddress",
    "places.googleMapsUri", "places.rating", "places.userRatingCount",
    "places.types", "places.id", "places.location",
]

SEARCH_URL = "https://places.googleapis.com/v1/places:searchText"
DETAILS_BASE = "https://places.googleapis.com/v1/"
SESSION = requests.Session()

# ─────────────────────────── TEXT HELPERS ────────────────────────────────
def _strip_accents(s: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFD", s or "")
        if unicodedata.category(c) != "Mn"
    )


def _norm(s: str) -> str:
    return _re.sub(r"\s+", " ", _strip_accents(s or "").lower()).strip()


def _norm_np(s: str) -> str:
    return _re.sub(r"[^\w\s]", " ", _norm(s))

# ─────────────────────────── BRAND RULES ─────────────────────────────────
KEYWORDS_NORM: Dict[str, List[str]] = {
    "ACS": ["acs"],
    "Courier Center": ["courier center"],
    "EASYMAIL": ["easymail", "easy mail", "easy-mail"],
    "SPEEDEX": ["speedex", "speed ex"],
    "Γενική Ταχυδρομική": [
        "γενικη ταχυδρομικη", "γενικη", "ταχυδρομικη",
        "geniki tachydromiki", "geniki", "tachydromiki",
    ],
    "ΕΛΤΑ Courier": [
        "ελτα courier", "elta courier", "ελτα κουριερ",
        "ταχυμεταφορες ελτα", "ελτα", "elta",
    ],
}

REASSIGN_ALIASES: Dict[str, List[str]] = {
    "SPEEDEX": ["speedex", "speed ex"],
    "ACS": ["acs", "acs courier"],
    "Γενική Ταχυδρομική": ["γενικη ταχυδρομικη", "geniki tachydromiki"],
    "ΕΛΤΑ Courier": ["ελτα", "ελτα courier", "elta", "elta courier"],
    "Courier Center": ["courier center"],
    "EASYMAIL": ["easymail", "easy mail"],
}

BLACKLIST = [
    "city courier", "city express", "general courier", "sports center", "dpd",
    "smartpoint", "smart point", "dhl", "hub", "clever point", "ups", "artcourier",
    "kritiki tahidromiki", "ελτα πρακτορειο", "ελτα -", "ελληνικα ταχυδρομεια",
    "hellenic post", "ταχυδρομικο πρακτορειο", "ταχυδρομειο", "postal agency",
    "ταχυδρομικο ταμιευτηριο", "box express", "icc", "taxydema",
]
_BL_RE = _re.compile("|".join(_re.escape(t) for t in BLACKLIST), _re.I)

# ─────────────────────────── REGION INFERENCE ────────────────────────────
def infer_region(name: str, addr: str, lat: float, lng: float) -> str:
    if lat is None or lng is None:
        return "Κεντρική Ελλάδα"
    if 34.7 <= lat <= 35.9 and 23.3 <= lng <= 26.7:
        return "ΚΡΗΤΗ"
    if 35.8 <= lat <= 37.0 and 26.8 <= lng <= 29.7:
        return "Υπόλοιπα νησιά"
    if 36.3 <= lat <= 37.9 and 24.3 <= lng <= 26.5:
        return "Υπόλοιπα νησιά"
    if 37.4 <= lat <= 38.7 and 25.8 <= lng <= 27.2:
        return "Υπόλοιπα νησιά"
    if 38.8 <= lat <= 40.8 and 25.0 <= lng <= 26.5:
        return "Υπόλοιπα νησιά"
    if 37.1 <= lat <= 37.9 and 23.0 <= lng <= 23.6:
        return "Υπόλοιπα νησιά"
    if 37.5 <= lat <= 38.9 and 20.3 <= lng <= 21.1:
        return "Υπόλοιπα νησιά"
    if 39.3 <= lat <= 39.9 and 19.5 <= lng <= 20.3:
        return "Δυτική Ελλάδα (με Κέρκυρα)"
    if 37.7 <= lat <= 38.25 and 23.2 <= lng <= 24.2:
        return "ΑΤΤΙΚΗ"
    if 40.4 <= lat <= 40.85 and 22.6 <= lng <= 23.3:
        return "Θεσσαλονίκη"
    if lat >= 40.0:
        return "Βόρεια Ελλάδα"
    if 37.9 <= lat <= 38.35 and 21.6 <= lng <= 22.3:
        return "Πελοπόννησος"
    if 38.2 <= lat <= 38.65 and 21.0 <= lng <= 21.9:
        return "Δυτική Ελλάδα (με Κέρκυρα)"
    if 38.3 <= lat <= 39.9 and 21.5 <= lng <= 24.5:
        return "Κεντρική Ελλάδα"
    if lat <= 38.1 and 21.5 <= lng <= 23.2:
        return "Πελοπόννησος"
    if lng <= 22.5 and lat <= 40.0:
        return "Δυτική Ελλάδα (με Κέρκυρα)"
    return "Κεντρική Ελλάδα"

# ─────────────────────────── GOOGLE API ──────────────────────────────────
def _headers() -> Dict[str, str]:
    return {
        "Content-Type": "application/json",
        "X-Goog-Api-Key": API_KEY,
        "X-Goog-FieldMask": ",".join(FIELDS),
    }


def _det_headers() -> Dict[str, str]:
    return {
        "Content-Type": "application/json",
        "X-Goog-Api-Key": API_KEY,
        "X-Goog-FieldMask": "rating,userRatingCount,location,formattedAddress,googleMapsUri,displayName",
    }


def search_text(query: str) -> List[Dict[str, Any]]:
    results = []
    for clat, clng in GREECE_CENTERS:
        payload = {
            "textQuery": query,
            "languageCode": "el",
            "regionCode": "GR",
            "locationBias": {
                "circle": {
                    "center": {"latitude": clat, "longitude": clng},
                    "radius": SEARCH_RADIUS_M,
                }
            },
        }
        r = SESSION.post(SEARCH_URL, headers=_headers(), json=payload, timeout=HTTP_TIMEOUT)
        if r.status_code == 200:
            results.extend(r.json().get("places", []))
        else:
            print(f"[WARN] Google API status {r.status_code} for query: {query}")
        time.sleep(RATE_SLEEP)
    return results


@lru_cache(maxsize=4096)
def fetch_details(resource_name: str) -> Dict[str, Any]:
    r = SESSION.get(DETAILS_BASE + resource_name, headers=_det_headers(), timeout=HTTP_TIMEOUT)
    return r.json() if r.status_code == 200 else {}

# ─────────────────────────── DATA COLLECTION ─────────────────────────────
def collect() -> pd.DataFrame:
    rows = []
    seen = set()
    for brand, queries in BRANDS.items():
        brand_start = len(rows)
        for q in queries:
            try:
                places = search_text(q)
            except Exception as e:
                print(f"[WARN] {brand} / '{q}': {e}")
                continue
            for p in places:
                rid = p.get("name") or p.get("id")
                if not rid or rid in seen:
                    continue
                seen.add(rid)
                det = fetch_details(p["name"]) if not p.get("rating") else {}
                loc = p.get("location") or det.get("location") or {}
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
        print(f"[INFO] {brand}: {len(rows) - brand_start} places")
    return pd.DataFrame(rows)

# ─────────────────────────── CLEANING ────────────────────────────────────
def clean(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df

    bl = df["place_name"].fillna("").str.contains(_BL_RE, na=False)
    if bl.sum():
        print(f"[CLEAN] Blacklist removed: {bl.sum()}")
    df = df[~bl].copy()

    changed = 0
    for idx, row in df.iterrows():
        txt = _norm(f"{row.get('place_name', '')} {row.get('address', '')}")
        for canonical, aliases in REASSIGN_ALIASES.items():
            if any(a and a in txt for a in aliases):
                if canonical != row["brand"]:
                    df.at[idx, "brand"] = canonical
                    changed += 1
                break
    if changed:
        print(f"[REASSIGN] Changed: {changed}")

    def ok(row: pd.Series) -> bool:
        brand = str(row.get("brand", ""))
        txt = _norm_np(str(row.get("place_name", "")))
        kws = KEYWORDS_NORM.get(brand, [])
        return any(kw in txt for kw in kws) if kws else True

    mask = df.apply(ok, axis=1)
    if (~mask).sum():
        print(f"[FILTER] Brand-name rule removed: {(~mask).sum()}")
    df = df[mask].copy()

    def make_key(row: pd.Series):
        url = (row.get("maps_url") or "").strip()
        if url:
            return ("URL", url)
        return (
            "PNAD",
            f"{_norm(str(row.get('place_name', '')))}|{_norm(str(row.get('address', '')))}",
        )

    chosen = {}
    for _, row in df.iterrows():
        key = make_key(row)
        cur = chosen.get(key)
        if cur is None:
            chosen[key] = row
            continue
        cur_reviews = float(cur.get("user_rating_count") or 0)
        row_reviews = float(row.get("user_rating_count") or 0)
        if row_reviews > cur_reviews:
            chosen[key] = row

    before = len(df)
    df = pd.DataFrame(list(chosen.values())).reset_index(drop=True)
    if before - len(df):
        print(f"[DEDUP] Removed: {before - len(df)}")
    return df

# ─────────────────────────── SUMMARY ─────────────────────────────────────
def summarize(df: pd.DataFrame) -> Dict[str, Dict[str, Any]]:
    out = {}
    for brand, g in df.groupby("brand"):
        g = g.dropna(subset=["rating"]).copy()
        r = pd.to_numeric(g["rating"], errors="coerce").dropna()
        urc = pd.to_numeric(g.loc[r.index, "user_rating_count"], errors="coerce").fillna(0)
        total = int(urc.sum())
        out[brand] = {
            "weighted_avg": round(float((r * urc).sum() / total), 2) if total else None,
            "simple_avg": round(float(r.mean()), 2) if len(r) else None,
            "total_reviews": total,
            "store_count": len(g),
        }
    return out


def summarize_regions(df: pd.DataFrame) -> Dict[str, Dict[str, Any]]:
    df = df.copy()
    df["region"] = df.apply(
        lambda r: infer_region(
            str(r.get("place_name", "")),
            str(r.get("address", "")),
            r.get("lat"),
            r.get("lng"),
        ),
        axis=1,
    )

    out = {}
    for (region, brand), g in df.groupby(["region", "brand"]):
        g = g.dropna(subset=["rating"]).copy()
        r = pd.to_numeric(g["rating"], errors="coerce").dropna()
        urc = pd.to_numeric(g.loc[r.index, "user_rating_count"], errors="coerce").fillna(0)
        total = int(urc.sum())
        out[f"{region}||{brand}"] = {
            "region": region,
            "brand": brand,
            "weighted_avg": round(float((r * urc).sum() / total), 2) if total else None,
            "total_reviews": total,
            "store_count": len(g),
        }
    return out


def places_list(df: pd.DataFrame) -> List[Dict[str, Any]]:
    rows = []
    for _, r in df.dropna(subset=["rating"]).iterrows():
        rows.append({
            "brand": str(r.get("brand", "")),
            "place_name": str(r.get("place_name", "")),
            "address": str(r.get("address", "")),
            "rating": round(float(r["rating"]), 1),
            "reviews": int(r.get("user_rating_count") or 0),
            "maps_url": str(r.get("maps_url", "")),
            "lat": float(r["lat"]) if pd.notna(r.get("lat")) else None,
            "lng": float(r["lng"]) if pd.notna(r.get("lng")) else None,
        })
    return rows

# ─────────────────────────── HISTORY ─────────────────────────────────────
def load_history() -> Dict[str, Any]:
    if os.path.exists(HISTORY_FILE):
        with open(HISTORY_FILE, encoding="utf-8") as f:
            return json.load(f)
    return {"snapshots": []}


def save_history(history: Dict[str, Any]) -> None:
    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, indent=2)


def append_snapshot(
    history: Dict[str, Any],
    date_str: str,
    label: str,
    summary: Dict[str, Any],
    regions: Dict[str, Any],
    places: List[Dict[str, Any]],
) -> Dict[str, Any]:
    new_snap = {
        "date": date_str,
        "label": label,
        "summary": summary,
        "regions": regions,
        "places": places,
    }

    snapshots = history.setdefault("snapshots", [])

    # Replace snapshot when rerunning the same UTC date.
    snapshots[:] = [s for s in snapshots if s.get("date") != date_str]
    snapshots.append(new_snap)
    snapshots.sort(key=lambda s: s.get("date", ""))
    print(f"[HISTORY] Saved snapshot {date_str}")
    return history

# ─────────────────────────── MAIN ────────────────────────────────────────
def main() -> None:
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    label = datetime.now(timezone.utc).strftime("%d/%m/%Y")

    print(f"\n{'=' * 55}\nRatings data collection — {label}\n{'=' * 55}")

    print("\n[1/4] Collecting places from Google API...")
    df = collect()

    print("\n[2/4] Cleaning data...")
    df = clean(df)
    print(f"      → {len(df)} places after cleaning")

    print("\n[3/4] Summarising...")
    summary = summarize(df)
    regions = summarize_regions(df)
    places = places_list(df)
    for brand, values in sorted(summary.items(), key=lambda x: -(x[1]["weighted_avg"] or 0)):
        print(f"      {brand:25s} weighted={values['weighted_avg']} reviews={values['total_reviews']}")

    print("\n[4/4] Updating history.json...")
    history = load_history()
    history = append_snapshot(history, today, label, summary, regions, places)
    save_history(history)

    print(f"\nDone — history.json updated for {label}")


if __name__ == "__main__":
    main()
