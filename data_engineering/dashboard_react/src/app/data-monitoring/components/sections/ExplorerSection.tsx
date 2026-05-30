"use client";

import { useState, useMemo } from "react";
import type { ProgramData } from "../../types";
import { fmt } from "../../helpers";

type SectionStat = { with_data: number; verified: number; partial: number; flagged: number; no_data: number; trust: number };

type CourseEntity = [string, string]; // [status_letter, value_string]

interface Course {
  n: string;
  f: string;
  s: number;
  l: string;
  t: string;
  d: string;
  a: string;
  e: CourseEntity[];
  tv: string;
  dv: string;
  tr: string;
  dr: string;
  u: Record<string, string>;
  md: string;
  sm: string;
}

interface Uni {
  name: string;
  folder: string;
  courses: number;
  avg_score: number;
  label: string;
  course_list: Course[];
}

const ENT_LABELS = ["TOEFL", "IELTS", "GRE", "GPA", "Fee", "LOR", "PTE", "DET", "GRE#"];

type SecCode = "V" | "P" | "F" | "-";
const SEC_LABEL: Record<SecCode, string> = { V: "Verified", P: "Partial", F: "Flagged", "-": "No Data" };
const STATUS_TO_FIELD: Record<SecCode, keyof SectionStat> = { V: "verified", P: "partial", F: "flagged", "-": "no_data" };

// Original constants from build_dashboard.py
const SEC_MAP: Record<string, string> = { V: "verified", P: "partial", F: "flagged", "-": "no_data" };
const SEC_ICON: Record<string, string> = { V: "✓", P: "~", F: "✗", "-": "—" };
const SEC_CLS: Record<string, string> = { V: "V", P: "P", F: "F", "-": "x" };
const ENT_ICON: Record<string, string> = { V: "✓", F: "✗", "?": "?", "-": "—", "~": "~" };
const ENT_CLS: Record<string, string> = { V: "V", F: "F", "?": "q", "-": "x", "~": "x" };

type ConfBand = "75" | "60" | "40" | "0";

function cleanEntValue(val: string): string {
  let v = val ? " " + val : "";
  if (v.includes("negative")) v = " not required";
  if (v.includes("positive")) v = " required";
  if (v === " no_fee") v = " free";
  if (v.startsWith(" [")) v = v.replace(/[[\]']/g, "");
  return v;
}

export default function ExplorerSection({ data, isUG }: { data: ProgramData; isUG: boolean }) {
  const [search, setSearch] = useState("");
  const [confFilter, setConfFilter] = useState<Set<ConfBand>>(new Set());
  const [tuitionFilter, setTuitionFilter] = useState<Set<SecCode>>(new Set());
  const [deadlineFilter, setDeadlineFilter] = useState<Set<SecCode>>(new Set());
  const [admissionFilter, setAdmissionFilter] = useState<Set<SecCode>>(new Set());
  const [entityFilter, setEntityFilter] = useState<Set<number>>(new Set());
  const [openUnis, setOpenUnis] = useState<Set<string>>(new Set());
  const [openCourses, setOpenCourses] = useState<Set<string>>(new Set());
  const toggleCourse = (key: string) =>
    setOpenCourses((p) => {
      const n = new Set(p);
      if (n.has(key)) n.delete(key);
      else n.add(key);
      return n;
    });

  const d = data;
  const unis = (d.universities || []) as Uni[];
  const ss = (d.section_stats || {}) as Record<string, SectionStat>;
  const en = (d.entity_stats || {}) as Record<string, { matched?: number }>;

  const safe = (d as unknown as { safe?: number }).safe || 0;
  const mostly = (d as unknown as { mostly?: number }).mostly || 0;
  const caveat = (d as unknown as { caveat?: number }).caveat || 0;
  const review = (d as unknown as { review?: number }).review || 0;

  const hasAnyFilter =
    confFilter.size > 0 || tuitionFilter.size > 0 || deadlineFilter.size > 0 || admissionFilter.size > 0 || entityFilter.size > 0;

  const toggleSet = <T,>(set: Set<T>, val: T, fn: (s: Set<T>) => void) => {
    const next = new Set(set);
    if (next.has(val)) next.delete(val);
    else next.add(val);
    fn(next);
  };

  const toggleUni = (folder: string) =>
    setOpenUnis((p) => {
      const n = new Set(p);
      if (n.has(folder)) n.delete(folder);
      else n.add(folder);
      return n;
    });

  const resetFilters = () => {
    setSearch("");
    setConfFilter(new Set());
    setTuitionFilter(new Set());
    setDeadlineFilter(new Set());
    setAdmissionFilter(new Set());
    setEntityFilter(new Set());
  };

  const courseMatches = (c: Course): boolean => {
    // Confidence filter — EXCLUSIVE bands (matches original)
    if (confFilter.size > 0) {
      let confMatch = false;
      if (confFilter.has("75") && c.s >= 75) confMatch = true;
      if (confFilter.has("60") && c.s >= 60 && c.s < 75) confMatch = true;
      if (confFilter.has("40") && c.s >= 40 && c.s < 60) confMatch = true;
      if (confFilter.has("0") && c.s < 40) confMatch = true;
      if (!confMatch) return false;
    }
    // Section filters
    if (tuitionFilter.size > 0 && !tuitionFilter.has(c.t as SecCode)) return false;
    if (deadlineFilter.size > 0 && !deadlineFilter.has(c.d as SecCode)) return false;
    if (admissionFilter.size > 0 && !admissionFilter.has(c.a as SecCode)) return false;
    // Entity filter — must be "V" (matched) for all selected entity indices
    if (entityFilter.size > 0 && Array.isArray(c.e)) {
      for (const idx of entityFilter) {
        if (!c.e[idx] || c.e[idx][0] !== "V") return false;
      }
    }
    return true;
  };

  // Build per-uni filtered course lists
  const filteredView = useMemo(() => {
    const q = search.trim().toLowerCase();
    const list: { uni: Uni; matchedCourses: Course[] }[] = [];
    let totalCourses = 0;
    for (const u of unis) {
      if (q && !u.name.toLowerCase().includes(q)) continue;
      const matched = u.course_list.filter(courseMatches);
      if (matched.length === 0 && hasAnyFilter) continue;
      list.push({ uni: u, matchedCourses: matched });
      totalCourses += matched.length;
    }
    return { list, totalCourses };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [unis, search, confFilter, tuitionFilter, deadlineFilter, admissionFilter, entityFilter]);

  const quickFilter = (type: "verified" | "attention" | "toefl_miss") => {
    resetFilters();
    if (type === "verified") setConfFilter(new Set(["75"]));
    if (type === "attention") setConfFilter(new Set(["0"]));
    if (type === "toefl_miss") {
      // TOEFL is index 0, IELTS is index 1 — but original original logic was just a placeholder
      // matching not-V on either. Closest approximation: filter TOEFL = F or IELTS = F
      // For simplicity here, just check TOEFL index 0 must be V — inverse not supported
      // So we mimic the original which actually just enables fc0
      setConfFilter(new Set(["0"]));
    }
  };

  const countLabel = hasAnyFilter
    ? `Filtered: ${fmt(filteredView.totalCourses)} courses from ${fmt(filteredView.list.length)} universities`
    : `Showing ${fmt(filteredView.list.length)} universities`;

  return (
    <div className="explorer-layout">
      <div className="explorer-sidebar">
        <input
          type="text"
          className="search-box"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder="Search university..."
          style={{ marginBottom: 12, padding: "8px 12px", fontSize: 12 }}
        />

        <div className="filter-group">
          <div className="filter-title">Confidence</div>
          {[
            { v: "75" as ConfBand, label: ">= 75", cnt: safe + mostly },
            { v: "60" as ConfBand, label: ">= 60", cnt: "incl above" as string | number },
            { v: "40" as ConfBand, label: ">= 40", cnt: caveat },
            { v: "0" as ConfBand, label: "< 40", cnt: review },
          ].map((f) => (
            <div key={f.v} className="filter-item">
              <input
                type="checkbox"
                id={`fc${f.v}`}
                checked={confFilter.has(f.v)}
                onChange={() => toggleSet(confFilter, f.v, setConfFilter)}
              />
              <label htmlFor={`fc${f.v}`}>
                {f.label} <span className="cnt">({f.cnt})</span>
              </label>
            </div>
          ))}
        </div>

        <div className="filter-group">
          <div className="filter-title">Tuition & Fees</div>
          {(["V", "P", "F", "-"] as SecCode[]).map((code) => (
            <div key={code} className="filter-item">
              <input
                type="checkbox"
                id={`ft${code}`}
                checked={tuitionFilter.has(code)}
                onChange={() => toggleSet(tuitionFilter, code, setTuitionFilter)}
              />
              <label htmlFor={`ft${code}`}>
                {SEC_LABEL[code]} <span className="cnt">({fmt(ss.tuition_and_fees?.[STATUS_TO_FIELD[code]] ?? 0)})</span>
              </label>
            </div>
          ))}
        </div>

        <div className="filter-group">
          <div className="filter-title">Deadlines</div>
          {(["V", "P", "F", "-"] as SecCode[]).map((code) => (
            <div key={code} className="filter-item">
              <input
                type="checkbox"
                id={`fd${code}`}
                checked={deadlineFilter.has(code)}
                onChange={() => toggleSet(deadlineFilter, code, setDeadlineFilter)}
              />
              <label htmlFor={`fd${code}`}>
                {SEC_LABEL[code]} <span className="cnt">({fmt(ss.application_deadlines?.[STATUS_TO_FIELD[code]] ?? 0)})</span>
              </label>
            </div>
          ))}
        </div>

        {ss.admission_requirements && (
          <div className="filter-group">
            <div className="filter-title">Admission Req</div>
            {(["V", "P", "F", "-"] as SecCode[]).map((code) => (
              <div key={code} className="filter-item">
                <input
                  type="checkbox"
                  id={`fa${code}`}
                  checked={admissionFilter.has(code)}
                  onChange={() => toggleSet(admissionFilter, code, setAdmissionFilter)}
                />
                <label htmlFor={`fa${code}`}>
                  {SEC_LABEL[code]} <span className="cnt">({fmt(ss.admission_requirements?.[STATUS_TO_FIELD[code]] ?? 0)})</span>
                </label>
              </div>
            ))}
          </div>
        )}

        {!isUG && en && Object.keys(en).length > 0 && (
          <div className="filter-group">
            <div className="filter-title">Entities</div>
            {ENT_LABELS.map((lbl, i) => {
              const key = ["toefl", "ielts", "gre_status", "gpa", "app_fee", "lor_count", "pte", "duolingo", "gre_score"][i];
              const cnt = en[key]?.matched || 0;
              return (
                <div key={i} className="filter-item">
                  <input
                    type="checkbox"
                    id={`fe${i}`}
                    checked={entityFilter.has(i)}
                    onChange={() => toggleSet(entityFilter, i, setEntityFilter)}
                  />
                  <label htmlFor={`fe${i}`}>
                    {lbl} matched <span className="cnt">({fmt(cnt)})</span>
                  </label>
                </div>
              );
            })}
          </div>
        )}

        <div className="filter-group">
          <div className="filter-title">Quick Filters</div>
          <button className="quick-btn" onClick={() => quickFilter("verified")}>All Verified</button>
          <button className="quick-btn" onClick={() => quickFilter("attention")}>Needs Attention</button>
          <button className="quick-btn" onClick={() => quickFilter("toefl_miss")}>TOEFL/IELTS Miss</button>
          <button className="reset-btn" onClick={resetFilters}>Reset All Filters</button>
        </div>
      </div>

      <div className="explorer-content">
        <div
          style={{
            background: "var(--white)",
            borderRadius: 14,
            padding: "16px 24px",
            marginBottom: 20,
            boxShadow: "0 2px 12px rgba(26,115,232,0.1)",
            border: "2px solid var(--primary)",
          }}
        >
          <input
            type="text"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Search university by name..."
            style={{
              width: "100%",
              padding: "14px 20px",
              border: "none",
              outline: "none",
              fontSize: 16,
              fontWeight: 500,
              color: "var(--dark)",
              background: "transparent",
            }}
          />
        </div>
        <div className="showing-count">{countLabel}</div>
        <div>
          {filteredView.list.slice(0, 200).map(({ uni: u, matchedCourses }) => {
            // Auto-expand when any filter is active
            const open = hasAnyFilter || openUnis.has(u.folder);
            return (
              <div key={u.folder} className="uni-row">
                <div className="uni-header" onClick={() => toggleUni(u.folder)}>
                  <div className="uni-name">{u.name}</div>
                  <div className="uni-meta">
                    <span style={{ color: "var(--gray)", fontSize: 11 }}>
                      {hasAnyFilter ? `${matchedCourses.length}/${u.courses}` : `${u.courses}`} courses
                    </span>
                    <span className={`label-tag ${u.label}`}>{u.label}</span>
                    <span style={{ fontWeight: 700 }}>{u.avg_score}</span>
                  </div>
                </div>
                {open && (
                  <div className="uni-courses open">
                    {matchedCourses.map((c, ci) => {
                      const courseKey = `${u.folder}-${ci}-${c.f || c.n}`;
                      const courseOpen = openCourses.has(courseKey);
                      return (
                        <div key={ci}>
                          <div
                            className="course-row"
                            onClick={(e) => {
                              e.stopPropagation();
                              toggleCourse(courseKey);
                            }}
                          >
                            <div className="course-name">{c.n}</div>
                            <div className="course-meta">
                              <span style={{ fontWeight: 700, width: 35, textAlign: "right" }}>{c.s}</span>
                              <span className={`sb ${SEC_CLS[c.t] || "x"}`}>{c.t || "-"}</span>
                              <span className={`sb ${SEC_CLS[c.d] || "x"}`}>{c.d || "-"}</span>
                              {!isUG && (
                                <span className={`sb ${SEC_CLS[c.a] || "x"}`}>{c.a || "-"}</span>
                              )}
                              <span className={`label-tag ${c.l}`} style={{ fontSize: 9, padding: "2px 6px" }}>
                                {c.l}
                              </span>
                            </div>
                          </div>
                          <div className={`course-detail ${courseOpen ? "open" : ""}`}>
                            {courseOpen && (
                              <>
                                <div style={{ fontWeight: 700, marginBottom: 8 }}>
                                  {u.name} &mdash; {c.n}
                                </div>
                                <div style={{ marginBottom: 8, color: "var(--gray)" }}>
                                  Score: {c.s} | Label: {c.l}
                                </div>

                                {/* Tuition box */}
                                <div className={`detail-box ${c.t}`}>
                                  <div className="detail-box-title">
                                    Tuition &amp; Fees &mdash;{" "}
                                    <span className={`ent-v ${SEC_CLS[c.t] || "x"}`}>
                                      {SEC_ICON[c.t] || "—"} {c.tv || ""}
                                    </span>{" "}
                                    <span style={{ fontWeight: 400, textTransform: "none", fontSize: 10, color: "var(--gray)" }}>
                                      ({SEC_MAP[c.t] || "no_data"})
                                    </span>
                                  </div>
                                  <div style={{ fontSize: 11, color: "var(--gray)" }}>
                                    {c.tr ? c.tr.replace(/_/g, " ") : ""}
                                  </div>
                                  {c.u && c.u.tuition_and_fees && (
                                    <div style={{ fontSize: 10, marginTop: 4 }}>
                                      <a
                                        href={c.u.tuition_and_fees}
                                        target="_blank"
                                        rel="noopener noreferrer"
                                        style={{ color: "var(--primary)", textDecoration: "none" }}
                                      >
                                        Source &rarr;
                                      </a>
                                    </div>
                                  )}
                                </div>

                                {/* Deadlines box */}
                                <div className={`detail-box ${c.d}`}>
                                  <div className="detail-box-title">
                                    Deadlines &mdash;{" "}
                                    <span className={`ent-v ${SEC_CLS[c.d] || "x"}`}>
                                      {SEC_ICON[c.d] || "—"} {c.dv || ""}
                                    </span>{" "}
                                    <span style={{ fontWeight: 400, textTransform: "none", fontSize: 10, color: "var(--gray)" }}>
                                      ({SEC_MAP[c.d] || "no_data"})
                                    </span>
                                  </div>
                                  <div style={{ fontSize: 11, color: "var(--gray)" }}>
                                    {c.dr ? c.dr.replace(/_/g, " ") : ""}
                                  </div>
                                  {c.u && c.u.application_deadlines && (
                                    <div style={{ fontSize: 10, marginTop: 4 }}>
                                      <a
                                        href={c.u.application_deadlines}
                                        target="_blank"
                                        rel="noopener noreferrer"
                                        style={{ color: "var(--primary)", textDecoration: "none" }}
                                      >
                                        Source &rarr;
                                      </a>
                                    </div>
                                  )}
                                </div>

                                {/* Admission box (PhD/Masters only) */}
                                {!isUG && (
                                  <div className={`detail-box ${c.a}`}>
                                    <div className="detail-box-title">
                                      Admission &mdash; {SEC_ICON[c.a] || "—"} {SEC_MAP[c.a] || "no_data"}
                                    </div>
                                    <div className="ent-grid">
                                      {Array.isArray(c.e) &&
                                        ENT_LABELS.map((lbl, i) => {
                                          const e = c.e[i] || ["-", ""];
                                          const st = e[0] || "-";
                                          const val = e[1] || "";
                                          const dispVal = cleanEntValue(val);
                                          return (
                                            <div key={i} className="ent-item">
                                              <span>{lbl}:</span>
                                              <span className={`ent-v ${ENT_CLS[st] || "x"}`}>
                                                {ENT_ICON[st] || "—"}
                                                {dispVal}
                                              </span>
                                            </div>
                                          );
                                        })}
                                    </div>
                                    {c.u && c.u.admission_requirements && (
                                      <div style={{ fontSize: 10, marginTop: 6 }}>
                                        <a
                                          href={c.u.admission_requirements}
                                          target="_blank"
                                          rel="noopener noreferrer"
                                          style={{ color: "var(--primary)", textDecoration: "none" }}
                                        >
                                          Source &rarr;
                                        </a>
                                      </div>
                                    )}
                                  </div>
                                )}

                                {/* Action buttons */}
                                <div style={{ display: "flex", gap: 8, marginTop: 12, flexWrap: "wrap" }}>
                                  <button
                                    onClick={(e) => {
                                      e.stopPropagation();
                                      alert(`View all details for ${u.folder} / ${c.f || c.n}`);
                                    }}
                                    style={{
                                      padding: "7px 14px",
                                      border: "1px solid var(--primary)",
                                      borderRadius: 8,
                                      background: "#e8f0fe",
                                      color: "var(--primary)",
                                      fontSize: 11,
                                      fontWeight: 600,
                                      cursor: "pointer",
                                    }}
                                  >
                                    View All Details
                                  </button>
                                  <button
                                    onClick={(e) => {
                                      e.stopPropagation();
                                      alert(`Would open: ${c.md || "(no md path)"}`);
                                    }}
                                    style={{
                                      padding: "7px 14px",
                                      border: "1px solid var(--border)",
                                      borderRadius: 8,
                                      background: "white",
                                      color: "var(--dark)",
                                      fontSize: 11,
                                      fontWeight: 600,
                                      cursor: "pointer",
                                    }}
                                  >
                                    View .md File
                                  </button>
                                  <button
                                    onClick={(e) => {
                                      e.stopPropagation();
                                      const urls = c.u || {};
                                      let n = 0;
                                      if (urls.tuition_and_fees) {
                                        window.open(urls.tuition_and_fees, "_blank");
                                        n++;
                                      }
                                      if (urls.application_deadlines) {
                                        window.open(urls.application_deadlines, "_blank");
                                        n++;
                                      }
                                      if (!isUG && urls.admission_requirements) {
                                        window.open(urls.admission_requirements, "_blank");
                                        n++;
                                      }
                                      if (n === 0) alert("No official URLs available for this course.");
                                    }}
                                    style={{
                                      padding: "7px 14px",
                                      border: "1px solid #34a853",
                                      borderRadius: 8,
                                      background: "#e6f4ea",
                                      color: "#34a853",
                                      fontSize: 11,
                                      fontWeight: 600,
                                      cursor: "pointer",
                                    }}
                                  >
                                    Open Official URLs
                                  </button>
                                </div>
                              </>
                            )}
                          </div>
                        </div>
                      );
                    })}
                  </div>
                )}
              </div>
            );
          })}
          {filteredView.list.length > 200 && (
            <div style={{ padding: 14, textAlign: "center", color: "var(--gray)", fontSize: 12 }}>
              Showing first 200 of {filteredView.list.length} — refine filters to narrow down.
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
