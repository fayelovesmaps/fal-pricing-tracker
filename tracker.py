#!/usr/bin/env python3
"""
fal.ai video-model pricing tracker
==================================
Scrapes the pricing sentence shown in the "Result" box of each fal.ai model page
(the text under "What would you like to do next?" / the Download button),
parses it into structured $/second price points, snapshots it to data/,
flags any changes vs the previous snapshot, and regenerates dashboard.html.

Run locally (per mentor guidance — fal may serve different content to cloud IPs):

    pip install requests playwright
    playwright install chromium        # only needed for the fallback path
    python tracker.py                  # full run: fetch -> parse -> diff -> dashboard
    python tracker.py --render-only    # rebuild dashboard.html from latest snapshot

How extraction works, in order of preference:
  1. GET {model_url}/llms.txt  -> clean markdown with a "## Pricing" section.
     This is fal's machine-readable page and is far more stable than the DOM.
  2. GET {model_url}           -> the pricing sentence is server-rendered HTML;
     we strip tags and regex for the sentence.
  3. Playwright (chromium)     -> only if plain requests are blocked/empty.
Pages that show the "Early access" gate are recorded with status=early_access.
"""

from __future__ import annotations
import argparse, csv, datetime, html, json, re, sys, time
from pathlib import Path

import requests

# --------------------------------------------------------------------------- #
# Endpoint registry: (fal model path, company, display model name, category)
# --------------------------------------------------------------------------- #
ENDPOINTS = [
    # ---- Text to Video ----
    ("alibaba/happy-horse/text-to-video",                   "Alibaba",  "Happy Horse 1.0",         "T2V"),
    ("alibaba/happy-horse/v1.1/text-to-video",              "Alibaba",  "Happy Horse 1.1",         "T2V"),
    ("fal-ai/wan/v2.7/text-to-video",                       "Alibaba",  "Wan 2.7",                 "T2V"),
    ("wan/v2.6/text-to-video",                              "Alibaba",  "Wan 2.6",                 "T2V"),
    ("bytedance/seedance-2.0/mini/text-to-video",           "ByteDance","Seedance 2.0 Mini",       "T2V"),
    ("bytedance/seedance-2.0/text-to-video",                "ByteDance","Seedance 2.0",            "T2V"),
    ("bytedance/seedance-2.0/fast/text-to-video",           "ByteDance","Seedance 2.0 Fast",       "T2V"),
    ("fal-ai/kling-video/v3/pro/text-to-video",             "Kling",    "Kling v3 Pro",            "T2V"),
    ("fal-ai/kling-video/v3/standard/text-to-video",        "Kling",    "Kling v3 Standard",       "T2V"),
    ("fal-ai/kling-video/v3/turbo/pro/text-to-video",       "Kling",    "Kling v3 Turbo Pro",      "T2V"),
    ("fal-ai/kling-video/v3/4k/text-to-video",              "Kling",    "Kling v3 4K",             "T2V"),
    ("fal-ai/kling-video/v3/turbo/standard/text-to-video",  "Kling",    "Kling v3 Turbo Standard", "T2V"),
    # ---- Image to Video ----
    ("alibaba/happy-horse/image-to-video",                  "Alibaba",  "Happy Horse 1.0",         "I2V"),
    ("alibaba/happy-horse/v1.1/image-to-video",             "Alibaba",  "Happy Horse 1.1",         "I2V"),
    ("fal-ai/wan/v2.7/image-to-video",                      "Alibaba",  "Wan 2.7",                 "I2V"),
    ("wan/v2.6/image-to-video",                             "Alibaba",  "Wan 2.6",                 "I2V"),
    ("wan/v2.6/image-to-video/flash",                       "Alibaba",  "Wan 2.6 Flash",           "I2V"),
    ("bytedance/seedance-2.0/mini/image-to-video",          "ByteDance","Seedance 2.0 Mini",       "I2V"),
    ("bytedance/seedance-2.0/image-to-video",               "ByteDance","Seedance 2.0",            "I2V"),
    ("bytedance/seedance-2.0/fast/image-to-video",          "ByteDance","Seedance 2.0 Fast",       "I2V"),
    ("fal-ai/kling-video/v3/pro/image-to-video",            "Kling",    "Kling v3 Pro",            "I2V"),
    ("fal-ai/kling-video/v3/standard/image-to-video",       "Kling",    "Kling v3 Standard",       "I2V"),
    ("fal-ai/kling-video/v3/4k/image-to-video",             "Kling",    "Kling v3 4K",             "I2V"),
    ("fal-ai/kling-video/v3/turbo/pro/image-to-video",      "Kling",    "Kling v3 Turbo Pro",      "I2V"),
    ("fal-ai/kling-video/v3/turbo/standard/image-to-video", "Kling",    "Kling v3 Turbo Standard", "I2V"),
    # ---- Reference to Video ----
    ("alibaba/happy-horse/reference-to-video",              "Alibaba",  "Happy Horse 1.0",         "R2V"),
    ("alibaba/happy-horse/v1.1/reference-to-video",         "Alibaba",  "Happy Horse 1.1",         "R2V"),
    ("fal-ai/wan/v2.7/reference-to-video",                  "Alibaba",  "Wan 2.7",                 "R2V"),
    ("wan/v2.6/reference-to-video/flash",                   "Alibaba",  "Wan 2.6 Flash",           "R2V"),
    ("wan/v2.6/reference-to-video",                         "Alibaba",  "Wan 2.6",                 "R2V"),
    ("bytedance/seedance-2.0/mini/reference-to-video",      "ByteDance","Seedance 2.0 Mini",       "R2V"),
    ("bytedance/seedance-2.0/reference-to-video",           "ByteDance","Seedance 2.0",            "R2V"),
    ("bytedance/seedance-2.0/fast/reference-to-video",      "ByteDance","Seedance 2.0 Fast",       "R2V"),
    # ---- Video Edit ----
    ("alibaba/happy-horse/video-edit",                      "Alibaba",  "Happy Horse 1.0",         "Video Edit"),
    ("fal-ai/wan/v2.7/edit-video",                          "Alibaba",  "Wan 2.7 Edit Video",      "Video Edit"),
]

BASE = "https://fal.ai/models/"
HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                         "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36"}
ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
DASHBOARD_TEMPLATE = ROOT / "dashboard_template.html"
DASHBOARD_OUT = ROOT / "dashboard.html"

# --------------------------------------------------------------------------- #
# Fetching
# --------------------------------------------------------------------------- #
def fetch_llms_txt(model_path: str) -> str | None:
    """fal's machine-readable page; the '## Pricing' section is the same
    sentence shown under the Download button on the model page."""
    try:
        r = requests.get(f"{BASE}{model_path}/llms.txt", headers=HEADERS, timeout=30)
        if r.status_code == 200 and "## Pricing" in r.text:
            return r.text
    except requests.RequestException:
        pass
    return None


def fetch_page_html(model_path: str) -> str | None:
    try:
        r = requests.get(f"{BASE}{model_path}", headers=HEADERS, timeout=30)
        if r.status_code == 200:
            return r.text
    except requests.RequestException:
        pass
    return None


def fetch_page_playwright(model_path: str) -> str | None:
    """Fallback: full browser render. Grabs the visible text of the page."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return None
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page(user_agent=HEADERS["User-Agent"])
            page.goto(f"{BASE}{model_path}", wait_until="networkidle", timeout=60000)
            # anchor on the Result section that holds the pricing sentence
            try:
                page.wait_for_selector("text=What would you like to do next?", timeout=15000)
            except Exception:
                pass
            text = page.inner_text("body")
            browser.close()
            return text
    except Exception:
        return None

# --------------------------------------------------------------------------- #
# Extraction: pull the pricing sentence out of whatever we fetched
# --------------------------------------------------------------------------- #
PRICING_CUES = re.compile(
    r"(you will be charged|your request\s+will cost|for every second of)", re.I)

def strip_html(raw: str) -> str:
    raw = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", raw, flags=re.S | re.I)
    raw = re.sub(r"<[^>]+>", " ", raw)
    return html.unescape(re.sub(r"\s+", " ", raw))


def extract_pricing_text(model_path: str) -> tuple[str, str]:
    """Returns (status, pricing_text)."""
    # 1) llms.txt — cleanest source
    llms = fetch_llms_txt(model_path)
    if llms:
        m = re.search(r"## Pricing\s*(.+?)\s*(?:For more details|\n## )", llms, re.S)
        if m:
            txt = re.sub(r"\*\*", "", re.sub(r"\s+", " ", m.group(1))).strip()
            if txt:
                return "ok", txt

    # 2) raw HTML — pricing sentence is server-rendered right after the
    #    "What would you like to do next?" block
    page = fetch_page_html(model_path)
    if page is None:
        text = fetch_page_playwright(model_path)          # 3) browser fallback
        if text is None:
            return "fetch_failed", ""
    else:
        text = strip_html(page)

    if re.search(r"Early access.*not yet generally available", text, re.I):
        return "early_access", "Early access — no public pricing shown."

    # take the region after the Result anchor, then the first pricing-cue sentence(s)
    anchor = text.find("What would you like to do next?")
    region = text[anchor:anchor + 4000] if anchor != -1 else text
    m = PRICING_CUES.search(region)
    if not m:
        m = PRICING_CUES.search(text)
        region = text
    if not m:
        return "no_pricing_found", ""
    start = m.start()
    # pricing blurb ends where the page's next section ("Logs") begins
    end = region.find("Logs", start)
    blurb = region[start:end if end != -1 else start + 900].strip()
    return "ok", re.sub(r"\s+", " ", blurb)

# --------------------------------------------------------------------------- #
# Parsing: turn the sentence into structured price points
# --------------------------------------------------------------------------- #
MONEY = r"\$([0-9]*\.?[0-9]+)"

def parse_price_points(text: str) -> list[dict]:
    """Handles every phrasing observed on fal so far. Order of checks matters."""
    pts: list[dict] = []
    t = text

    # Kling tiered audio pricing: "$X (audio off) or $Y (audio on) ... $Z"
    m = re.search(MONEY + r"\s*\(audio off\)\s*or\s*" + MONEY + r"\s*\(audio on\)", t, re.I)
    if m:
        pts.append({"label": "audio off", "usd_per_second": float(m.group(1))})
        pts.append({"label": "audio on",  "usd_per_second": float(m.group(2))})
        v = re.search(r"voice control[^$]*" + MONEY, t, re.I)
        if v:
            pts.append({"label": "voice ctrl", "usd_per_second": float(v.group(1))})
        return pts

    # Flat rate: "$X regardless of whether audio is on or off"
    m = re.search(MONEY + r"[^.$]*regardless of whether audio", t, re.I)
    if m:
        return [{"label": "flat", "usd_per_second": float(m.group(1))}]

    # Per-image endpoints (Wan 2.7 edit / pro edit)
    m = re.search(MONEY + r"\s*per\s+image", t, re.I)
    if m:
        return [{"label": "per image", "usd_per_image": float(m.group(1))}]

    # Resolution-keyed rates. Pattern order matters, and gaps must not cross
    # another "$" (a different price) or a comma (a different resolution clause):
    #   c) "every second of 720p video ... charged $X"
    #   b) "$X per second for 720p"  /  "$X/second for 720p"
    #   a) "For 1080p ... will cost $X per second"
    for res in ("480p", "720p", "1080p"):
        pat_c = re.compile(r"second of\s+" + res + r"[^$]{0,120}?" + MONEY, re.I)
        pat_b = re.compile(MONEY + r"\s*(?:/|per\s+)second\s+for\s+" + res, re.I)
        pat_a = re.compile(res + r"[^$,]{0,80}?" + MONEY + r"\s*(?:/|per\s+)second", re.I)
        for pat in (pat_c, pat_b, pat_a):
            m = pat.search(t)
            if m and not any(p["label"] == res.lower() for p in pts):
                pts.append({"label": res.lower(), "usd_per_second": float(m.group(1))})
                break

    # Video-input discounted rate (Seedance R2V): "With video input(s) ... $X per second"
    m = re.search(r"video inputs?[^$]{0,200}?" + MONEY + r"\s*(?:/|per\s*)second", t, re.I)
    if m:
        pts.append({"label": "with video input", "usd_per_second": float(m.group(1))})

    # Bare "$X per second" if nothing else matched
    if not pts:
        m = re.search(MONEY + r"\s*(?:/|per\s+)second", t, re.I)
        if m:
            pts.append({"label": "per second", "usd_per_second": float(m.group(1))})

    return pts

# --------------------------------------------------------------------------- #
# Snapshot, diff, history
# --------------------------------------------------------------------------- #
def latest_snapshot() -> dict | None:
    files = sorted(DATA_DIR.glob("pricing_*.json"))
    if not files:
        return None
    return json.loads(files[-1].read_text())


def diff_snapshots(old: dict | None, new: dict) -> list[str]:
    if old is None:
        return []
    changes = []
    old_map = {e["id"]: e for e in old["endpoints"]}
    for e in new["endpoints"]:
        prev = old_map.get(e["id"])
        if prev is None:
            changes.append(f"NEW ENDPOINT  {e['id']}")
            continue
        if prev.get("status") != e.get("status"):
            changes.append(f"STATUS CHANGE {e['id']}: {prev.get('status')} -> {e.get('status')}")
        old_pts = {p["label"]: p for p in prev.get("price_points", [])}
        for p in e.get("price_points", []):
            op = old_pts.get(p["label"])
            key = "usd_per_second" if "usd_per_second" in p else "usd_per_image"
            if op and op.get(key) != p.get(key):
                changes.append(f"PRICE CHANGE  {e['id']} [{p['label']}]: "
                               f"${op.get(key)} -> ${p.get(key)}")
    return changes


def append_history(snapshot: dict) -> None:
    hist = DATA_DIR / "history.csv"
    new_file = not hist.exists()
    with hist.open("a", newline="") as f:
        w = csv.writer(f)
        if new_file:
            w.writerow(["date", "endpoint_id", "company", "model", "category",
                        "status", "price_label", "usd_per_second", "usd_per_image"])
        for e in snapshot["endpoints"]:
            if not e["price_points"]:
                w.writerow([snapshot["as_of"], e["id"], e["company"], e["model"],
                            e["category"], e["status"], "", "", ""])
            for p in e["price_points"]:
                w.writerow([snapshot["as_of"], e["id"], e["company"], e["model"],
                            e["category"], e["status"], p["label"],
                            p.get("usd_per_second", ""), p.get("usd_per_image", "")])

# --------------------------------------------------------------------------- #
# Dashboard rendering
# --------------------------------------------------------------------------- #
def render_dashboard(snapshot: dict) -> None:
    template = DASHBOARD_TEMPLATE.read_text()
    payload = json.dumps(snapshot, indent=None)
    DASHBOARD_OUT.write_text(template.replace("/*__PRICING_DATA__*/null",
                                              payload, 1))
    print(f"dashboard written -> {DASHBOARD_OUT}")

# --------------------------------------------------------------------------- #
def run(render_only: bool = False) -> None:
    DATA_DIR.mkdir(exist_ok=True)
    if render_only:
        snap = latest_snapshot()
        if snap is None:
            sys.exit("no snapshot in data/ to render")
        render_dashboard(snap)
        return

    today = datetime.date.today().isoformat()
    endpoints = []
    for path, company, model, category in ENDPOINTS:
        status, text = extract_pricing_text(path)
        pts = parse_price_points(text) if status == "ok" else []
        if status == "ok" and not pts:
            status = "parse_failed"
        # per-image endpoints keep their warning status
        if pts and "usd_per_image" in pts[0]:
            status = "image_endpoint"
            text += " NOTE: this endpoint is IMAGE editing, not video editing."
        endpoints.append({"id": path, "company": company, "model": model,
                          "category": category, "url": f"{BASE}{path}",
                          "status": status, "pricing_text": text,
                          "price_points": pts})
        print(f"[{status:>14}] {path}")
        time.sleep(1.0)          # be polite to fal

    snapshot = {"as_of": today,
                "source": "fal.ai model pages (Result-section pricing text)",
                "endpoints": endpoints}

    prev = latest_snapshot()
    out = DATA_DIR / f"pricing_{today}.json"
    out.write_text(json.dumps(snapshot, indent=2))
    print(f"snapshot written -> {out}")

    changes = diff_snapshots(prev, snapshot)
    if changes:
        print("\n=== CHANGES SINCE LAST SNAPSHOT ===")
        for c in changes:
            print("  " + c)
        (DATA_DIR / f"changes_{today}.txt").write_text("\n".join(changes))
    else:
        print("no pricing changes vs previous snapshot" if prev else "first snapshot")

    append_history(snapshot)
    render_dashboard(snapshot)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--render-only", action="store_true",
                    help="rebuild dashboard.html from the latest snapshot without fetching")
    args = ap.parse_args()
    run(render_only=args.render_only)
