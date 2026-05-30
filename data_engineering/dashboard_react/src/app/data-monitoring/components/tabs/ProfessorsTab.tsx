"use client";

import { useState } from "react";
import type { ProfessorsData } from "../../types";
import { fmt, getGrad } from "../../helpers";

const REGION_COLORS: Record<string, string> = {
  US: "linear-gradient(90deg,#1a73e8,#4285f4)",
  Europe: "linear-gradient(90deg,#34a853,#43b463)",
  Others: "linear-gradient(90deg,#fbbc04,#ffca28)",
};

export default function ProfessorsTab({ data }: { data: ProfessorsData | undefined }) {
  const [expanded, setExpanded] = useState<Record<number, boolean>>({});
  const toggleCard = (i: number) => setExpanded((p) => ({ ...p, [i]: !p[i] }));

  if (!data || !data.summary) {
    return (
      <div className="panel" style={{ padding: 32, textAlign: "center", color: "var(--gray)" }}>
        No Professors data available. Run <code>python dashboard/compute_professor_coverage.py</code> then regenerate.
      </div>
    );
  }

  const s = data.summary;
  const pl = data.pipeline || ({} as ProfessorsData["pipeline"]);
  const fc = data.field_coverage || [];
  const rc = data.regional_coverage || [];
  const ps = data.personal_sites || ({} as ProfessorsData["personal_sites"]);

  const uniOkPct = s.input_universities ? ((s.unis_with_ok / s.input_universities) * 100).toFixed(1) : "0";
  const successPct = s.success_pct_of_attempts != null ? s.success_pct_of_attempts : s.success_pct;
  const avgProfs = s.avg_profs_per_uni;

  const stats = pl.statuses || {};
  const strategies = pl.strategies || {};
  const okOnly = stats.ok || 0;
  const okHomepage = stats.ok_via_homepage_navigation || 0;
  const skipped = stats.skipped || 0;
  const total = okOnly + okHomepage + skipped;
  const okPct = total ? (((okOnly + okHomepage) / total) * 100).toFixed(1) : "0";

  return (
    <>
      {/* ─── Top 5 cards ─── */}
      <div className="card-row">
        <div className={`card clickable ${expanded[0] ? "expanded" : ""}`} onClick={() => toggleCard(0)}>
          <div className="number">{fmt(s.total_professors)}</div>
          <div className="label">Total Professors</div>
          <div className="sublabel">across {s.unique_departments} departments &middot; click for details</div>
          <div className="card-dropdown">
            <div style={{ display: "flex", justifyContent: "space-between" }}>
              <span>Across departments</span>
              <strong>{fmt(s.ok_count)}</strong>
            </div>
            <div style={{ display: "flex", justifyContent: "space-between" }}>
              <span>Across universities</span>
              <strong>{fmt(s.unis_with_data)}</strong>
            </div>
          </div>
        </div>

        <div className={`card clickable ${expanded[1] ? "expanded" : ""}`} onClick={() => toggleCard(1)}>
          <div className="number">{s.unis_with_ok}</div>
          <div className="label">Universities Covered</div>
          <div className="sublabel">{uniOkPct}% of {s.input_universities} input &middot; click for details</div>
          <div className="card-dropdown">
            <div style={{ display: "flex", justifyContent: "space-between" }}>
              <span>Input universities</span>
              <strong>{s.input_universities}</strong>
            </div>
            <div style={{ display: "flex", justifyContent: "space-between" }}>
              <span>With &ge;1 ok extraction</span>
              <strong>{s.unis_with_ok}</strong>
            </div>
            <div style={{ display: "flex", justifyContent: "space-between" }}>
              <span>Zero-extraction unis</span>
              <strong>{s.input_universities - s.unis_with_ok}</strong>
            </div>
          </div>
        </div>

        <div className={`card clickable ${expanded[2] ? "expanded" : ""}`} onClick={() => toggleCard(2)}>
          <div className="number">{fmt(s.total_dept_files)}</div>
          <div className="label">Department Files</div>
          <div className="sublabel">{s.unique_departments} dept types &middot; click for details</div>
          <div className="card-dropdown">
            <div style={{ display: "flex", justifyContent: "space-between" }}>
              <span>Total attempts</span>
              <strong>{fmt(s.total_attempts)}</strong>
            </div>
            <div style={{ display: "flex", justifyContent: "space-between" }}>
              <span>Max possible ({s.unique_departments}&times;{s.input_universities})</span>
              <strong>{fmt(s.max_possible_pairs)}</strong>
            </div>
            <div style={{ display: "flex", justifyContent: "space-between" }}>
              <span>JSON files written</span>
              <strong>{fmt(s.total_dept_files)}</strong>
            </div>
          </div>
        </div>

        <div className={`card clickable ${expanded[3] ? "expanded" : ""}`} onClick={() => toggleCard(3)}>
          <div className="number">{successPct}%</div>
          <div className="label">Extraction Success</div>
          <div className="sublabel">{fmt(s.ok_count)} ok / {fmt(s.total_attempts)} attempts &middot; click for details</div>
          <div className="card-dropdown">
            <div style={{ display: "flex", justifyContent: "space-between" }}>
              <span>OK (full + homepage)</span>
              <strong>{fmt(s.ok_count)}</strong>
            </div>
            <div style={{ display: "flex", justifyContent: "space-between" }}>
              <span>Skipped</span>
              <strong>{fmt(s.skipped_count)}</strong>
            </div>
            <div style={{ display: "flex", justifyContent: "space-between" }}>
              <span>Errors (Bedrock fails)</span>
              <strong>{fmt(s.error_count || 0)}</strong>
            </div>
            <div style={{ display: "flex", justifyContent: "space-between" }}>
              <span>Parse errors</span>
              <strong>{fmt(s.parse_error_count || 0)}</strong>
            </div>
          </div>
        </div>

        <div className={`card clickable ${expanded[4] ? "expanded" : ""}`} onClick={() => toggleCard(4)}>
          <div className="number">{fmt(avgProfs)}</div>
          <div className="label">Avg Profs / University</div>
          <div className="sublabel">across {s.unis_with_ok} unis with data &middot; click for max/min</div>
          <div className="card-dropdown">
            <div style={{ display: "flex", justifyContent: "space-between" }}>
              <span>Max</span>
              <strong>{fmt(s.uni_max_count)} ({s.uni_max_name || "—"})</strong>
            </div>
            <div style={{ display: "flex", justifyContent: "space-between" }}>
              <span>Min</span>
              <strong>{fmt(s.uni_min_count)} ({s.uni_min_name || "—"})</strong>
            </div>
            <div style={{ display: "flex", justifyContent: "space-between" }}>
              <span>Median</span>
              <strong>{fmt(s.uni_median_count)}</strong>
            </div>
          </div>
        </div>
      </div>

      {/* ─── Pipeline Funnel ─── */}
      <div className="section-title">Extraction Pipeline Funnel</div>
      <div style={{ margin: "-12px 0 12px", fontSize: 12, color: "var(--gray)", fontStyle: "italic" }}>
        How department-level attempts flowed through the grounding pipeline.
      </div>
      <div className="panel" style={{ padding: 24 }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 12, marginBottom: 18 }}>
          <div style={{ flex: 1, textAlign: "center" }}>
            <div style={{ fontSize: 32, fontWeight: 800, color: "var(--primary)" }}>{fmt(total)}</div>
            <div style={{ fontSize: 13, color: "var(--dark)", fontWeight: 600, marginTop: 4 }}>Department Extracted</div>
            <div style={{ fontSize: 11, color: "#9aa0a6" }}>{s.unique_departments} dept types &times; {s.total_universities} unis</div>
          </div>
          <div style={{ flex: 0, textAlign: "center", minWidth: 90 }}>
            <div style={{ fontSize: 24, color: "var(--gray)", lineHeight: 1 }}>&rarr;</div>
            <div style={{ marginTop: 6, padding: "4px 8px", background: "#fce8e6", borderRadius: 4, fontSize: 11, color: "#ea4335", fontWeight: 600 }}>-{fmt(skipped)}</div>
            <div style={{ fontSize: 10, color: "#9aa0a6", marginTop: 2 }}>skipped</div>
          </div>
          <div style={{ flex: 1, textAlign: "center" }}>
            <div style={{ fontSize: 32, fontWeight: 800, color: "var(--primary)" }}>{fmt(okOnly + okHomepage)}</div>
            <div style={{ fontSize: 13, color: "var(--dark)", fontWeight: 600, marginTop: 4 }}>Successful OK Extractions</div>
            <div style={{ fontSize: 11, color: "#9aa0a6" }}>{okPct}% of attempts</div>
          </div>
          <div style={{ flex: 0, textAlign: "center", minWidth: 90 }}>
            <div style={{ fontSize: 24, color: "var(--gray)", lineHeight: 1 }}>&rarr;</div>
          </div>
          <div style={{ flex: 1, textAlign: "center" }}>
            <div style={{ fontSize: 32, fontWeight: 800, color: "var(--green)" }}>{fmt(s.total_professors)}</div>
            <div style={{ fontSize: 13, color: "var(--dark)", fontWeight: 600, marginTop: 4 }}>Professors Extracted</div>
            <div style={{ fontSize: 11, color: "#9aa0a6" }}>avg {(s.total_professors / (okOnly + okHomepage || 1)).toFixed(1)} per file</div>
          </div>
        </div>

        <div style={{ marginTop: 8, paddingTop: 16, borderTop: "1px solid var(--border)" }}>
          <div style={{ fontSize: 12, fontWeight: 700, color: "var(--dark)", marginBottom: 8 }}>Selection Strategy Breakdown</div>
          <div style={{ display: "flex", gap: 14, flexWrap: "wrap", fontSize: 12 }}>
            <div>
              <span style={{ display: "inline-block", width: 10, height: 10, background: "#34a853", borderRadius: 2, marginRight: 6 }}></span>
              <strong>{fmt(strategies.direct_chunk || 0)}</strong> direct chunk
            </div>
            <div>
              <span style={{ display: "inline-block", width: 10, height: 10, background: "#1a73e8", borderRadius: 2, marginRight: 6 }}></span>
              <strong>{fmt(strategies.homepage_navigation || 0)}</strong> homepage navigation
            </div>
            <div>
              <span style={{ display: "inline-block", width: 10, height: 10, background: "#ea4335", borderRadius: 2, marginRight: 6 }}></span>
              <strong>{fmt(strategies.none || 0)}</strong> none (skipped)
            </div>
          </div>
        </div>

        <div style={{ marginTop: 16, paddingTop: 16, borderTop: "1px solid var(--border)" }}>
          <div style={{ fontSize: 12, fontWeight: 700, color: "var(--dark)", marginBottom: 8 }}>Top Skip Reasons</div>
          {(pl.skip_reasons_top || []).length > 0 ? (
            (pl.skip_reasons_top || []).map((r, i) => (
              <div key={i} style={{ display: "flex", justifyContent: "space-between", padding: "6px 0", borderBottom: "1px solid var(--border)", fontSize: 12, gap: 12 }}>
                <span style={{ flex: 1, color: "var(--dark)" }}>{r.reason}</span>
                <span style={{ fontWeight: 700, color: "#ea4335" }}>{fmt(r.count)}</span>
              </div>
            ))
          ) : (
            <div style={{ color: "var(--gray)", fontSize: 12 }}>No skip reasons recorded.</div>
          )}
        </div>
      </div>

      {/* ─── Per-Professor Field Coverage ─── */}
      <div className="section-title">Per-Professor Field Coverage</div>
      <div style={{ margin: "-12px 0 12px", fontSize: 12, color: "var(--gray)", fontStyle: "italic" }}>
        % of {fmt(s.total_professors)} professor records where each field is populated with meaningful content.
      </div>
      <div className="panel" style={{ padding: 20 }}>
        {fc.map((f) => (
          <div key={f.name} className="density-field">
            <div className="df-name">{f.name}</div>
            <div className="df-bar-bg">
              <div className="df-bar-fill" style={{ width: `${f.pct}%`, background: getGrad(f.pct) }}></div>
            </div>
            <div className="df-pct">{f.pct}%</div>
            <div className="df-count">
              <span className="covered">{fmt(f.count)}</span> filled
              <span className="sep">&middot;</span>
              <span className="missing">{fmt(s.total_professors - f.count)}</span> missing
            </div>
          </div>
        ))}
        <div style={{ marginTop: 14, paddingTop: 12, borderTop: "1px solid var(--border)", fontSize: 11.5, color: "var(--gray)", fontStyle: "italic", lineHeight: 1.5 }}>
          <strong style={{ color: "var(--dark)", fontStyle: "normal" }}>Note:</strong> profile_url sits at 89.4% because some professors&apos; info was extracted from a single faculty-listing page that didn&apos;t link to individual profile pages. Name, role and other fields still come from the same listing &mdash; only the per-prof drill-down link was missing.
        </div>
      </div>

      {/* ─── Personal Website Capture ─── */}
      <div className="section-title">Personal Website Capture</div>
      <div style={{ margin: "-12px 0 12px", fontSize: 12, color: "var(--gray)", fontStyle: "italic" }}>
        Mini-funnel: of all professors, how many had a personal site URL, and how many of those were scraped to markdown.
      </div>
      <div className="panel" style={{ padding: 24 }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 12 }}>
          <div style={{ flex: 1, textAlign: "center" }}>
            <div style={{ fontSize: 28, fontWeight: 800, color: "var(--primary)" }}>{fmt(ps.total_profs || 0)}</div>
            <div style={{ fontSize: 12, color: "var(--dark)", fontWeight: 600, marginTop: 4 }}>All Professors</div>
          </div>
          <div style={{ flex: 0, textAlign: "center", minWidth: 80 }}>
            <div style={{ fontSize: 22, color: "var(--gray)", lineHeight: 1 }}>&rarr;</div>
            <div style={{ marginTop: 6, padding: "3px 8px", background: "#e8f0fe", borderRadius: 4, fontSize: 11, color: "#1a73e8", fontWeight: 600 }}>{ps.url_pct}%</div>
          </div>
          <div style={{ flex: 1, textAlign: "center" }}>
            <div style={{ fontSize: 28, fontWeight: 800, color: "var(--blue)" }}>{fmt(ps.with_url || 0)}</div>
            <div style={{ fontSize: 12, color: "var(--dark)", fontWeight: 600, marginTop: 4 }}>Had Personal Site URL</div>
          </div>
          <div style={{ flex: 0, textAlign: "center", minWidth: 80 }}>
            <div style={{ fontSize: 22, color: "var(--gray)", lineHeight: 1 }}>&rarr;</div>
            <div style={{ marginTop: 6, padding: "3px 8px", background: "#e6f4ea", borderRadius: 4, fontSize: 11, color: "#137333", fontWeight: 600 }}>{ps.capture_pct}%</div>
          </div>
          <div style={{ flex: 1, textAlign: "center" }}>
            <div style={{ fontSize: 28, fontWeight: 800, color: "var(--green)" }}>{fmt(ps.captured || 0)}</div>
            <div style={{ fontSize: 12, color: "var(--dark)", fontWeight: 600, marginTop: 4 }}>Captured to Markdown</div>
          </div>
        </div>
      </div>

      {/* ─── Coverage by Region ─── */}
      <div className="section-title">Coverage by Region</div>
      <div style={{ margin: "-12px 0 12px", fontSize: 12, color: "var(--gray)", fontStyle: "italic" }}>
        How the 450-university input list breaks down by region, and what we extracted. Bar length is proportional to total professors per region.
      </div>
      <div className="panel" style={{ padding: "0 20px" }}>
        {rc.map((r) => {
          const covPct = r.input ? ((r.with_profs / r.input) * 100).toFixed(1) : "0";
          const grad = REGION_COLORS[r.region] || "linear-gradient(90deg,#1a73e8,#4285f4)";
          return (
            <div key={r.region} style={{ padding: "14px 0", borderBottom: "1px solid var(--border)" }}>
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 8 }}>
                <div style={{ fontSize: 15, fontWeight: 700, color: "var(--dark)" }}>{r.label}</div>
                <div style={{ fontSize: 12, color: "var(--gray)" }}>
                  <strong style={{ color: "var(--dark)" }}>{r.with_profs}</strong> / {r.input} unis covered
                  <span style={{ color: "var(--primary)", fontWeight: 600, marginLeft: 6 }}>({covPct}%)</span>
                  <span style={{ margin: "0 10px", opacity: 0.4 }}>|</span>
                  <strong style={{ color: "var(--dark)" }}>{fmt(r.profs)}</strong> professors
                </div>
              </div>
              <div style={{ background: "#f0f2f5", height: 18, borderRadius: 9, overflow: "hidden" }}>
                <div
                  style={{
                    height: "100%",
                    width: `${covPct}%`,
                    background: grad,
                    borderRadius: 9,
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "flex-end",
                    paddingRight: 10,
                    color: "white",
                    fontSize: 11,
                    fontWeight: 700,
                  }}
                >
                  {r.with_profs} / {r.input} &middot; {covPct}%
                </div>
              </div>
            </div>
          );
        })}
      </div>
    </>
  );
}
