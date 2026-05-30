// Type definitions for dashboard_data.json
// Loose types — we only define what's actively used by the UI.

export type ProgramKey = "phd" | "masters" | "ug";

export interface FieldCov {
  name: string;
  pct: number;
  count?: number;
}

export interface FieldGroup {
  group: string;
  section_pct: number;
  fields: FieldCov[];
}

export interface ActivityType {
  label: string;
  total_records: number;
  verified_count?: number;
  with_deadlines?: number;
  avg_coverage_pct: number;
  groups: FieldGroup[];
  extracted_count?: number;
  extracted_breakdown?: Record<string, number>;
  shared_for_ui_count?: number;
  shared_for_ui_breakdown?: Record<string, number>;
  deadline_count?: number;
  deadline_field?: string;
}

export interface ActivitiesData {
  activities: Record<string, ActivityType>;
  summary: {
    total_records: number;
    total_types: number;
    verified_total: number;
    with_deadlines_total: number;
    avg_coverage_pct: number;
    total_extracted?: number;
    total_shared_for_ui?: number;
    total_deadlines?: number;
  };
}

export interface ProfessorRegion {
  region: string;
  label: string;
  input: number;
  folder_created: number;
  with_profs: number;
  profs: number;
  coverage_pct: number;
}

export interface ProfessorDept {
  dept: string;
  attempted: number;
  ok: number;
  with_profs: number;
  profs: number;
  success_pct: number;
  avg_per_file: number;
  bucket_ok?: number;
  bucket_dne?: number;
  bucket_failed?: number;
  bucket_total?: number;
}

export interface ProfessorsData {
  generated_at: string;
  summary: {
    total_professors: number;
    total_universities: number;
    input_universities: number;
    unis_with_data: number;
    unis_with_ok: number;
    total_dept_files: number;
    unique_departments: number;
    max_possible_pairs: number;
    total_attempts: number;
    ok_count: number;
    skipped_count: number;
    error_count: number;
    parse_error_count: number;
    success_pct: number;
    success_pct_of_attempts: number;
    avg_profs_per_uni: number;
    avg_profs_per_uni_with_data: number;
    uni_max_name: string | null;
    uni_max_count: number;
    uni_min_name: string | null;
    uni_min_count: number;
    uni_median_count: number;
    countries: number;
  };
  pipeline: {
    statuses: Record<string, number>;
    strategies: Record<string, number>;
    skip_reasons_top: { reason: string; count: number }[];
  };
  field_coverage: { name: string; count: number; pct: number }[];
  dept_coverage: ProfessorDept[];
  regional_coverage: ProfessorRegion[];
  top_universities: { name: string; folder: string; country: string; profs: number }[];
  personal_sites: {
    total_profs: number;
    with_url: number;
    captured: number;
    url_pct: number;
    capture_pct: number;
  };
}

// Loose program data — used by PhD/Masters/UG tabs.
// Defined as Record<string, any> here; the actual structure is huge and we'll
// type-narrow inside the components as needed.
export type ProgramData = {
  program_type: string;
  total_classified: number;
  total_courses_all: number;
  classified_unis: number;
  total_unis_all: number;
  verified_combined: number;
  verified_pct: number;
  avg_courses_per_uni: number;
  num_domains: number;
  section_stats?: Record<string, { with_data: number; verified: number; partial: number; flagged: number; no_data: number; trust: number }>;
  alerts?: Record<string, number>;
  region_coverage?: Record<string, { we_have: number; total_top_universities: number; coverage_pct: number }>;
  field_coverage?: FieldGroup[];
  uni_completion?: { buckets: Record<string, number>; examples: Record<string, unknown[]> };
  pipeline?: { stage1_total: number; stage2_url_complete: number; stage3_classified: number; dropped_url: number; dropped_content: number };
  entity_stats?: Record<string, { matched: number; mismatched: number; total: number; rate: number }>;
  universities?: unknown[];
  // Allow any extra fields not strictly defined here
  [key: string]: unknown;
};

export interface DashboardData {
  phd?: ProgramData;
  masters?: ProgramData;
  ug?: ProgramData;
  activities?: ActivitiesData;
  professors?: ProfessorsData;
  region_coverage?: Record<string, { we_have: number; total_top_universities: number; coverage_pct: number }>;
}
