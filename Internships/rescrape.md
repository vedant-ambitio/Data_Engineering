# Rescrape Schedule — Internships

**Recommended: Monthly**

## Reasoning

Internships have **moderate churn** — faster than volunteering, slower than
competitions:

1. **Internship listings expire in 2-4 weeks** — Most Internshala listings
   close within 3-4 weeks of posting. Company career pages update monthly.

2. **New listings appear weekly on Internshala** — But the high-school-eligible
   pool is small (~39 at any time). Weekly scraping would re-fetch the same
   35 listings with only 3-4 new ones.

3. **The market is genuinely small** — Only ~50-65 real internships for class
   8-12 at any given time. Monthly scraping catches ~10-15 new + removes
   ~10-15 expired. The pool stays fresh.

4. **Seasonal patterns** — Internship availability peaks during:
   - Summer (May-July) — summer internship programs
   - Winter (Dec-Jan) — winter internship batches
   Monthly scraping catches both peaks.

5. **Scam listings appear regularly** — Monthly review allows content team
   to filter out "paid certificate" scams that appear on Internshala.

## Why NOT weekly?
- Only ~3-5 new HS listings per week on Internshala
- Diminishing returns — scraping 50 pages weekly for 3-5 new results
- Content team review queue would be too frequent

## Why NOT quarterly?
- ~30-40 internships would expire between scrapes
- Summer internship window (May-July) is only 3 months — quarterly
  scraping might miss the entire peak season
- Students checking monthly would see stale listings

## Rescrape plan

| Step | What | When | Time |
|------|------|------|------|
| 1. Internshala scrape | Scrape HS-filtered listings | Monthly (1st week) | ~15 min |
| 2. Deep research | Run deep_research_internships.txt for new sources | Quarterly | ~30 min |
| 3. Extraction | Run browser_extract.py on new input JSONs | Monthly | ~30 min |
| 4. Scam filter | Review new listings for certificate mills | Monthly | ~15 min |
| 5. Expiry check | Remove listings with closed applications | Monthly | ~5 min |
| 6. Content review | Team approves before publish (value-for-money filter) | Monthly | ~30 min |
