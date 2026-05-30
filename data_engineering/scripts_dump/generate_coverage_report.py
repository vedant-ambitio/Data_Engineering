import os
import re
import json
from pathlib import Path

def analyze_file(file_path):
    with open(file_path, 'r', encoding='utf-8') as f:
        content = f.read()

    patterns = {
        "Degree Type": r"\*\*Degree Type:\*\* (.*)",
        "Duration": r"\*\*Duration:\*\* (.*)",
        "Delivery Mode": r"\*\*Delivery Mode:\*\* (.*)",
        "Credits": r"\*\*Credits:\*\* (.*)",
        "Program page URL": r"\*\*Program page URL:\*\* (.*)",
        "GPA Requirements": r"### GPA Requirements\n(.*?)\n\n|\*\*GPA Requirements:\*\* (.*)",
        "GRE/GMAT Status": r"### Standardized Tests\n(.*?)\n\n|\*\*Standardized Tests\*\*:\n(.*?)\n\n",
        "Work Experience": r"### Work Experience\n(.*?)\n\n|\*\*Work Experience:\*\* (.*)",
        "TOEFL iBT": r"\*\*TOEFL iBT:\*\* (.*)",
        "IELTS Academic": r"\*\*IELTS Academic:\*\* (.*)",
        "PTE Academic": r"\*\*PTE Academic:\*\* (.*)",
        "Duolingo": r"\*\*Duolingo English Test (DET):\*\* (.*)|\*\*Duolingo:\*\* (.*)",
        "International Tuition": r"\* \*\*International Students:\*\* (.*)|\* \*\*Overseas Students\*\*:\s*(.*)",
        "Domestic Tuition": r"\* \*\*Domestic Students:\*\* (.*)|\* \*\*Home Students\*\*:\s*(.*)",
        "Application Fee": r"\*\*Application Fee:\*\* (.*)",
        "Fall Deadline": r"\* \*\*Fall \d{4} Entry\*\*:\s*(.*)",
        "Admissions Email": r"\*\*Admissions Email:\*\* (.*)",
        "Phone": r"\*\*Phone:\*\* (.*)",
        "Apply Now": r"\*\*Direct application portal / \"Apply Now\" link:\*\* (.*)",
        "Faculty Directory": r"\*\*Faculty directory page for the department:\*\* (.*)"
    }

    negative_indicators = ["information not available", "not explicitly mentioned", "not found", "not applicable", "not specified"]
    results = {}
    for field, pattern in patterns.items():
        match = re.search(pattern, content, re.IGNORECASE | re.DOTALL)
        if match:
            val = next((g for g in match.groups() if g is not None), "").strip()
            is_empty = any(re.search(ind, val, re.IGNORECASE) for ind in negative_indicators)
            results[field] = not is_empty
        else: results[field] = False

    sections = {
        "Curriculum Section": r"## Curriculum\n(.*?)\n\n",
        "Scholarships Section": r"## Scholarships & Financial Aid\n(.*?)\n\n",
        "Career Outcomes Section": r"## Career Outcomes\n(.*?)\n\n",
        "Admission Req Section": r"## Admission Requirements\n(.*?)\n\n"
    }
    for sec, pattern in sections.items():
        match = re.search(pattern, content, re.IGNORECASE | re.DOTALL)
        if match:
            val = match.group(1).strip()
            is_empty = any(re.search(ind, val, re.IGNORECASE) for ind in negative_indicators)
            results[sec] = not is_empty
        else: results[sec] = False
    return results

def run_analysis(folder_path, output_path):
    base_dir = Path(folder_path)
    groups = {
        "Program Essentials": {"section": "Admission Req Section", "fields": ["Degree Type", "Duration", "Delivery Mode", "Credits", "Program page URL"]},
        "Admission Requirements": {"section": "Admission Req Section", "fields": ["GPA Requirements", "GRE/GMAT Status", "Work Experience"]},
        "English Proficiency": {"section": None, "fields": ["TOEFL iBT", "IELTS Academic", "PTE Academic", "Duolingo"]},
        "Curriculum & Careers": {"section": "Curriculum Section", "fields": ["Curriculum Section", "Career Outcomes Section", "Scholarships Section"]},
        "Financials & Deadlines": {"section": None, "fields": ["International Tuition", "Domestic Tuition", "Application Fee", "Fall Deadline"]},
        "Direct Links & Contacts": {"section": None, "fields": ["Admissions Email", "Phone", "Apply Now", "Faculty Directory"]}
    }
    coverage_data = {g: {"total": 0, "section_has_val": 0, "fields": {f: 0 for f in data["fields"]}} for g, data in groups.items()}
    file_list = list(base_dir.rglob("*.md"))
    for file_path in file_list:
        if "__MACOSX" in str(file_path) or file_path.name.startswith("._"): continue
        res = analyze_file(file_path)
        for g, gdata in groups.items():
            coverage_data[g]["total"] += 1
            if gdata["section"] and res.get(gdata["section"]): coverage_data[g]["section_has_val"] += 1
            for f in gdata["fields"]:
                if res.get(f): coverage_data[g]["fields"][f] += 1
    
    final_output = []
    for g, data in coverage_data.items():
        total = data["total"]
        final_output.append({
            "group": g, "section_pct": round((data["section_has_val"] / total) * 100, 1) if total > 0 else 0,
            "fields": [{"name": f, "pct": round((data["fields"][f] / total) * 100, 1) if total > 0 else 0} for f in data["fields"]]
        })
    with open(output_path, "w", encoding="utf-8") as f: json.dump(final_output, f, indent=2)

if __name__ == "__main__":
    BASE = r"C:\Users\HP\OneDrive\Desktop\course_data"
    run_analysis(os.path.join(BASE, "masters_data", "masters"), os.path.join(BASE, "dashboard", "masters_coverage.json"))
