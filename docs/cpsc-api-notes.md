# CPSC Recall API — Verification Notes

Verified 2026-10-04 by direct probing (read-only GET requests; nothing submitted).
This note answers the blueprint's Phase 2 gate: *"validate availability and field format before integration"*.

## Endpoint

```
GET https://www.saferproducts.gov/RestWebServices/Recall?Format=json
```

- **Auth:** none. No API key, no registration.
- **Format:** `?Format=json` returns a JSON array. (Default without the parameter is XML.)
- **HTTP:** 200 on every probe (2026-10-04).
- **Filtering:** none server-side. The `/Recall/Help` endpoint returns an error
  document (`"Error retrieving Recalls: An error occurred while reading from the
  store provider's data reader"`), and every request returns the **full recall
  dump** regardless of query parameters. There is no `brand=`, `model=`, or
  free-text search parameter.
- **Payload size:** ~27.7 MB per response (measured 3×, byte-identical).
- **Latency:** 13–25 s per full download (measured from this VM).
- **Rate limiting:** none observed on rapid sequential requests — but each
  request is ~28 MB, so be polite: **fetch at most once per day** and cache.
- **User-Agent filtering:** the WAF returns **HTTP 403 for the default
  `python-requests/*` User-Agent** (verified 2026-10-04; curl and any other
  UA get 200). Always send a custom `User-Agent` header.
- **Snapshot stats (2026-10-04):** ~9,100–10,000 recall records, `RecallDate`
  range 1994-04-07 → 2026-09-24.

## Record fields

Top-level fields (22), from a real record (`RecallID` 10992, 5Color helmets):

| Field | Type | Notes for us |
|---|---|---|
| `RecallID` / `RecallNumber` | int | Stable IDs; use `RecallID` as `alert_id`. |
| `RecallDate` / `LastPublishDate` | ISO-8601 datetime | Sort/filter client-side; `LastPublishDate` enables incremental sync. |
| `Title` | string | **Contains the brand name** (e.g. "5Color Recalls Children's Bicycle Helmet…"). Primary brand-match surface. |
| `Description` | string | Free text; often repeats brand + model numbers. |
| `URL` | string | Official `cpsc.gov/Recalls/…` link — attach to every alert. |
| `Products` | list of `{Name, Description, Model, Type, CategoryID}` | `Name` helps brand matching. ⚠️ **`Model` is ALWAYS empty** (10,077 product entries probed, 0 non-empty). Do not rely on it. |
| `Hazards` | list of `{Name}` | Human-readable hazard; good for the alert summary. |
| `Injuries` / `Remedies` / `RemedyOptions` | lists | Optional context for the briefing card. |
| `Manufacturers` / `Retailers` / `Importers` / `Distributors` / `Inconjunctions` | lists | Frequently empty; `Retailers[].Name` sometimes holds the seller. Not reliable for brand. |
| `ProductUPCs` | list | Rarely populated; ignore for MVP. |
| `Images` | list of `{URL, Caption}` | Recalled-product photos (nice-to-have for cards). |
| `ManufacturerCountries` | list of `{Country}` | Ignore for MVP. |
| `ConsumerContact` | string | Phone/email for the remedy; useful in `report_issue`. |
| `SoldAtLabel` | string/null | Usually null. |

## Critical findings for the matching logic

1. **No dedicated brand field.** Brand matching must be a case-insensitive
   substring search over `Title + Description + Products[].Name`.
2. **`Products[].Model` is always empty** in the current snapshot, so model
   matching must *also* be a substring search over `Title + Description`
   (model numbers like "YD-001" appear there, e.g. in image captions text).
3. The blueprint rule *"only flag 'possibly affected' when brand AND model
   both match"* therefore means: brand substring found **and** model
   substring found in the recall's text. Both the brand and the model must be
   non-empty on our side, otherwise no "possibly affected" flag (avoids
   false positives from brand-only matches).
4. Because the dump is full-download-only, the daily scan must **cache one
   snapshot per day** (S3 object, refreshed by the EventBridge trigger) and
   match all appliances against the in-memory copy. Never fetch per device.

## Python call example

```python
import time
import requests

CPSC_URL = "https://www.saferproducts.gov/RestWebServices/Recall"
_cache = {"at": 0.0, "recalls": []}
CACHE_TTL = 24 * 3600  # refresh at most once a day

# The endpoint's WAF rejects the default `python-requests/*` User-Agent
# (HTTP 403, verified 2026-10-04); any other UA works.
USER_AGENT = "HomeKeeper/1.0 (AWS hackathon demo)"


def fetch_recalls(timeout=60):
    """Download the full CPSC recall dump (cached 24h). Cold path only."""
    now = time.time()
    if _cache["recalls"] and now - _cache["at"] < CACHE_TTL:
        return _cache["recalls"]
    resp = requests.get(CPSC_URL, params={"Format": "json"},
                        headers={"User-Agent": USER_AGENT}, timeout=timeout)
    resp.raise_for_status()
    _cache["recalls"] = resp.json()
    _cache["at"] = now
    return _cache["recalls"]


def _haystack(recall):
    parts = [recall.get("Title") or "", recall.get("Description") or ""]
    for p in recall.get("Products") or []:
        parts += [p.get("Name") or "", p.get("Description") or ""]
    return " ".join(parts).lower()


def match_recalls(recalls, brand, model):
    """Brand AND model must both substring-match (case-insensitive).

    Returns recalls flagged "possibly affected". Empty brand/model -> no match.
    """
    brand, model = (brand or "").strip().lower(), (model or "").strip().lower()
    if not brand or not model:
        return []
    return [r for r in recalls if brand in _haystack(r) and model in _haystack(r)]
```

## Caveats

- **Snapshot, not live search.** A recall published minutes ago may not be in
  the dump yet; daily caching matches our daily-scan cadence anyway.
- **False negatives possible.** If CPSC writes the brand as "5-Color" but our
  record says "5Color", substring matching misses. Mitigation: normalize by
  stripping non-alphanumerics on both sides before comparing (implemented in
  `agent/home_analyst.py`).
- **False positives possible.** A model substring like "100" matches many
  unrelated recalls. The brand+model AND-rule plus the "possibly affected"
  wording (never "your unit IS recalled") keeps this honest.
- **Payload weight.** 27.7 MB × daily = ~830 MB/month transfer if fetched
  from AgentCore each day; prefer one S3 copy per day shared by all runs.
- **No SLA.** This is a public informational endpoint; the static-snapshot
  fallback in the risk table (seed a JSON copy for demo) stays valid.
