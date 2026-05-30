"use client";

import { useEffect, useState, useCallback } from "react";
import type { ActivitiesData, ProfessorsData, ProgramData } from "../types";
import ActivityFinderTab from "./tabs/ActivityFinderTab";
import ProfessorsTab from "./tabs/ProfessorsTab";
import PhDTab from "./tabs/PhDTab";
import MastersTab from "./tabs/MastersTab";
import UGTab from "./tabs/UGTab";

type TabKey = "phd" | "masters" | "ug" | "activities" | "professors";

const TABS: { key: TabKey; label: string; file: string }[] = [
  { key: "phd", label: "PhD", file: "phd.json" },
  { key: "masters", label: "Masters", file: "masters.json" },
  { key: "ug", label: "Undergraduate", file: "ug.json" },
  { key: "activities", label: "Activity Finder", file: "activities.json" },
  { key: "professors", label: "Professors", file: "professors.json" },
];

// Base URL for data fetching — points to the S3 bucket where the 6 split JSON files live.
// To switch data source later, just change this URL.
const DATA_BASE_URL = "https://ambitio-seed-data.s3.ap-south-1.amazonaws.com/data-monitoring";

type TabData = ProgramData | ActivitiesData | ProfessorsData;

export default function DashboardClient() {
  const [currentTab, setCurrentTab] = useState<TabKey>("phd");
  const [cache, setCache] = useState<Partial<Record<TabKey, TabData>>>({});
  const [loadingTab, setLoadingTab] = useState<TabKey | null>(null);
  const [errors, setErrors] = useState<Partial<Record<TabKey, string>>>({});

  const fetchTab = useCallback(async (key: TabKey, file: string) => {
    setLoadingTab(key);
    try {
      const url = `${DATA_BASE_URL}/${file}`;
      const res = await fetch(url);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const json = (await res.json()) as TabData;
      setCache((prev) => ({ ...prev, [key]: json }));
      setErrors((prev) => {
        const next = { ...prev };
        delete next[key];
        return next;
      });
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      setErrors((prev) => ({ ...prev, [key]: msg }));
    } finally {
      setLoadingTab((prev) => (prev === key ? null : prev));
    }
  }, []);

  // Fetch the active tab's data on mount + whenever tab changes (only if not cached yet)
  useEffect(() => {
    if (cache[currentTab] || errors[currentTab]) return;
    const tab = TABS.find((t) => t.key === currentTab);
    if (tab) fetchTab(currentTab, tab.file);
  }, [currentTab, cache, errors, fetchTab]);

  const renderTab = () => {
    if (errors[currentTab]) {
      return (
        <div className="dm-error">
          <strong>Failed to load {currentTab} data:</strong> {errors[currentTab]}
          <div className="dm-error-hint">
            Tried URL: <code>{DATA_BASE_URL}/{TABS.find((t) => t.key === currentTab)?.file}</code>
            <br />
            If you see a CORS error in the browser console, check the S3 bucket&apos;s CORS configuration.
          </div>
        </div>
      );
    }

    if (!cache[currentTab] || loadingTab === currentTab) {
      return <div className="dm-loading">Loading {currentTab} data&hellip;</div>;
    }

    const data = cache[currentTab];

    switch (currentTab) {
      case "phd":
        return (
          <PhDTab
            data={data as ProgramData}
            regionCoverage={(data as ProgramData)?.region_coverage as Record<string, { we_have: number; total_top_universities: number; coverage_pct: number }> | undefined}
          />
        );
      case "masters":
        return (
          <MastersTab
            data={data as ProgramData}
            regionCoverage={(data as ProgramData)?.region_coverage as Record<string, { we_have: number; total_top_universities: number; coverage_pct: number }> | undefined}
          />
        );
      case "ug":
        return (
          <UGTab
            data={data as ProgramData}
            regionCoverage={(data as ProgramData)?.region_coverage as Record<string, { we_have: number; total_top_universities: number; coverage_pct: number }> | undefined}
          />
        );
      case "activities":
        return <ActivityFinderTab data={data as ActivitiesData} />;
      case "professors":
        return <ProfessorsTab data={data as ProfessorsData} />;
      default:
        return null;
    }
  };

  return (
    <div className="dashboard-page">
      <PageHeader />

      <div className="dm-tabbar">
        {TABS.map((t) => (
          <button
            key={t.key}
            type="button"
            className={`dm-tab ${currentTab === t.key ? "active" : ""}`}
            onClick={() => setCurrentTab(t.key)}
          >
            {t.label}
            {loadingTab === t.key && <span className="dm-tab-loading">&middot;&middot;&middot;</span>}
          </button>
        ))}
      </div>

      <div className="dm-content">{renderTab()}</div>
    </div>
  );
}

function PageHeader() {
  return (
    <div className="dm-page-header">
      <h1 className="dm-page-title">Data Monitoring</h1>
      <p className="dm-page-desc">Course data verification and quality monitoring across PhD, Masters, Undergraduate, Activity Finder, and Professors datasets.</p>
    </div>
  );
}
