"""
Patch deadline_status for 36 olympiads that have missing/null deadline fields.
Only adds deadline_status — no deadline_note.
"""

import json
import os
import glob

RAW_DIR = os.path.join(os.path.dirname(__file__), "olympiad_data", "raw")

# Verified statuses from web research
PATCHES = {
    # SOF olympiads — registration opens Aug-Sep each year via schools
    "SOF_NSO": "registration_opens_aug_sep_2026",
    "SOF_IMO": "registration_opens_aug_sep_2026",
    "SOF_IEO": "registration_opens_aug_sep_2026",
    "SOF_NCO": "registration_opens_aug_sep_2026",
    "SOF_IGKO": "registration_opens_aug_sep_2026",
    "SOF_ICO": "registration_opens_aug_sep_2026",
    "SOF_ISSO": "registration_opens_aug_sep_2026",

    # Silverzone — registration opens Aug-Sep each year via schools
    "SZ_iIO": "registration_opens_aug_sep_2026",
    "SZ_iOM": "registration_opens_aug_sep_2026",
    "SZ_iOS": "registration_opens_aug_sep_2026",
    "SZ_iOEL": "registration_opens_aug_sep_2026",
    "SZ_SKGKO": "registration_opens_aug_sep_2026",

    # HBCSE/NSE stage-1 — registration opens Aug-Sep via schools
    "INPhO": "registration_opens_aug_sep_2026",
    "INChO": "registration_opens_aug_sep_2026",
    "INBO": "registration_opens_aug_sep_2026",
    "INAO": "registration_opens_aug_sep_2026",

    # International olympiads — qualify via national olympiad, no direct registration
    "IPhO": "register_via_national_olympiad",
    "IChO": "register_via_national_olympiad",
    "IBO": "register_via_national_olympiad",
    "IAAO": "register_via_national_olympiad",
    "IMO": "register_via_national_olympiad",
    "IOI": "register_via_national_olympiad",
    "IJSO": "register_via_national_olympiad",
    "IOL": "register_via_national_olympiad",
    "IEO_INTL": "register_via_national_olympiad",
    "IOAA": "register_via_national_olympiad",
    "RMO": "register_via_national_olympiad",

    # CREST — rolling registration, always open
    "CREST_CSO": "registration_open_ongoing",
    "CREST_CMO": "registration_open_ongoing",
    "CREST_CEO": "registration_open_ongoing",
    "CREST_CCO": "registration_open_ongoing",

    # Unified Council — dates not yet announced for 2026
    "UC_NSTSE": "yet_to_be_announced",
    "UC_UCO": "yet_to_be_announced",
    "UC_UIMO": "yet_to_be_announced",

    # HBCSE/NSE stage-1 science exams — registration opens Aug-Sep
    "NSEA": "registration_opens_aug_sep_2026",
    "NSEB": "registration_opens_aug_sep_2026",
    "NSEC": "registration_opens_aug_sep_2026",
    "NSEP": "registration_opens_aug_sep_2026",
    "NSEJS": "registration_opens_aug_sep_2026",

    # Math path — IOQM registration opens Aug-Sep
    "IOQM": "registration_opens_aug_sep_2026",
    "INMO": "register_via_national_olympiad",

    # Indian selection stages
    "INJSO": "registration_opens_aug_sep_2026",

    # More international — qualify via national
    "IEarthSO": "register_via_national_olympiad",
    "IGeO": "register_via_national_olympiad",
    "IPO": "register_via_national_olympiad",
    "PaIO": "register_via_national_olympiad",
    "IOAI": "register_via_national_olympiad",

    # SOF additional
    "SOF_ICSO": "registration_opens_aug_sep_2026",

    # UC additional
    "UC_UIEO": "yet_to_be_announced",

    # NMTC (AMTI) — typically Aug-Oct registration
    "NMTC_JR": "registration_opens_aug_sep_2026",
    "NMTC_INTER": "registration_opens_aug_sep_2026",
    "NMTC_SR": "registration_opens_aug_sep_2026",

    # Others
    "HBCSE_NSO": "yet_to_be_announced",
    "INOI": "register_via_national_olympiad",
    "IAPT": "yet_to_be_announced",
    "ICAS": "yet_to_be_announced",
    "IHO": "no_2026_event",

    # Poor quality (5) — still patch for completeness
    "AI4ALL": "yet_to_be_announced",
    "CBSE_HC": "yet_to_be_announced",
    "CBSE_SC": "yet_to_be_announced",
    "IJMO": "yet_to_be_announced",
}


def find_file_for_olympiad(olympiad_id):
    """Find the JSON file matching this olympiad ID (exact match)."""
    fpath = os.path.join(RAW_DIR, f"{olympiad_id}.json")
    return fpath if os.path.exists(fpath) else None


def patch():
    patched = 0
    skipped = []

    for oid, status in PATCHES.items():
        fpath = find_file_for_olympiad(oid)
        if not fpath:
            skipped.append(oid)
            continue

        with open(fpath, "r", encoding="utf-8") as f:
            data = json.load(f)

        data["deadline_status"] = status

        with open(fpath, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

        patched += 1
        print(f"  PATCHED: {os.path.basename(fpath)} -> {status}")

    print(f"\nDone: {patched} patched, {len(skipped)} skipped")
    if skipped:
        print(f"Skipped (no file found): {skipped}")


if __name__ == "__main__":
    patch()
