"use client";

import { useState } from "react";
import type { ProgramData } from "../types";
import StatisticsSection from "./sections/StatisticsSection";
import AccuracySection from "./sections/AccuracySection";
import ExplorerSection from "./sections/ExplorerSection";
import CoverageSection from "./sections/CoverageSection";
import AlertsSection from "./sections/AlertsSection";

type SectionKey = "statistics" | "accuracy" | "explorer" | "coverage" | "alerts";
type ProgramKind = "phd" | "masters" | "ug";

const SECTIONS: { key: SectionKey; label: string }[] = [
  { key: "statistics", label: "Statistics" },
  { key: "accuracy", label: "Accuracy" },
  { key: "explorer", label: "Explorer" },
  { key: "coverage", label: "Coverage" },
  { key: "alerts", label: "Alerts" },
];

export default function ProgramTab({
  data,
  program,
  regionCoverage,
}: {
  data: ProgramData | undefined;
  program: ProgramKind;
  regionCoverage?: Record<string, { we_have: number; total_top_universities: number; coverage_pct: number }>;
}) {
  const [section, setSection] = useState<SectionKey>("statistics");

  if (!data) {
    return (
      <div className="panel" style={{ padding: 32, textAlign: "center", color: "var(--gray)" }}>
        No {program.toUpperCase()} data available.
      </div>
    );
  }

  const isUG = program === "ug";

  return (
    <>
      <div className="section-nav">
        {SECTIONS.map((s) => (
          <div
            key={s.key}
            className={`section-btn ${section === s.key ? "active" : ""}`}
            onClick={() => setSection(s.key)}
          >
            {s.label}
          </div>
        ))}
      </div>

      {section === "statistics" && (
        <StatisticsSection data={data} isUG={isUG} regionCoverage={regionCoverage} />
      )}
      {section === "accuracy" && <AccuracySection data={data} isUG={isUG} />}
      {section === "explorer" && <ExplorerSection data={data} isUG={isUG} />}
      {section === "coverage" && <CoverageSection data={data} />}
      {section === "alerts" && <AlertsSection data={data} isUG={isUG} />}
    </>
  );
}
