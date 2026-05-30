"use client";

import { useState } from "react";
import type { ProgramData } from "../../types";
import { fmt, getGrad } from "../../helpers";

type SectionStat = { with_data: number; verified: number; partial: number; flagged: number; no_data: number; trust: number };

const DOMAIN_ORDER = [
  "CS & AI",
  "Engineering",
  "Business & Economics",
  "Natural Sciences",
  "Medicine & Health",
  "Social Sciences & Humanities",
  "Arts & Design",
  "Environment & Sustainability",
  "Other",
];

const SECTION_NAMES: Record<string, string> = {
  admission_requirements: "Admission Requirements",
  application_deadlines: "Application Deadlines",
  tuition_and_fees: "Tuition & Fees",
};

const TIERS = [
  { key: "full", label: "100%", color: "#34a853" },
  { key: "high", label: "75-99%", color: "#4caf50" },
  { key: "mid", label: "50-74%", color: "#1a73e8" },
  { key: "low", label: "25-49%", color: "#4285f4" },
  { key: "minimal", label: "1-24%", color: "#fbbc04" },
  { key: "zero", label: "0%", color: "#ea4335" },
] as const;

export default function CoverageSection({ data }: { data: ProgramData }) {
  const [openGroups, setOpenGroups] = useState<Set<number>>(new Set());
  const toggleGroup = (i: number) =>
    setOpenGroups((p) => {
      const n = new Set(p);
      if (n.has(i)) n.delete(i);
      else n.add(i);
      return n;
    });

  const d = data;
  const s = (d.section_stats || {}) as Record<string, SectionStat>;
  const dc = ((d as unknown as { domain_coverage?: Record<string, { total: number; reliable: number }> }).domain_coverage || {}) as Record<string, { total: number; reliable: number }>;
  const sortedDom = Object.entries(dc).sort((a, b) => {
    const ai = DOMAIN_ORDER.indexOf(a[0]);
    const bi = DOMAIN_ORDER.indexOf(b[0]);
    return (ai === -1 ? 99 : ai) - (bi === -1 ? 99 : bi);
  });

  const p = d.pipeline || { stage1_total: 0, stage2_url_complete: 0, stage3_classified: 0, dropped_url: 0, dropped_content: 0 };
  const uc = (d.uni_completion || { buckets: {}, examples: {} }) as { buckets: Record<string, number>; examples: Record<string, { name: string; classified: number; total: number }[]> };
  const fieldCoverage = d.field_coverage || [];
  const totalFiles = d.total_courses_all || 0;

  return (
    <>
      <div className="section-title">Pipeline Funnel</div>
      <div style={{ margin: "-12px 0 12px", fontSize: 12, color: "var(--gray)", fontStyle: "italic" }}>
        How programs flow through the verification pipeline. Drops show where data is lost.
      </div>
      <div className="panel" style={{ padding: 24 }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 12 }}>
          <div style={{ flex: 1, textAlign: "center" }}>
            <div style={{ fontSize: 36, fontWeight: 800, color: "var(--primary)" }}>{fmt(p.stage1_total)}</div>
            <div style={{ fontSize: 13, color: "var(--dark)", fontWeight: 600, marginTop: 4 }}>Programs</div>
            <div style={{ fontSize: 11, color: "#9aa0a6" }}>md files on disk</div>
          </div>
          <div style={{ flex: 0, textAlign: "center", minWidth: 90 }}>
            <div style={{ fontSize: 24, color: "var(--gray)", lineHeight: 1 }}>&rarr;</div>
            <div style={{ marginTop: 6, padding: "4px 8px", background: "#fce8e6", borderRadius: 4, fontSize: 11, color: "#ea4335", fontWeight: 600 }}>-{fmt(p.dropped_url)}</div>
            <div style={{ fontSize: 10, color: "#9aa0a6", marginTop: 2 }}>missing URLs</div>
          </div>
          <div style={{ flex: 1, textAlign: "center" }}>
            <div style={{ fontSize: 36, fontWeight: 800, color: "var(--primary)" }}>{fmt(p.stage2_url_complete)}</div>
            <div style={{ fontSize: 13, color: "var(--dark)", fontWeight: 600, marginTop: 4 }}>Pages Ready</div>
            <div style={{ fontSize: 11, color: "#9aa0a6" }}>all URLs crawled</div>
          </div>
          <div style={{ flex: 0, textAlign: "center", minWidth: 90 }}>
            <div style={{ fontSize: 24, color: "var(--gray)", lineHeight: 1 }}>&rarr;</div>
            <div style={{ marginTop: 6, padding: "4px 8px", background: "#fce8e6", borderRadius: 4, fontSize: 11, color: "#ea4335", fontWeight: 600 }}>-{fmt(p.dropped_content)}</div>
            <div style={{ fontSize: 10, color: "#9aa0a6", marginTop: 2 }}>empty content</div>
          </div>
          <div style={{ flex: 1, textAlign: "center" }}>
            <div style={{ fontSize: 36, fontWeight: 800, color: "var(--green)" }}>{fmt(p.stage3_classified)}</div>
            <div style={{ fontSize: 13, color: "var(--dark)", fontWeight: 600, marginTop: 4 }}>Classified</div>
            <div style={{ fontSize: 11, color: "#9aa0a6" }}>fully verified</div>
          </div>
        </div>
      </div>

      <div className="section-title">University Completion Distribution</div>
      <div style={{ margin: "-12px 0 12px", fontSize: 12, color: "var(--gray)", fontStyle: "italic" }}>
        % of programs per university that made it through the full classification pipeline.
      </div>
      <div className="panel">
        {TIERS.map((t) => {
          const count = uc.buckets?.[t.key] || 0;
          const examples = (uc.examples?.[t.key] || []).slice(0, 3);
          const exampleStr = examples.map((e) => `${e.name} (${e.classified}/${e.total})`).join(" · ");
          return (
            <div key={t.key} style={{ display: "flex", alignItems: "center", gap: 14, padding: "8px 0", borderBottom: "1px solid var(--border)" }}>
              <div style={{ width: 14, height: 14, background: t.color, borderRadius: 3, flexShrink: 0 }}></div>
              <div style={{ width: 70, fontWeight: 700, color: "var(--dark)" }}>{t.label}</div>
              <div style={{ width: 120, fontSize: 13 }}><strong>{count}</strong> universities</div>
              <div style={{ flex: 1, fontSize: 11, color: "#5f6368" }}>
                {exampleStr || <em>none</em>}
              </div>
            </div>
          );
        })}
      </div>

      <div className="section-title">Section Data Coverage</div>
      <div style={{ margin: "-12px 0 12px", fontSize: 12, color: "var(--gray)", fontStyle: "italic", lineHeight: 1.6 }}>
        <strong>covered</strong> = file had section data on both md AND crawled page (could be verified/partial/flagged).<br />
        <strong>missing</strong> = file was marked &quot;no_data&quot; for this section (either md is missing the section, OR crawled page returned empty content).
      </div>
      <div className="panel">
        {["admission_requirements", "application_deadlines", "tuition_and_fees"]
          .filter((k) => s[k])
          .map((k) => {
            const sec = s[k];
            const pct = ((sec.with_data / d.total_classified) * 100).toFixed(1);
            return (
              <div key={k} className="progress-item">
                <div className="progress-label">
                  <span>{SECTION_NAMES[k]}</span>
                  <span>{fmt(sec.with_data)} covered | {fmt(sec.no_data)} missing</span>
                </div>
                <div className="progress-bar">
                  <div className="progress-fill green" style={{ width: `${pct}%` }}>{pct}%</div>
                </div>
              </div>
            );
          })}
      </div>

      {fieldCoverage.length > 0 && (
        <>
          <div className="section-title">Data Density &amp; Field-Wise Coverage</div>
          <div style={{ margin: "-12px 0 12px", fontSize: 12, color: "var(--gray)", fontStyle: "italic" }}>
            How many programs have specific data fields filled (vs. &quot;Information not available&quot;). Click a category to expand.
          </div>
          <div>
            {fieldCoverage.map((g, idx) => {
              const avgFieldPct = g.fields && g.fields.length
                ? Math.round((g.fields.reduce((acc, f) => acc + (f.pct || 0), 0) / g.fields.length) * 10) / 10
                : 0;
              const open = openGroups.has(idx);
              return (
                <div key={idx} className="density-group">
                  <div className="density-header" onClick={() => toggleGroup(idx)}>
                    <span>{g.group}</span>
                    <span>
                      {g.fields.length > 0 ? (
                        <>
                          {avgFieldPct}% Avg Field Coverage{" "}
                          <span style={{ color: "var(--gray)", fontWeight: 500 }}>
                            (avg of {g.fields.length} fields &middot; section present in {g.section_pct}% of files)
                          </span>
                        </>
                      ) : (
                        "Expand Details"
                      )}{" "}
                      &nbsp; &#x25BE;
                    </span>
                  </div>
                  <div className={`density-content ${open ? "open" : ""}`}>
                    {g.fields.map((f) => {
                      const covered = Math.round((f.pct / 100) * totalFiles);
                      const missing = totalFiles - covered;
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
        </>
      )}

      <div className="section-title">Coverage by Academic Domain</div>
      <div style={{ margin: "-12px 0 12px", fontSize: 12, color: "var(--gray)", fontStyle: "italic" }}>
        <strong>% reliable</strong> = % of courses in that field with confidence_score &ge; 60 (safe_to_present_as_official OR mostly_reliable).
      </div>
      <div className="panel">
        {sortedDom.map(([name, val]) => {
          const relPct = val.total > 0 ? ((val.reliable / val.total) * 100).toFixed(0) : "0";
          return (
            <div key={name} className="entity-row">
              <div className="entity-name" style={{ width: 200 }}>{name} ({val.total})</div>
              <div className="entity-bar-bg">
                <div className="entity-bar-fill" style={{ width: `${relPct}%`, background: getGrad(Number(relPct)) }}></div>
              </div>
              <div className="entity-rate">{relPct}% reliable</div>
            </div>
          );
        })}
      </div>
    </>
  );
}
