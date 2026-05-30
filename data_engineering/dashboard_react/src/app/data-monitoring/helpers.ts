// Mirrors helper functions from the current build_dashboard.py JS

export function fmt(n: unknown): string {
  if (typeof n === "number") return n.toLocaleString();
  return String(n ?? "");
}

export function getColor(r: number): string {
  if (r >= 80) return "var(--green)";
  if (r >= 60) return "var(--blue)";
  if (r >= 40) return "var(--yellow)";
  return "var(--red)";
}

export function getGrad(r: number): string {
  if (r >= 80) return "linear-gradient(90deg,#34a853,#4caf50)";
  if (r >= 60) return "linear-gradient(90deg,#1a73e8,#4285f4)";
  if (r >= 40) return "linear-gradient(90deg,#f9ab00,#fbbc04)";
  return "linear-gradient(90deg,#ea4335,#ef5350)";
}
