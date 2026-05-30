import type { ProgramData } from "../../types";
import ProgramTab from "../ProgramTab";

export default function MastersTab({
  data,
  regionCoverage,
}: {
  data: ProgramData | undefined;
  regionCoverage?: Record<string, { we_have: number; total_top_universities: number; coverage_pct: number }>;
}) {
  return <ProgramTab data={data} program="masters" regionCoverage={regionCoverage} />;
}
