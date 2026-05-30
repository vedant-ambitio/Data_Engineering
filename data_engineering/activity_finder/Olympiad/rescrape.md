# Rescrape Schedule — Olympiads

**Recommended: Annually (August) + one mid-year check (January)**

## Reasoning

Olympiads are the **most stable** section — a fixed universe of 56 olympiads
that repeat every year:

1. **The list doesn't change** — IMO, IPhO, SOF NSO, Silverzone iOM — these
   are the same 56 every year. No new olympiads appear mid-year. No olympiads
   disappear suddenly.

2. **Only dates and fees change** — Registration dates, exam dates, and
   occasionally fees update for the new academic year. The descriptions,
   eligibility, structure, rewards stay identical year to year.

3. **Registration opens Aug-Sep** — SOF, Silverzone, HBCSE, Unified Council
   all announce 2026-27 dates in August-September. This is when the 17
   missing registration_close_dates will be filled.

4. **56 olympiads = small set** — Annual full rescrape takes ~30 min with
   BrowserOS. Not a resource concern.

5. **Current data is 94.6% filled** — Only registration_close_date (69.6%)
   and rewards_outcomes (78.6%) have notable gaps. Everything else is 94%+.

## Why annually in August?
- SOF publishes exam schedule in July-August
- Silverzone publishes olympiad dates in August
- HBCSE announces NSE/INO dates in August-September
- Unified Council announces NSTSE/UCO dates in August
- By September, all 56 olympiads have their 2026-27 dates published

## Why a January check?
- Some olympiads update fees or eligibility criteria in January
- HBCSE publishes exact exam dates for INxO (stage 2) in December-January
- International olympiads (IMO, IPhO, IChO) announce host country + dates
- Quick diff check — only update changed fields, not full rescrape

## Why NOT quarterly or monthly?
- Olympiad data doesn't change between September and July
- Monthly scraping of 56 pages = wasted compute for zero new data
- The only gap (registration dates) fills itself in August

## Rescrape plan

| Step | What | When | Time |
|------|------|------|------|
| 1. Full rescrape | Run browser_extract.py on all 56 official URLs | August | ~30 min |
| 2. Date patch | Extract registration_close_date + exam_date for all | August | ~20 min |
| 3. Merge | Update raw JSONs with new dates/fees | August | ~5 min |
| 4. Mid-year check | Re-run on HBCSE + international olympiads only | January | ~10 min |
| 5. Postprocess | Run postprocess_raw.py to reorder fields | After any update | ~1 min |

## What changes annually vs what stays same

| Changes every year | Stays same |
|---|---|
| registration_close_date | activity_name |
| exam_date | organizer |
| registration_open_date | subject, level |
| cost (occasionally) | about_description |
| result_date | eligibility_text |
| | structure_format |
| | how_to_apply |
| | entry_route, modality |
| | official_website |
