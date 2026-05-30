# Rescrape Schedule — Volunteering

**Recommended: Quarterly (every 3 months)**

## Reasoning

Volunteering sits between competitions (fast churn) and summer schools (stable):

1. **Programs are ongoing, not event-based** — Unlike competitions with deadlines,
   most volunteering is "join anytime" or runs in cohorts. Teach For India,
   Robin Hood Army, Goonj — they accept volunteers year-round.

2. **New opportunities appear gradually** — NGOs launch new volunteer drives
   quarterly (seasonal campaigns, festivals, disaster response). Not weekly
   like hackathons, but not annually like summer schools.

3. **Platform listings are moderately stable** — iVolunteer.in refreshes
   listings monthly. VolunteerMatch updates weekly but the core opportunities
   stay the same. Catchafire projects rotate every few months.

4. **Seasonal peaks exist** — Volunteering activity peaks during:
   - Summer break (May-June) — students have time
   - Festival season (Oct-Nov) — NGO campaigns
   - Year-end (Dec-Jan) — annual drives
   Quarterly scraping catches all seasonal shifts.

5. **85-120 listings expected** — Stable enough that 10-15 expiring per quarter
   doesn't make the UI feel stale.

## Why NOT monthly?
- Most volunteering programs stay active for 3-6 months minimum
- Monthly scraping of 85-120 pages = effort with marginal new data
- iVolunteer/VolunteerMatch core listings don't change monthly

## Why NOT bi-annually?
- Seasonal campaigns (disaster relief, festival drives) would be missed
- Some NGO programs close after 3-4 months — would show stale listings
- New organizations joining platforms would be discovered 6 months late

## Rescrape plan

| Step | What | When | Time |
|------|------|------|------|
| 1. Discovery | Run deep_research_volunteering.txt | Quarterly (Jan, Apr, Jul, Oct) | ~30 min |
| 2. Extraction | Run browser_extract.py on input JSONs | After discovery | ~1 hr |
| 3. Age filter | Verify opportunities still accept under-18 | After extraction | ~10 min |
| 4. Expiry check | Remove closed/past opportunities | Quarterly | ~5 min |
| 5. Content review | Team reviews new listings before publish | Quarterly | ~1 hr |
