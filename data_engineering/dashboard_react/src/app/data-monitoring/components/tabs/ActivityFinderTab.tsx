"use client";

import { useState, useMemo, Fragment } from "react";
import type { ActivitiesData } from "../../types";
import { fmt, getGrad } from "../../helpers";

const ORDER = ["olympiads", "competitions", "internships", "volunteering", "summer_schools"] as const;
const PALETTE = ["#1a73e8", "#34a853", "#fbbc04", "#ea4335", "#9c27b0"];

export default function ActivityFinderTab({ data }: { data: ActivitiesData | undefined }) {
  const [card1Expanded, setCard1Expanded] = useState(false);
  const [card2Expanded, setCard2Expanded] = useState(false);
  // open state for each "act-dc-{ai}-{gi}" density group
  const [openGroups, setOpenGroups] = useState<Set<string>>(new Set());

  const toggleGroup = (id: string) => {
    setOpenGroups((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const orderedKeys = useMemo(() => {
    if (!data?.activities) return [];
    const acts = data.activities;
    const allKeys = Object.keys(acts);
    return ORDER.filter((k) => acts[k]).concat(allKeys.filter((k) => !(ORDER as readonly string[]).includes(k)));
  }, [data]);

  if (!data || !data.activities) {
    return (
      <div className="panel" style={{ padding: 32, textAlign: "center", color: "var(--gray)" }}>
        No Activity Finder data available. Run <code>python dashboard/compute_activity_coverage.py</code> then regenerate.
      </div>
    );
  }

  const acts = data.activities;
  const sum = data.summary || ({} as ActivitiesData["summary"]);
  const totalExtracted = sum.total_extracted || sum.total_records || 0;
  const totalShared = sum.total_shared_for_ui || 0;
  const totalSharedForUi = totalShared;

  return (
    <>
      {/* ─── Top cards (2 only — Total, Activity Types) ─── */}
      <div className="card-row" style={{ gridTemplateColumns: "1fr 1fr" }}>
        <div
          className={`card clickable ${card1Expanded ? "expanded" : ""}`}
          onClick={() => setCard1Expanded((x) => !x)}
        >
          <div className="number">{fmt(totalExtracted)}</div>
          <div className="label">Total Activities</div>
          <div className="sublabel">from extracted/{"{good,avg,poor}"} &middot; click for Shared-for-UI</div>
          <div className="card-dropdown">
            <div style={{ display: "flex", justifyContent: "space-between" }}>
              <span>Shared for UI</span>
              <strong>{fmt(totalShared)}</strong>
            </div>
          </div>
        </div>
        <div
          className={`card clickable ${card2Expanded ? "expanded" : ""}`}
          onClick={() => setCard2Expanded((x) => !x)}
        >
          <div className="number">{sum.total_types || 0}</div>
          <div className="label">Activity Types</div>
          <div className="sublabel">click to see all 5</div>
          <div className="card-dropdown">
            <div
              style={{
                display: "flex",
                flexWrap: "wrap",
                justifyContent: "center",
                alignItems: "center",
                fontSize: 13,
                lineHeight: 1.8,
                whiteSpace: "nowrap",
              }}
            >
              {orderedKeys.map((k, i) => (
                <Fragment key={k}>
                  <span style={{ whiteSpace: "nowrap" }}>
                    <span style={{ color: "var(--primary)", fontWeight: 900, marginRight: 4 }}>&bull;</span>
                    {acts[k].label}
                  </span>
                  {i < orderedKeys.length - 1 && (
                    <span style={{ opacity: 0.4, margin: "0 10px" }}>|</span>
                  )}
                </Fragment>
              ))}
            </div>
          </div>
        </div>
      </div>

      {/* ─── Activity Type Distribution — Extracted ─── */}
      <div className="section-title">Activity Type Distribution &mdash; Extracted</div>
      <div style={{ margin: "-12px 0 12px", fontSize: 12, color: "var(--gray)", fontStyle: "italic" }}>
        Quality-tier split of extracted records per activity type. Green = good, Blue = avg, Red = poor.
        Olympiads has no tier split (single source).
      </div>
      <div className="panel">
        {orderedKeys.map((k) => {
          const a = acts[k];
          const br = a.extracted_breakdown || {};
          const good = br.good || 0;
          const avg = br.avg || 0;
          const poor = br.poor || 0;
          const single = good + avg + poor === 0 ? a.extracted_count || 0 : 0;
          const tot = good + avg + poor + single;
          const gPct = tot ? ((good / tot) * 100).toFixed(1) : "0";
          const aPct = tot ? ((avg / tot) * 100).toFixed(1) : "0";
          const pPct = tot ? ((poor / tot) * 100).toFixed(1) : "0";
          const sPct = tot ? ((single / tot) * 100).toFixed(1) : "0";
          const subtitleParts: string[] = [];
          if (good) subtitleParts.push(`${good} good`);
          if (avg) subtitleParts.push(`${avg} avg`);
          if (poor) subtitleParts.push(`${poor} poor`);
          if (single) subtitleParts.push(`${single} (single-tier)`);
          return (
            <div key={k} className="stacked-section">
              <div className="stacked-header">
                <span className="sh-name">{a.label}</span>
                <span className="sh-total">
                  {tot} extracted &middot; {subtitleParts.join(" · ")}
                </span>
              </div>
              <div className="stacked-bar">
                {good > 0 && (
                  <div className="stacked-seg verified" style={{ width: `${gPct}%` }}>{gPct}%</div>
                )}
                {avg > 0 && (
                  <div className="stacked-seg partial" style={{ width: `${aPct}%` }}>{aPct}%</div>
                )}
                {poor > 0 && (
                  <div className="stacked-seg flagged" style={{ width: `${pPct}%` }}>{pPct}%</div>
                )}
                {single > 0 && (
                  <div className="stacked-seg verified" style={{ width: `${sPct}%` }}>{sPct}%</div>
                )}
              </div>
              <div className="stacked-legend">
                {good > 0 && (
                  <div className="stacked-legend-item">
                    <div className="stacked-legend-dot" style={{ background: "#34a853" }}></div>
                    {good} good ({gPct}%)
                  </div>
                )}
                {avg > 0 && (
                  <div className="stacked-legend-item">
                    <div className="stacked-legend-dot" style={{ background: "#1a73e8" }}></div>
                    {avg} avg ({aPct}%)
                  </div>
                )}
                {poor > 0 && (
                  <div className="stacked-legend-item">
                    <div className="stacked-legend-dot" style={{ background: "#ea4335" }}></div>
                    {poor} poor ({pPct}%)
                  </div>
                )}
                {single > 0 && (
                  <div className="stacked-legend-item">
                    <div className="stacked-legend-dot" style={{ background: "#34a853" }}></div>
                    {single} records (no tier split)
                  </div>
                )}
              </div>
            </div>
          );
        })}
      </div>

      {/* ─── Shared for UI — Folder Breakdown table ─── */}
      <div className="section-title">Shared for UI &mdash; Folder Breakdown</div>
      <div style={{ margin: "-12px 0 12px", fontSize: 12, color: "var(--gray)", fontStyle: "italic" }}>
        Per-folder counts for files actually shared with the UI (processed_* + processed_*_2). Auto-computed
        from disk &mdash; add files to any folder and re-run the compute script to update.
      </div>
      <div className="panel" style={{ padding: 0, overflow: "hidden" }}>
        <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 13 }}>
          <thead>
            <tr style={{ background: "var(--light-gray)", borderBottom: "2px solid var(--border)" }}>
              <th style={{ padding: "10px 14px", textAlign: "left", color: "var(--dark)", fontSize: 12, textTransform: "uppercase", letterSpacing: "0.5px" }}>Type</th>
              <th style={{ padding: "10px 14px", textAlign: "left", color: "var(--dark)", fontSize: 12, textTransform: "uppercase", letterSpacing: "0.5px" }}>Folder</th>
              <th style={{ padding: "10px 14px", textAlign: "right", color: "var(--dark)", fontSize: 12, textTransform: "uppercase", letterSpacing: "0.5px" }}>Records</th>
            </tr>
          </thead>
          <tbody>
            {orderedKeys.map((k) => {
              const a = acts[k];
              const br = a.shared_for_ui_breakdown || {};
              const subtotal = a.shared_for_ui_count || 0;
              const folderNames = Object.keys(br);
              const isOlympiad = k === "olympiads";
              if (folderNames.length === 0) {
                return (
                  <tr key={k}>
                    <td colSpan={3} style={{ padding: "8px 14px", color: "var(--gray)", fontStyle: "italic" }}>
                      No folders configured ({a.label})
                    </td>
                  </tr>
                );
              }
              return (
                <Fragment key={k}>
                  {folderNames.map((fn, i) => {
                    const isFirst = i === 0;
                    return (
                      <tr key={fn} style={{ borderBottom: "1px solid var(--border)" }}>
                        <td style={{ padding: "7px 14px", color: "var(--dark)", fontWeight: isFirst ? 700 : 400, verticalAlign: "top" }}>
                          {isFirst ? a.label : ""}
                        </td>
                        <td style={{ padding: "7px 14px", color: "var(--gray)", fontFamily: "monospace", fontSize: 12 }}>
                          {fn}/
                        </td>
                        <td style={{ padding: "7px 14px", textAlign: "right", color: "var(--dark)", fontWeight: 600 }}>
                          {br[fn]}
                        </td>
                      </tr>
                    );
                  })}
                  {isOlympiad && (
                    <tr style={{ borderBottom: "1px solid var(--border)" }}>
                      <td style={{ padding: "7px 14px" }}></td>
                      <td style={{ padding: "7px 14px", color: "#9aa0a6", fontStyle: "italic", fontSize: 12 }}>
                        (no _2 folder exists)
                      </td>
                      <td style={{ padding: "7px 14px", textAlign: "right", color: "#9aa0a6" }}>&mdash;</td>
                    </tr>
                  )}
                  <tr style={{ background: "#f8f9fa", borderBottom: "2px solid var(--border)" }}>
                    <td style={{ padding: "7px 14px" }}></td>
                    <td style={{ padding: "7px 14px", color: "var(--dark)", fontStyle: "italic" }}>&rarr; subtotal</td>
                    <td style={{ padding: "7px 14px", textAlign: "right", color: "var(--primary)", fontWeight: 700 }}>{subtotal}</td>
                  </tr>
                </Fragment>
              );
            })}
            <tr style={{ background: "linear-gradient(90deg,#e8f0fe,#f0f6ff)" }}>
              <td colSpan={2} style={{ padding: "12px 14px", fontWeight: 800, color: "var(--primary)", fontSize: 14 }}>
                GRAND TOTAL
              </td>
              <td style={{ padding: "12px 14px", textAlign: "right", fontWeight: 800, color: "var(--primary)", fontSize: 16 }}>
                {fmt(totalSharedForUi)}
              </td>
            </tr>
          </tbody>
        </table>
      </div>

      {/* ─── Per-Activity Field Coverage ─── */}
      <div className="section-title">Per-Activity Field Coverage</div>
      <div style={{ margin: "-12px 0 12px", fontSize: 12, color: "var(--gray)", fontStyle: "italic" }}>
        For each activity type, % of records where each field is populated with meaningful content. Click a group to expand.
      </div>
      {orderedKeys.map((k, ai) => {
        const a = acts[k];
        return (
          <div key={k} style={{ marginBottom: 24 }}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", margin: "0 0 10px 0" }}>
              <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
                <div style={{ width: 10, height: 24, background: PALETTE[ai % PALETTE.length], borderRadius: 3 }}></div>
                <div style={{ fontSize: 18, fontWeight: 700, color: "var(--dark)" }}>{a.label}</div>
                <div style={{ fontSize: 12, color: "var(--gray)" }}>
                  {a.total_records} records &middot; {a.avg_coverage_pct}% avg field coverage
                </div>
              </div>
              <div style={{ display: "flex", gap: 10, fontSize: 11 }}>
                {(a.verified_count ?? 0) > 0 && (
                  <span style={{ padding: "3px 8px", background: "#e6f4ea", color: "#137333", borderRadius: 10, fontWeight: 600 }}>
                    {a.verified_count} verified
                  </span>
                )}
                {(a.with_deadlines ?? 0) > 0 && (
                  <span style={{ padding: "3px 8px", background: "#e8f0fe", color: "#1a73e8", borderRadius: 10, fontWeight: 600 }}>
                    {a.with_deadlines} with deadlines
                  </span>
                )}
              </div>
            </div>
            {(a.groups || []).map((g, gi) => {
              const avgFieldPct = g.fields && g.fields.length
                ? Math.round((g.fields.reduce((s, f) => s + (f.pct || 0), 0) / g.fields.length) * 10) / 10
                : 0;
              const dcId = `act-dc-${ai}-${gi}`;
              const open = openGroups.has(dcId);
              return (
                <div key={dcId} className="density-group">
                  <div className="density-header" onClick={() => toggleGroup(dcId)}>
                    <span>{g.group}</span>
                    <span>
                      {avgFieldPct}% Avg Field Coverage{" "}
                      <span style={{ color: "var(--gray)", fontWeight: 500 }}>
                        (avg of {g.fields.length} fields &middot; section present in {g.section_pct}% of records)
                      </span>{" "}
                      &nbsp; &#x25BE;
                    </span>
                  </div>
                  <div className={`density-content ${open ? "open" : ""}`}>
                    {(g.fields || []).map((f) => {
                      const covered = Math.round((f.pct / 100) * a.total_records);
                      const missing = a.total_records - covered;
                      return (
                        <div key={f.name} className="density-field">
                          <div className="df-name">{f.name}</div>
                          <div className="df-bar-bg">
                            <div className="df-bar-fill" style={{ width: `${f.pct}%`, background: getGrad(f.pct) }}></div>
                          </div>
                          <div className="df-pct">{f.pct}%</div>
                          <div className="df-count">
                            <span className="covered">{fmt(covered)}</span> covered
                            <span className="sep">&middot;</span>
                            <span className="missing">{fmt(missing)}</span> missing
                          </div>
                        </div>
                      );
                    })}
                  </div>
                </div>
              );
            })}
          </div>
        );
      })}
    </>
  );
}
