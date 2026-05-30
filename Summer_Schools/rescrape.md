# Rescrape Schedule — Summer Schools

**Recommended: Twice a year (January + March)**

## Reasoning

Summer schools are the **most stable** section — programs recur annually with
predictable timelines:

1. **Programs are annual** — Harvard Pre-College, Ashoka YSP, ISRO YUVIKA run
   the same program every year. Only dates, fees, and deadlines change.

2. **Application windows are seasonal** — Most open Dec-Feb, close Mar-May,
   programs run Jun-Aug. The entire cycle is predictable.

3. **Very few new programs appear mid-year** — Universities announce new summer
   programs in October-November for the next summer. Once the season starts,
   the list is fixed.

4. **150+ programs expected** — Large enough to absorb a few closures without
   feeling stale.

## Why twice a year?

- **January scrape**: Catches all programs opening applications for summer 2026.
  This is the main discovery run — find all new programs, update dates/fees.

- **March scrape**: Final check before application season peaks. Catches:
  - Programs that opened late (Feb-Mar)
  - Updated deadlines (extensions, early closures)
  - New programs announced after January

## Why NOT quarterly or monthly?
- Summer programs don't change between March-December
- Once applications close (May-June), there's nothing to scrape until next year
- Quarterly wastes compute — 3 of 4 quarters have no changes

## Why NOT annually?
- Some programs change deadlines or fees between January and March
- A single January scrape might miss late-opening programs
- Two scrapes catches 99%+ of all programs

## Rescrape plan

| Step | What | When | Time |
|------|------|------|------|
| 1. Discovery | Run deep_research_summer.txt in new session | January | ~45 min |
| 2. Extraction | Run browser_extract.py on all input JSONs | January | ~1-2 hrs |
| 3. Final check | Re-run discovery + extraction for changes | March | ~30 min |
| 4. Diff review | Content team reviews only new/changed programs | March | ~1-2 hrs |
| 5. Archive | Mark past-deadline programs as closed | June | ~5 min |
