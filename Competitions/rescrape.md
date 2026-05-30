# Rescrape Schedule — Competitions

**Recommended: Every 2 weeks (bi-weekly)**

## Reasoning

Competitions have the **fastest churn** of all Activity Finder sections:

1. **Deadlines expire frequently** — From the market research, ~15-20 competitions
   have deadlines within any 2-week window (Apr 17-30 alone had 13 imminent deadlines).
   After deadline passes, the listing becomes stale.

2. **New competitions appear constantly** — Hackathons on Devpost launch weekly.
   Unstop has new listings every few days. StudentCompetitions refreshes weekly.

3. **103 competitions found** (April 2026), but only ~90-110 have active registration
   at any time. The pool rotates — old ones close, new ones open.

4. **Rolling/always-open competitions exist** (CodeChef, MyGov, India Genius) —
   these don't need rescraping, but their surrounding listings do.

5. **Seasonal clusters** — Competition activity peaks in:
   - April-May (pre-summer deadlines)
   - August-September (new academic year launches)
   - October-November (year-end competitions)
   Missing a 2-week window means missing an entire batch.

## Why NOT weekly?
- 103 pages × BrowserOS extraction = ~30-45 min per run
- Weekly is feasible but marginal benefit — most competitions have 2-4 week windows
- Bi-weekly catches 95%+ of new competitions before they expire

## Why NOT monthly?
- ~30-40 competitions would expire between scrapes
- Students would see expired listings for 2+ weeks — bad UX
- New hackathons on Devpost would be missed entirely

## Rescrape plan

| Step | What | Frequency | Time |
|------|------|-----------|------|
| 1. Discovery | Run discover_devpost.py + discover_studentcomp.py | Bi-weekly | ~5 min |
| 2. Deep research | Run deep_research_comp.txt in new session | Monthly | ~30 min |
| 3. Extraction | Run browser_extract.py on new input JSONs | Bi-weekly | ~30 min |
| 4. Filter | Run filter_hs_only.py | After each extraction | ~1 min |
| 5. Expiry check | Remove competitions with deadline < today | Bi-weekly | ~1 min |
