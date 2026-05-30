"""
compute_professor_coverage.py

Scans professors_info/output_professors_13k_final/universities/ and computes
coverage metrics for the Professors dashboard tab.

Output: dashboard/professor_coverage.json
"""

import json
import shutil
import sys
import io
from pathlib import Path
from datetime import datetime
from collections import Counter, defaultdict

if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True)

BASE = Path(r"c:\Users\HP\OneDrive\Desktop\course_data")
RUN_ROOT = BASE / "professors_info" / "output_professors_13k_final"
ROOT = RUN_ROOT / "universities"
STATE_FILE = RUN_ROOT / "state.jsonl"
INPUT_CSV = BASE / "professors_info" / "config" / "universities_top_450.csv"
OUTPUT = BASE / "dashboard" / "professor_coverage.json"

PROF_FIELDS = [
    "name", "role", "email", "research_area",
    "profile_url", "lab_url",
    "personal_website_url", "personal_website_content_file",
]


def has_value(v):
    if v is None: return False
    if isinstance(v, str) and not v.strip(): return False
    if isinstance(v, (list, dict)) and len(v) == 0: return False
    return True


def main():
    if not ROOT.exists():
        print(f"ERROR: {ROOT} does not exist")
        return

    statuses = Counter()
    strategies = Counter()
    skip_reasons = Counter()
    countries = Counter()
    country_profs = Counter()
    dept_files = Counter()
    dept_ok = Counter()
    dept_profs = Counter()
    dept_sum_profs_for_avg = defaultdict(int)
    dept_files_with_profs = Counter()
    uni_total_profs = {}
    uni_country = {}
    field_counts = {f: 0 for f in PROF_FIELDS}

    total_profs = 0
    total_files = 0
    total_unis = 0
    unis_with_data = 0
    profs_with_personal_url = 0
    profs_with_personal_captured = 0

    unis = sorted([d for d in ROOT.iterdir() if d.is_dir()])
    total_unis = len(unis)
    print(f"Scanning {total_unis} universities...")

    for i, uni in enumerate(unis):
        if i and i % 50 == 0:
            print(f"  {i}/{total_unis}...")
        uni_profs = 0
        for f in uni.iterdir():
            if not f.is_file() or f.suffix != ".json":
                continue
            total_files += 1
            dept = f.stem
            dept_files[dept] += 1
            try:
                d = json.loads(f.read_text(encoding="utf-8"))
            except Exception:
                continue

            s = d.get("status") or "unknown"
            statuses[s] += 1
            ss = d.get("selection_strategy") or "none"
            strategies[ss] += 1
            sr = d.get("skip_reason")
            if sr:
                short = sr.strip()
                if len(short) > 80:
                    short = short[:77] + "..."
                skip_reasons[short] += 1

            c = d.get("country")
            if c:
                uni_country[uni.name] = c

            profs = d.get("professors") or []
            if profs:
                dept_files_with_profs[dept] += 1
            dept_profs[dept] += len(profs)
            if s in ("ok", "ok_via_homepage_navigation"):
                dept_ok[dept] += 1

            for p in profs:
                total_profs += 1
                uni_profs += 1
                for fld in PROF_FIELDS:
                    if has_value(p.get(fld)):
                        field_counts[fld] += 1
                if has_value(p.get("personal_website_url")):
                    profs_with_personal_url += 1
                if has_value(p.get("personal_website_content_file")):
                    profs_with_personal_captured += 1

        uni_total_profs[uni.name] = uni_profs
        if uni_profs > 0:
            unis_with_data += 1

    for u, c in uni_country.items():
        countries[c] += 1
        country_profs[c] += uni_total_profs.get(u, 0)

    # ── Parse state.jsonl just for top-level stats (errors/parse_errors) ──
    import re as _re
    state_status = Counter()
    state_pairs = set()
    state_unis = set()
    state_ok_unis = set()
    if STATE_FILE.exists():
        unique_status = {}
        with open(STATE_FILE, encoding="utf-8") as f:
            for line in f:
                try:
                    d = json.loads(line)
                    pid = d.get("pair_id")
                    if pid:
                        state_pairs.add(pid)
                        unique_status[pid] = d.get("status") or ""
                except Exception:
                    pass
        for pid, st in unique_status.items():
            state_status[st] += 1
            uni = pid.split("__")[0] if "__" in pid else pid
            state_unis.add(uni)
            if st in ("ok", "ok_via_homepage_navigation"):
                state_ok_unis.add(uni)
        print(f"\nstate.jsonl: {len(state_pairs):,} pairs across {len(state_unis):,} unis")
        for st, c in state_status.most_common():
            print(f"  {st}: {c:,}")

    # ── Per-dept 3-bucket classification — DISK-ONLY scan of the 408 unis ──
    # For each of the 408 unis-with-data, check each of the 30 dept files:
    #   green = file exists AND has profs (or status=ok with no skip)
    #   blue  = file exists, skipped, skip_reason matches "doesn't exist"
    #   yellow = file missing (errored) OR skipped with other reason
    DOESNT_EXIST_RE = _re.compile(
        r"\bdoes not have\b|\bhas no\b|\bno such\b|\bnot have a\b|"
        r"\bnot exist\b|\bnon[\s-]?existent\b|\bdoesn'?t have\b|"
        r"\bdoes not exist\b|\bno standalone\b",
        _re.I,
    )
    DEPT_FOLDER_NAMES = sorted(dept_files.keys())
    unis_with_data_set = set(u for u, p in uni_total_profs.items() if p > 0)
    dept_bucket = {d: Counter() for d in DEPT_FOLDER_NAMES}
    for uni in unis_with_data_set:
        uni_folder = ROOT / uni
        for dept_name in DEPT_FOLDER_NAMES:
            fpath = uni_folder / f"{dept_name}.json"
            if not fpath.exists():
                dept_bucket[dept_name]["failed"] += 1
                continue
            try:
                d = json.loads(fpath.read_text(encoding="utf-8"))
            except Exception:
                dept_bucket[dept_name]["failed"] += 1
                continue
            profs = d.get("professors") or []
            sr = d.get("skip_reason") or ""
            st = d.get("status") or ""
            if len(profs) > 0 or st in ("ok", "ok_via_homepage_navigation"):
                dept_bucket[dept_name]["ok"] += 1
            elif DOESNT_EXIST_RE.search(sr):
                dept_bucket[dept_name]["dne"] += 1
            else:
                dept_bucket[dept_name]["failed"] += 1

    total_g = sum(b["ok"] for b in dept_bucket.values())
    total_b = sum(b["dne"] for b in dept_bucket.values())
    total_y = sum(b["failed"] for b in dept_bucket.values())
    print(f"\nPer-dept disk scan (408 unis × 30 depts = {408*30}):")
    print(f"  Green (extracted):    {total_g:,}")
    print(f"  Blue  (dept absent):  {total_b:,}")
    print(f"  Yellow (failed/none): {total_y:,}")
    print(f"  TOTAL:                {total_g+total_b+total_y:,}")

    # ── Build output ──────────────────────────────────────────────────
    ok_count = statuses.get("ok", 0) + statuses.get("ok_via_homepage_navigation", 0)
    skipped_count = statuses.get("skipped", 0)

    # Authoritative attempt counts from state.jsonl (fall back to disk)
    total_attempts = len(state_pairs) if state_pairs else total_files
    input_universities = len(state_unis) if state_unis else total_unis
    unis_with_ok = len(state_ok_unis) if state_ok_unis else unis_with_data
    error_count = state_status.get("error", 0)
    parse_error_count = state_status.get("parse_error", 0)
    success_pct_attempts = round(ok_count / total_attempts * 100, 1) if total_attempts else 0

    # Per-uni max/min/median (only unis with profs > 0)
    uni_profs_sorted = sorted([(u, p) for u, p in uni_total_profs.items() if p > 0],
                              key=lambda x: -x[1])
    uni_max = uni_profs_sorted[0] if uni_profs_sorted else (None, 0)
    uni_min = uni_profs_sorted[-1] if uni_profs_sorted else (None, 0)
    uni_median = uni_profs_sorted[len(uni_profs_sorted) // 2] if uni_profs_sorted else (None, 0)

    # ── Regional coverage from the input CSV ──────────────────────────
    import csv, re
    def _norm(s):
        s = s.encode('ascii', 'ignore').decode('ascii')
        return re.sub(r'[^a-zA-Z0-9]', '', s).lower()

    folder_idx = {_norm(u): u for u in uni_total_profs}
    region_input = Counter()
    region_folder = Counter()
    region_with_data = Counter()
    region_profs = Counter()
    if INPUT_CSV.exists():
        with open(INPUT_CSV, encoding="utf-8") as f:
            for row in csv.DictReader(f):
                r = row.get("region", "Unknown")
                region_input[r] += 1
                match = folder_idx.get(_norm(row.get("university_name", "")))
                if match:
                    region_folder[r] += 1
                    p = uni_total_profs.get(match, 0)
                    if p > 0:
                        region_with_data[r] += 1
                        region_profs[r] += p

    REGION_ORDER = ["US", "Europe", "Others"]
    REGION_LABELS = {"US": "United States", "Europe": "Europe", "Others": "Others (Asia & Rest)"}
    regional_coverage = []
    for r in REGION_ORDER:
        if r not in region_input: continue
        regional_coverage.append({
            "region": r,
            "label": REGION_LABELS.get(r, r),
            "input": region_input[r],
            "folder_created": region_folder[r],
            "with_profs": region_with_data[r],
            "profs": region_profs[r],
            "coverage_pct": round(region_with_data[r] / region_input[r] * 100, 1) if region_input[r] else 0,
        })
    print("\nRegional coverage:")
    for rc in regional_coverage:
        print(f"  {rc['label']:<22s}  input={rc['input']:>3}  with_profs={rc['with_profs']:>3}  profs={rc['profs']:>6,}  coverage={rc['coverage_pct']}%")
    print(f"  TOTAL profs across regions:  {sum(rc['profs'] for rc in regional_coverage):,}")

    field_coverage = []
    for fld in PROF_FIELDS:
        pct = round(field_counts[fld] / total_profs * 100, 1) if total_profs else 0
        field_coverage.append({"name": fld, "count": field_counts[fld], "pct": pct})

    dept_coverage = []
    for dept in sorted(dept_files.keys()):
        attempted = dept_files[dept]
        ok = dept_ok[dept]
        with_data = dept_files_with_profs[dept]
        profs = dept_profs[dept]
        success_pct = round(ok / attempted * 100, 1) if attempted else 0
        avg = round(profs / with_data, 1) if with_data else 0

        # 3-bucket classification — direct lookup since dept_bucket is keyed by folder name
        b = dept_bucket.get(dept, {})
        green = b.get("ok", 0)
        blue = b.get("dne", 0)
        yellow = b.get("failed", 0)
        bucket_total = green + blue + yellow

        dept_coverage.append({
            "dept": dept.replace("_", " "),
            "attempted": attempted,
            "ok": ok,
            "with_profs": with_data,
            "profs": profs,
            "success_pct": success_pct,
            "avg_per_file": avg,
            "bucket_ok": green,
            "bucket_dne": blue,
            "bucket_failed": yellow,
            "bucket_total": bucket_total,
        })
    dept_coverage.sort(key=lambda x: -x["profs"])

    country_coverage = []
    for c, n in countries.most_common():
        country_coverage.append({
            "country": c,
            "unis": n,
            "profs": country_profs[c],
        })

    top_unis = sorted(
        [{"name": u.replace("_", " "), "folder": u,
          "country": uni_country.get(u, "—"),
          "profs": p} for u, p in uni_total_profs.items() if p > 0],
        key=lambda x: -x["profs"],
    )[:20]

    skip_reasons_top = [{"reason": r, "count": n}
                        for r, n in skip_reasons.most_common(5)]

    output = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "summary": {
            "total_professors": total_profs,
            "total_universities": total_unis,  # uni folders on disk (e.g. 435)
            "input_universities": input_universities,  # 450
            "unis_with_data": unis_with_data,  # 408
            "unis_with_ok": unis_with_ok,  # 411 (from state.jsonl)
            "total_dept_files": total_files,
            "unique_departments": len(dept_files),  # 30
            "max_possible_pairs": len(dept_files) * input_universities,  # 30 × 450 = 13,500
            "total_attempts": total_attempts,  # 13,497 from state.jsonl
            "ok_count": ok_count,
            "skipped_count": skipped_count,
            "error_count": error_count,
            "parse_error_count": parse_error_count,
            "success_pct": round(ok_count / total_files * 100, 1) if total_files else 0,  # legacy
            "success_pct_of_attempts": success_pct_attempts,  # 52.2%
            "avg_profs_per_uni": round(total_profs / max(unis_with_ok, 1), 1),  # 582.6
            "avg_profs_per_uni_with_data": round(total_profs / max(unis_with_data, 1), 1),  # 586.9
            "uni_max_name": uni_max[0].replace("_", " ") if uni_max[0] else None,
            "uni_max_count": uni_max[1],
            "uni_min_name": uni_min[0].replace("_", " ") if uni_min[0] else None,
            "uni_min_count": uni_min[1],
            "uni_median_count": uni_median[1],
            "countries": len(countries),
        },
        "pipeline": {
            "statuses": dict(statuses),
            "strategies": dict(strategies),
            "skip_reasons_top": skip_reasons_top,
        },
        "field_coverage": field_coverage,
        "dept_coverage": dept_coverage,
        "country_coverage": country_coverage,
        "regional_coverage": regional_coverage,
        "top_universities": top_unis,
        "personal_sites": {
            "total_profs": total_profs,
            "with_url": profs_with_personal_url,
            "captured": profs_with_personal_captured,
            "url_pct": round(profs_with_personal_url / total_profs * 100, 1) if total_profs else 0,
            "capture_pct": round(profs_with_personal_captured / max(profs_with_personal_url, 1) * 100, 1),
        },
    }

    if OUTPUT.exists():
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup = OUTPUT.parent / f"professor_coverage.backup_{ts}.json"
        shutil.copy2(OUTPUT, backup)
        print(f"Backup: {backup.name}")

    OUTPUT.write_text(json.dumps(output, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nWrote {OUTPUT.name}")

    # ── Print summary ─────────────────────────────────────────────────
    s = output["summary"]
    print(f"\nSummary:")
    print(f"  Universities:       {s['unis_with_data']} / {s['total_universities']} have prof data")
    print(f"  Department files:   {s['total_dept_files']:,} across {s['unique_departments']} dept types")
    print(f"  Total professors:   {s['total_professors']:,}")
    print(f"  Extraction success: {s['ok_count']:,} ok / {s['skipped_count']:,} skipped ({s['success_pct']}%)")
    print(f"  Countries:          {s['countries']}")

    print(f"\nField coverage:")
    for fc in field_coverage:
        bar = "#" * max(1, int(fc["pct"] / 5))
        print(f"  {fc['name']:<34s} {fc['pct']:>5.1f}%  {bar}")

    print(f"\nDepartment coverage (top 10 by profs):")
    for dc in dept_coverage[:10]:
        print(f"  {dc['dept']:<32s} {dc['profs']:>6,} profs from {dc['with_profs']:>3}/{dc['attempted']:>3} files")

    print(f"\nTop countries:")
    for cc in country_coverage[:10]:
        print(f"  {cc['country']:<22s} {cc['unis']:>4} unis  {cc['profs']:>7,} profs")

    print(f"\nPersonal sites:")
    ps = output["personal_sites"]
    print(f"  {ps['with_url']:,} / {ps['total_profs']:,} profs had URL ({ps['url_pct']}%)")
    print(f"  {ps['captured']:,} / {ps['with_url']:,} captured to .md ({ps['capture_pct']}%)")


if __name__ == "__main__":
    main()
