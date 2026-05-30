"use client";

import { useState } from "react";
import type { ProgramData } from "../../types";
import { fmt, getColor, getGrad } from "../../helpers";

type RegionCoverage = Record<string, { we_have: number; total_top_universities: number; coverage_pct: number }>;

type SectionStat = { with_data: number; verified: number; partial: number; flagged: number; no_data: number; trust: number };
type EntityStat = { matched?: number; mismatched?: number; total?: number; rate?: number };

const ENT_NAMES_PHD: Record<string, string> = {
  gre_status: "GRE Status", ielts: "IELTS", gpa: "GPA", lor_count: "LOR Count",
  cambridge: "Cambridge", toefl: "TOEFL", app_fee: "App Fee", work_experience: "Work Exp",
  duolingo: "Duolingo", pte: "PTE", gre_score: "GRE Score",
};
const ENT_NAMES_UG: Record<string, string> = {
  sat: "SAT", act: "ACT", toefl: "TOEFL", ielts: "IELTS", gpa: "GPA",
};

const SECTION_NAMES: Record<string, { name: string; short: string }> = {
  admission_requirements: { name: "Admission Requirements", short: "admission" },
  application_deadlines: { name: "Application Deadlines", short: "deadline" },
  tuition_and_fees: { name: "Tuition & Fees", short: "tuition" },
};

export default function StatisticsSection({
  data,
  isUG,
  regionCoverage,
}: {
  data: ProgramData;
  isUG: boolean;
  regionCoverage?: RegionCoverage;
}) {
  const [expanded, setExpanded] = useState<Record<number, boolean>>({});
  const [activeRegion, setActiveRegion] = useState<string | null>(null);
  const toggleCard = (i: number) => setExpanded((p) => ({ ...p, [i]: !p[i] }));

  const d = data;
  const s = (d.section_stats || {}) as Record<string, SectionStat>;
  const ent = (d.entity_stats || {}) as Record<string, EntityStat>;
  const comp = ((d as unknown as { component_stats?: Record<string, EntityStat> }).component_stats || {}) as Record<string, EntityStat>;
  const reg = (d.region_coverage || regionCoverage || {}) as RegionCoverage;

  const entNames = isUG ? ENT_NAMES_UG : ENT_NAMES_PHD;
  const entData = isUG ? comp : ent;
  const sortedEnts = Object.entries(entData)
    .filter(([, v]) => ((v.total || (v.matched || 0) + (v.mismatched || 0)) || 0) > 0)
    .sort((a, b) => (b[1].rate || 0) - (a[1].rate || 0));

  const sectionOrder = isUG ? ["tuition_and_fees", "application_deadlines"] : ["admission_requirements", "application_deadlines", "tuition_and_fees"];

  // Universities max/min for card 5
  const universities = (d.universities || []) as { courses: number }[];
  const maxCourses = universities.length ? Math.max(...universities.map((u) => u.courses)) : "-";
  const minCourses = universities.length ? Math.min(...universities.map((u) => u.courses)) : "-";

  const caveat = (d as unknown as { caveat?: number }).caveat || 0;
  const review = (d as unknown as { review?: number }).review || 0;
  const crawled_urls = (d as unknown as { crawled_urls?: number }).crawled_urls || 0;
  const needed_urls = (d as unknown as { needed_urls?: number }).needed_urls || 0;
  const crawl_pct = (d as unknown as { crawl_pct?: number }).crawl_pct || 0;

  const regEntries = Object.entries(reg);

  return (
    <>
      {/* === Top cards === */}
      <div className="card-row">
        <div className={`card clickable ${expanded[0] ? "expanded" : ""}`} onClick={() => toggleCard(0)}>
          <div className="number">{fmt(d.total_classified)}</div>
          <div className="label">Total Classified Courses</div>
          <div className="sublabel">{d.program_type} programs</div>
          <div className="card-dropdown">
            Total course records: <strong>{fmt(d.total_courses_all)}</strong>
          </div>
        </div>
        <div className={`card clickable ${expanded[1] ? "expanded" : ""}`} onClick={() => toggleCard(1)}>
          <div className="number">{d.classified_unis}</div>
          <div className="label">Total Universities Classified</div>
          <div className="sublabel">{d.program_type} Worldwide</div>
          <div className="card-dropdown">
            Total Universities Records: <strong>{fmt(d.total_unis_all)}</strong>
          </div>
        </div>
        <div className={`card clickable ${expanded[2] ? "expanded" : ""}`} onClick={() => toggleCard(2)}>
          <div className="number">{d.num_domains}</div>
          <div className="label">Domain Coverage</div>
          <div className="sublabel">academic fields</div>
          <div className="card-dropdown">
            Total domains in records: <strong>{d.num_domains}</strong>
          </div>
        </div>
        <div className={`card clickable ${expanded[3] ? "expanded" : ""}`} onClick={() => toggleCard(3)}>
          <div className="number">{fmt(d.verified_combined)}</div>
          <div className="label">Trusted Course Records</div>
          <div className="sublabel">{d.verified_pct}%</div>
          <div className="card-dropdown">
            <div>Use with caveat: <strong>{fmt(caveat)}</strong></div>
            <div>Needs review: <strong>{fmt(review)}</strong></div>
          </div>
        </div>
        <div className={`card clickable ${expanded[4] ? "expanded" : ""}`} onClick={() => toggleCard(4)}>
          <div className="number">{d.avg_courses_per_uni}</div>
          <div className="label">Avg Courses</div>
          <div className="sublabel">per college</div>
          <div className="card-dropdown">
            <div>Max: <strong>{maxCourses}</strong> courses per uni</div>
            <div>Min: <strong>{minCourses}</strong> course per uni</div>
          </div>
        </div>
      </div>

      {/* === Major Section Coverage === */}
      <div className="section-title">Major Section Coverage</div>
      <div style={{ margin: "-12px 0 12px", fontSize: 12, color: "var(--gray)", fontStyle: "italic" }}>
        Counts show files where data was present in both the markdown and the crawled page.
      </div>
      <div className="panel">
        {sectionOrder.map((k) => {
          const sec = s[k];
          if (!sec) return null;
          const wd = sec.with_data;
          const vPct = wd > 0 ? ((sec.verified / wd) * 100).toFixed(1) : "0";
          const pPct = wd > 0 ? ((sec.partial / wd) * 100).toFixed(1) : "0";
          const fPct = wd > 0 ? ((sec.flagged / wd) * 100).toFixed(1) : "0";
          const meta = SECTION_NAMES[k];
          return (
            <div key={k} className="stacked-section">
              <div className="stacked-header">
                <span className="sh-name">{meta.name}</span>
                <span className="sh-total">{fmt(wd)} / {fmt(d.total_classified)} files had {meta.short} data to compare</span>
              </div>
              <div className="stacked-bar">
                <div className="stacked-seg verified" style={{ width: `${vPct}%` }}>{vPct}%</div>
                <div className="stacked-seg partial" style={{ width: `${pPct}%` }}>{pPct}%</div>
                <div className="stacked-seg flagged" style={{ width: `${fPct}%` }}>{fPct}%</div>
              </div>
              <div className="stacked-legend">
                <div className="stacked-legend-item">
                  <div className="stacked-legend-dot" style={{ background: "#34a853" }}></div>
                  {fmt(sec.verified)} verified ({vPct}%)
                </div>
                <div className="stacked-legend-item">
                  <div className="stacked-legend-dot" style={{ background: "#1a73e8" }}></div>
                  {fmt(sec.partial)} partial ({pPct}%)
                </div>
                <div className="stacked-legend-item">
                  <div className="stacked-legend-dot" style={{ background: "#ea4335" }}></div>
                  {fmt(sec.flagged)} flagged ({fPct}%)
                </div>
              </div>
            </div>
          );
        })}
      </div>

      {/* === University Coverage by Region === */}
      <div className="section-title">University Coverage by Region</div>
      <div className="region-row">
        {regEntries.map(([name, rd]) => {
          const short = name.replace("EUROPE - ", "").replace("AUSTRALIA & NEW ZEALAND", "Aus/NZ");
          return (
            <div
              key={name}
              className={`region-card ${activeRegion === name ? "active" : ""}`}
              onClick={() => setActiveRegion((p) => (p === name ? null : name))}
            >
              <div className="region-name">{short}</div>
              <div className="region-stat">{rd.we_have}/{rd.total_top_universities}</div>
              <div className="region-pct" style={{ color: getColor(rd.coverage_pct) }}>{rd.coverage_pct}%</div>
            </div>
          );
        })}
      </div>
      {/* Region dropdown — opens on click */}
      {activeRegion && (() => {
        const rd = reg[activeRegion] as unknown as {
          we_have: number;
          we_dont_have?: number;
          total_top_universities: number;
          coverage_pct: number;
          universities_we_have?: { rank: number; name: string }[];
          universities_missing?: { rank: number; name: string }[];
        };
        if (!rd) return null;
        const short = activeRegion.replace("EUROPE - ", "").replace("AUSTRALIA & NEW ZEALAND", "Aus/NZ");
        const haveList = rd.universities_we_have || [];
        const missList = rd.universities_missing || [];
        return (
          <div className="region-dropdown open">
            <h3>
              {short} | {rd.we_have} of {rd.total_top_universities} top universities | {rd.coverage_pct}%
            </h3>
            <div className="region-columns">
              <div className="region-col">
                <h4 className="have">We Have ({rd.we_have})</h4>
                <ul className="region-list">
                  {haveList.length > 0 ? (
                    haveList.map((u, i) => (
                      <li key={i}>
                        <span>
                          <span className="rank">#{u.rank}</span> {u.name}
                        </span>
                      </li>
                    ))
                  ) : (
                    <li>None</li>
                  )}
                </ul>
              </div>
              <div className="region-col">
                <h4 className="missing">
                  We Don&apos;t Have ({rd.we_dont_have ?? missList.length})
                </h4>
                <ul className="region-list">
                  {missList.length > 0 ? (
                    missList.map((u, i) => (
                      <li key={i}>
                        <span>
                          <span className="rank">#{u.rank}</span> {u.name}
                        </span>
                      </li>
                    ))
                  ) : (
                    <li style={{ color: "var(--green)" }}>Complete coverage!</li>
                  )}
                </ul>
              </div>
            </div>
          </div>
        );
      })()}

      {/* === Official URL Crawls === */}
      <div className="section-title">Official URL Crawls</div>
      <div className="panel">
        <div className="progress-item">
          <div className="progress-label">
            <span>Total pages crawled</span>
            <span>{fmt(crawled_urls)} / {fmt(needed_urls)} URLs</span>
          </div>
          <div className="progress-bar">
            <div className="progress-fill blue" style={{ width: `${crawl_pct}%` }}>{crawl_pct}%</div>
          </div>
        </div>
        <div className="progress-item">
          <div className="progress-label">
            <span>Courses classified</span>
            <span>{fmt(d.total_classified)} / {fmt(d.total_courses_all)}</span>
          </div>
          <div className="progress-bar">
            <div
              className="progress-fill blue"
              style={{ width: `${((d.total_classified / d.total_courses_all) * 100).toFixed(1)}%` }}
            >
              {((d.total_classified / d.total_courses_all) * 100).toFixed(1)}%
            </div>
          </div>
        </div>
      </div>

      {/* === Entity Verification === */}
      <div className="section-title">{isUG ? "Admission Component Verification (SAT/ACT/TOEFL/IELTS/GPA)" : "Important Entity Verification Rates"}</div>
      <div style={{ fontSize: 12, color: "var(--gray)", margin: "-8px 0 12px", lineHeight: 1.6 }}>
        {isUG
          ? "Each admission component is checked by comparing .md data against crawled official pages. MATCHED means both agree (scores match or both say not required). MISMATCHED means data differs. "
          : "Each admission requirement entity (TOEFL, IELTS, GPA, etc.) is checked by comparing the value in our data against the official university website. "}
        <span style={{ color: "#34a853" }}>Green (&gt;80%)</span> = highly reliable,{" "}
        <span style={{ color: "#1a73e8" }}>Blue (60-80%)</span> = decent,{" "}
        <span style={{ color: "#f9ab00" }}>Yellow (40-60%)</span> = moderate,{" "}
        <span style={{ color: "#ea4335" }}>Red (&lt;40%)</span> = needs improvement.
        <div style={{ marginTop: 8, padding: "8px 12px", background: "#f0f4ff", borderLeft: "3px solid var(--primary)", borderRadius: 4, fontSize: 11.5, color: "var(--dark)" }}>
          <strong>How to read:</strong> Each row shows <code style={{ background: "#fff", padding: "1px 4px", borderRadius: 3 }}>X% (matched/total)</code>.{" "}
          For example, <code style={{ background: "#fff", padding: "1px 4px", borderRadius: 3 }}>IELTS 77% (2,708/3,515)</code> means: out of 3,515 files where both our markdown AND the official site mention IELTS, 2,708 had the same score. The other 807 had a mismatch (e.g., our md says 7.0, the site says 6.5). <em>Hover over any row to see its breakdown.</em>
        </div>
      </div>
      <div className="panel">
        {sortedEnts.map(([k, v]) => {
          const total = v.total || ((v.matched || 0) + (v.mismatched || 0)) || 0;
          const rate = v.rate || (total > 0 ? Math.round(((v.matched || 0) / total) * 100 * 10) / 10 : 0);
          const entLabel = entNames[k] || k;
          const mismatched = total - (v.matched || 0);
          const tip = `Of ${fmt(total)} files where both our data AND the official site mention ${entLabel}, ${fmt(v.matched || 0)} matched and ${fmt(mismatched)} had a mismatch (e.g., md says one value, site says another).`;
          return (
            <div key={k} className="entity-row" title={tip}>
              <div className="entity-name">{entLabel}</div>
              <div className="entity-bar-bg">
                <div className="entity-bar-fill" style={{ width: `${rate}%`, background: getGrad(rate) }}></div>
              </div>
              <div className="entity-rate">{rate}% ({fmt(v.matched || 0)}/{fmt(total)})</div>
            </div>
          );
        })}
      </div>
    </>
  );
}
