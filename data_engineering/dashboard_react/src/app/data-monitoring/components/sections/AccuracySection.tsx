"use client";

import type { ProgramData } from "../../types";
import { fmt } from "../../helpers";

type SectionStat = { with_data: number; verified: number; partial: number; flagged: number; no_data: number; trust: number };

export default function AccuracySection({ data, isUG }: { data: ProgramData; isUG: boolean }) {
  const d = data;
  const s = (d.section_stats || {}) as Record<string, SectionStat>;

  const tuition_matched = (d as unknown as { tuition_matched?: number }).tuition_matched || 0;
  const date_matched = (d as unknown as { date_matched?: number }).date_matched || 0;
  const total_ent_matched = (d as unknown as { total_ent_matched?: number }).total_ent_matched || 0;
  const tuition_total_checkable = (d as unknown as { tuition_total_checkable?: number }).tuition_total_checkable || 0;
  const deadline_total_checkable = (d as unknown as { deadline_total_checkable?: number }).deadline_total_checkable || 0;

  const total_data_points = (d as unknown as { total_data_points?: number }).total_data_points || 0;
  const caveat = (d as unknown as { caveat?: number }).caveat || 0;
  const review = (d as unknown as { review?: number }).review || 0;
  const safe = (d as unknown as { safe?: number }).safe || 0;
  const mostly = (d as unknown as { mostly?: number }).mostly || 0;

  const secData: [string, string, number, number][] = isUG
    ? [
        ["tuition_and_fees", "Tuition & Fees", tuition_matched, tuition_total_checkable],
        ["application_deadlines", "Deadlines", date_matched, deadline_total_checkable],
      ]
    : [
        ["tuition_and_fees", "Tuition & Fees", tuition_matched, 0],
        ["application_deadlines", "Deadlines", date_matched, 0],
        ["admission_requirements", "Admission Req", total_ent_matched, 0],
      ];

  const verifiedPct = d.verified_pct;
  const caveatPct = (caveat / d.total_classified) * 100;
  const cumulative1 = verifiedPct + caveatPct;

  return (
    <>
      <div className="section-title">Important Section Trust Rates</div>
      <div className="gauges">
        {secData.map(([k, name, dp, total]) => {
          const sec = s[k];
          if (!sec) return null;
          return (
            <div key={k} className="gauge">
              <div style={{ fontSize: 13, fontWeight: 700, color: "var(--dark)", marginBottom: 12 }}>{name}</div>
              <div className="gauge-pct">{sec.trust}%</div>
              <div className="gauge-label">trusted</div>
              <div className="gauge-data">{fmt(dp)}{total ? ` / ${fmt(total)}` : ""}</div>
              <div className="gauge-sub">data points verified</div>
              <div className="gauge-sub" style={{ marginTop: 8 }}>{fmt(sec.with_data)} files checked</div>
            </div>
          );
        })}
      </div>

      <div className="section-title">Data Trustworthiness</div>
      <div className="pie-container">
        <div className="pie-wrapper">
          <div
            className="pie-chart"
            style={{
              background: `conic-gradient(
                #34a853 0% ${verifiedPct}%,
                #ffcc80 ${verifiedPct}% ${cumulative1.toFixed(1)}%,
                #ef6c6c ${cumulative1.toFixed(1)}% 100%
              )`,
            }}
          ></div>
          <div className="pie-center">
            <div className="pie-center-num">{fmt(d.total_classified)}</div>
            <div className="pie-center-label">courses</div>
          </div>
          <div className="pie-tooltip">
            <strong>{fmt(d.total_classified)} courses classified</strong><br />
            {fmt(d.verified_combined)} verified ({verifiedPct}%) | {fmt(caveat)} caveat ({caveatPct.toFixed(1)}%) | {fmt(review)} review ({((review / d.total_classified) * 100).toFixed(1)}%)<br />
            {fmt(total_data_points)} data points | {d.classified_unis} universities | {d.num_domains} domains
          </div>
        </div>
        <div className="pie-legend">
          <div className="pie-legend-item pie-big">
            <div className="pie-dot" style={{ background: "#34a853" }}></div>
            <div className="pie-right">
              <span>Verified: {fmt(d.verified_combined)} files ({verifiedPct}%)</span>
              <span className="pie-detail">Safe to present: {fmt(safe)} | Mostly reliable: {fmt(mostly)}</span>
            </div>
          </div>
          <div className="pie-legend-item">
            <div className="pie-dot" style={{ background: "#ffcc80" }}></div>
            <div className="pie-right">
              <span>Use with caveat: {fmt(caveat)} files ({caveatPct.toFixed(1)}%)</span>
              <span className="pie-detail">Some data verified, some unverifiable or mismatched</span>
            </div>
          </div>
          <div className="pie-legend-item">
            <div className="pie-dot" style={{ background: "#ef6c6c" }}></div>
            <div className="pie-right">
              <span>Needs review: {fmt(review)} files ({((review / d.total_classified) * 100).toFixed(1)}%)</span>
              <span className="pie-detail">Insufficient matches, needs manual verification</span>
            </div>
          </div>
          <div style={{ marginTop: 16, padding: "14px 18px", background: "linear-gradient(135deg,#e8f0fe,#f0f4ff)", borderRadius: 10, fontSize: 14, border: "1px solid #d2e3fc" }}>
            <strong style={{ color: "var(--primary)" }}>{fmt(total_data_points)}</strong>{" "}
            <span style={{ color: "var(--gray)" }}>total data points verified against official sources</span>
          </div>
        </div>
      </div>
    </>
  );
}
