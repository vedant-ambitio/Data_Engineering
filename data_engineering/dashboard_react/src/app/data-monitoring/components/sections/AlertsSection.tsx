"use client";

import { useState, useMemo } from "react";
import type { ProgramData } from "../../types";
import { fmt } from "../../helpers";

type SectionStat = { no_data: number };
type CourseEntity = [string, string];
interface Course {
  n: string;
  s: number;
  l: string;
  t: string;
  d: string;
  a: string;
  e?: CourseEntity[];
  dr?: string;
  df?: string;
}
interface Uni {
  name: string;
  course_list: Course[];
}
type MatchedRow = { uni: string; name: string; score: number | string; label: string };

type AlertCard = {
  id: string;
  level: "critical" | "warning" | "info";
  title: string;
  desc: string;
  count: number | string;
};

function matchCourse(id: string, c: Course, uName: string): boolean {
  if (id === "al-tuition" && c.t === "F") return true;
  if (id === "al-toeflielts") {
    if (c.e && Array.isArray(c.e)) {
      if ((c.e[0] && c.e[0][0] === "F") || (c.e[1] && c.e[1][0] === "F")) return true;
    }
  }
  if (id === "al-prestige") {
    const pNames = ["harvard", "stanford", "oxford", "cambridge", "mit", "columbia", "eth", "imperial", "yale", "princeton"];
    if (c.s < 40 && pNames.some((p) => uName.toLowerCase().includes(p))) return true;
  }
  if (id === "al-reliableflag") {
    if (c.s >= 60 && (c.t === "F" || c.d === "F" || c.a === "F")) return true;
  }
  if (id === "al-multiadm") {
    if (c.e && Array.isArray(c.e)) {
      const mm = c.e.filter((e) => e[0] === "F").length;
      if (mm >= 3) return true;
    }
  }
  if (id === "al-stale" && c.d === "F" && c.dr && c.dr.includes("stale")) return true;
  if (id === "al-past" && c.dr && c.dr.includes("past")) return true;
  if (id === "al-zero" && c.s === 0) return true;
  if (id === "al-tuition-nodata" && c.t === "-") return true;
  if (id === "al-deadline-f2" && c.d === "F" && c.df === "F2_mismatch") return true;
  if (id === "al-deadline-f1" && c.df === "F1_stale") return true;
  return false;
}

export default function AlertsSection({ data, isUG }: { data: ProgramData; isUG: boolean }) {
  const [openAlert, setOpenAlert] = useState<string | null>(null);
  // Only one alert open at a time (matches original behavior)
  const toggleAlert = (id: string) => setOpenAlert((prev) => (prev === id ? null : id));

  const d = data;
  const a = (d.alerts || {}) as Record<string, number>;
  const s = (d.section_stats || {}) as Record<string, SectionStat>;

  type AlertsGroup = { title: string; cards: AlertCard[] };
  let groups: AlertsGroup[];
  let badges: { critical: number; warning: number; info: number };

  if (isUG) {
    badges = { critical: 2, warning: 2, info: 4 };
    const csvErrors = (d as unknown as { csv_errors_count?: number }).csv_errors_count || 0;
    const qualityFails = (d as unknown as { quality_failures_count?: number }).quality_failures_count || 0;
    const unclassified = (d as unknown as { unclassified_md_count?: number }).unclassified_md_count || 0;
    const missingCrawlUrls = (d as unknown as { missing_crawl_urls_count?: number }).missing_crawl_urls_count || 0;
    const missingCrawlPdf = (d as unknown as { missing_crawl_pdf_count?: number }).missing_crawl_pdf_count || 0;
    const missingCrawlNonpdf = (d as unknown as { missing_crawl_nonpdf_count?: number }).missing_crawl_nonpdf_count || 0;

    groups = [
      {
        title: "Tuition & Fees Alerts",
        cards: [
          { id: "al-tuition", level: "critical", title: "Tuition Amounts Flagged", desc: "Tuition data in .md does not match official university page", count: a.tuition_flagged || 0 },
          { id: "al-tuition-nodata", level: "warning", title: "Tuition No Data", desc: "Markdown files with empty tuition section", count: s.tuition_and_fees?.no_data || 0 },
        ],
      },
      {
        title: "Deadline Alerts",
        cards: [
          { id: "al-deadline-f2", level: "critical", title: "Deadline Mismatches (F2)", desc: "Dates don't match official page or zero matches", count: a.deadline_f2_mismatch || 0 },
          { id: "al-deadline-f1", level: "warning", title: "Stale Deadlines (F1)", desc: "Dates match but all in the past — needs re-scrape", count: a.deadline_f1_stale || 0 },
        ],
      },
      {
        title: "Pipeline Gaps",
        cards: [
          { id: "al-csv-errors", level: "info", title: "CSV Empty Fields", desc: "Rows in CSV with missing university/course name — skipped by scraper", count: csvErrors },
          { id: "al-quality-fail", level: "info", title: "Gemini Quality Check Failures", desc: "Scraper ran but output failed quality checks — no .md file created", count: qualityFails },
          { id: "al-unclassified", level: "info", title: "Unclassified Markdown Files", desc: "MD files exist but were not picked up by classification pipeline", count: unclassified },
          { id: "al-missing-urls", level: "info", title: "Uncrawled Official URLs", desc: `${fmt(missingCrawlPdf)} PDFs + ${fmt(missingCrawlNonpdf)} pages not yet crawled`, count: missingCrawlUrls },
        ],
      },
      {
        title: "Data Quality",
        cards: [{ id: "al-zero", level: "info", title: "Zero Confidence Files", desc: "Nothing could be verified in either section", count: a.zero_conf || 0 }],
      },
    ];
  } else {
    badges = { critical: 3, warning: 3, info: 3 };
    groups = [
      {
        title: "Critical",
        cards: [
          { id: "al-tuition", level: "critical", title: "Tuition Amounts Wrong", desc: "Tuition data in .md does not match official university page", count: a.tuition_flagged || 0 },
          { id: "al-toeflielts", level: "critical", title: "TOEFL/IELTS Score Mismatches", desc: `${fmt(a.toefl_mm || 0)} TOEFL + ${fmt(a.ielts_mm || 0)} IELTS don't match official pages`, count: a.toefl_ielts_mm || 0 },
          { id: "al-prestige", level: "critical", title: "Prestigious University Data Issues", desc: "High visibility programs with unreliable data", count: "--" },
        ],
      },
      {
        title: "Warning",
        cards: [
          { id: "al-reliableflag", level: "warning", title: "Reliable Files With Flagged Sections", desc: "Files scoring >= 60 with 1 flagged section", count: a.reliable_flagged || 0 },
          { id: "al-multiadm", level: "warning", title: "Multiple Admission Mismatches", desc: "3+ entities mismatched in single file", count: a.multi_adm_mm || 0 },
          { id: "al-stale", level: "warning", title: "Stale Deadlines", desc: "Website updated with newer dates", count: a.stale_deadlines || 0 },
        ],
      },
      {
        title: "Info",
        cards: [
          { id: "al-past", level: "info", title: "Past Deadlines", desc: "Dates matched but in the past", count: a.past_deadlines || 0 },
          { id: "al-uncrawled", level: "info", title: "Uncrawled Courses", desc: "Awaiting crawl data", count: a.uncrawled || 0 },
          { id: "al-zero", level: "info", title: "Zero Confidence Files", desc: "Nothing could be verified", count: a.zero_conf || 0 },
        ],
      },
    ];
  }

  // Compute matched rows for the currently open alert
  const matchedRows = useMemo<MatchedRow[]>(() => {
    if (!openAlert) return [];
    const rows: MatchedRow[] = [];

    // Special UG data lists
    const dRec = d as unknown as {
      csv_errors?: { uni: string; name: string }[];
      quality_failures?: { uni: string; name: string }[];
      unclassified_md?: { uni: string; name: string }[];
      missing_crawl_urls?: string[];
    };
    if (openAlert === "al-csv-errors" && dRec.csv_errors) {
      dRec.csv_errors.forEach((c) => rows.push({ uni: c.uni, name: c.name, score: "-", label: "review" }));
    }
    if (openAlert === "al-quality-fail" && dRec.quality_failures) {
      dRec.quality_failures.forEach((c) => rows.push({ uni: c.uni, name: c.name, score: "-", label: "review" }));
    }
    if (openAlert === "al-unclassified" && dRec.unclassified_md) {
      dRec.unclassified_md.forEach((c) => rows.push({ uni: c.uni, name: c.name, score: "-", label: "review" }));
    }
    if (openAlert === "al-missing-urls" && dRec.missing_crawl_urls) {
      dRec.missing_crawl_urls.forEach((url) => {
        const domain = url.split("/")[2] || "";
        rows.push({ uni: domain, name: url, score: "-", label: "review" });
      });
    }

    // Course-level matching
    const unis = (d.universities || []) as Uni[];
    unis.forEach((u) => {
      (u.course_list || []).forEach((c) => {
        if (matchCourse(openAlert, c, u.name)) {
          rows.push({ uni: u.name, name: c.n, score: c.s, label: c.l });
        }
      });
    });

    // Sort by score (numerical ascending; non-numerical first as a fallback)
    rows.sort((a, b) => {
      if (typeof a.score === "number" && typeof b.score === "number") return a.score - b.score;
      return 0;
    });
    return rows;
  }, [openAlert, d]);

  return (
    <>
      <div style={{ marginBottom: 20 }}>
        <span className="alert-badge critical">{badges.critical} Critical</span>
        <span className="alert-badge warning">{badges.warning} Warning</span>
        <span className="alert-badge info">{badges.info} Info</span>
      </div>
      {groups.map((g) => (
        <div key={g.title}>
          <div className="section-title">{g.title}</div>
          {g.cards.map((c) => {
            const isOpen = openAlert === c.id;
            return (
              <div key={c.id}>
                <div className={`alert-card ${c.level}`} onClick={() => toggleAlert(c.id)}>
                  <div>
                    <div className="alert-title">{c.title}</div>
                    <div className="alert-desc">{c.desc}</div>
                  </div>
                  <div className="alert-count">{typeof c.count === "number" ? fmt(c.count) : c.count}</div>
                </div>
                <div className={`alert-expand ${isOpen ? "open" : ""}`}>
                  {isOpen && (
                    <>
                      <div style={{ paddingTop: 8, fontSize: 11, color: "var(--gray)", marginBottom: 6 }}>
                        {matchedRows.length} {matchedRows.length === 1 ? "course" : "courses"}
                      </div>
                      {matchedRows.length === 0 && (
                        <div style={{ padding: "12px 0", fontSize: 12, color: "var(--gray)", fontStyle: "italic" }}>
                          No matching courses found for this alert.
                        </div>
                      )}
                      {matchedRows.slice(0, 100).map((row, i) => (
                        <div key={i} className="arow">
                          <span className="auni">{row.uni}</span>
                          <span className="acourse">{row.name}</span>
                          <span className="ascore">{row.score}</span>
                          <span className={`label-tag ${row.label}`} style={{ fontSize: 9, padding: "1px 6px", marginLeft: 6 }}>
                            {row.label}
                          </span>
                        </div>
                      ))}
                      {matchedRows.length > 100 && (
                        <div style={{ padding: "8px 0", fontSize: 11, color: "var(--gray)", textAlign: "center" }}>
                          Showing first 100 of {matchedRows.length} — open the original dashboard to see all.
                        </div>
                      )}
                    </>
                  )}
                </div>
              </div>
            );
          })}
        </div>
      ))}
    </>
  );
}
