# fal.ai Video Model Pricing Tracker

Tracks fal.ai list prices for Alibaba's video models (Happy Horse 1.0/1.1, Wan 2.6/2.7)
against Kling v3 and Seedance 2.0, across 35 endpoints in four categories
(T2V, I2V, R2V, Video Edit).

Each run: **fetch → parse → snapshot → diff vs last run → rebuild `dashboard.html`**.

## Files

| File | What it is |
|---|---|
| `tracker.py` | The whole pipeline. Endpoint registry lives at the top. |
| `dashboard_template.html` | Dashboard shell; tracker injects the latest snapshot into it. |
| `dashboard.html` | The deliverable — open in any browser, no server needed. |
| `data/pricing_YYYY-MM-DD.json` | One snapshot per run day. |
| `data/history.csv` | Long-format append-only log (one row per price point per day) — pandas-ready. |
| `data/changes_YYYY-MM-DD.txt` | Written only when a price or status changed vs the previous snapshot. |

## Setup (run locally — cloud runner IPs may see different content)

```bash
pip install requests
pip install playwright && playwright install chromium   # optional fallback only
python tracker.py
```

Playwright is a *fallback*: the pricing sentence is server-rendered, so plain
`requests` works. The primary source is each model's `/llms.txt` page
(append `/llms.txt` to any model URL), which has a clean `## Pricing` section
containing the exact sentence shown under "What would you like to do next?"
on the model page. Extraction order: llms.txt → raw HTML → Playwright.

`python tracker.py --render-only` rebuilds the dashboard from the latest
snapshot without hitting fal (useful after editing the template).

## Run it daily

macOS/Linux cron (9am):
```
0 9 * * * cd /path/to/fal-pricing-tracker && /usr/bin/python3 tracker.py >> data/run.log 2>&1
```
Windows: Task Scheduler → run `python tracker.py` in this folder daily.

When a price moves, the run prints `PRICE CHANGE ...` lines and writes
`data/changes_<date>.txt` — grep the log or just check whether that file exists.

## Sharing with your supervisor

`dashboard.html` is fully self-contained (data embedded, no dependencies beyond
Google Fonts, which degrade gracefully offline). Email it, drop it in a shared
drive, or host the folder on an internal static server — it always reflects the
latest run.

## Gotchas encoded in the tracker

- **Seedance 2.0 Mini text-to-video** is behind an early-access gate (no public
  price; B2B-outside-US terms). Recorded as `status: early_access`; the diff
  will flag the moment it goes GA.
- **`fal-ai/wan/v2.7/edit` and `/pro/edit` are excluded on purpose** — they
  are image-editing endpoints ($0.03 / $0.075 per image), not video edit.
  If fal ever recategorizes them, re-add via `ENDPOINTS`.
- Kling v3 Pro/Standard have three price tiers (audio off / on / voice control);
  Turbo and 4K tiers are flat. Seedance includes audio free but bills by tokens
  (`h·w·duration·24/1024`), with video inputs at 0.6× and input+output duration
  both billed on reference-to-video.
- Adding an endpoint = one line in `ENDPOINTS` at the top of `tracker.py`.
