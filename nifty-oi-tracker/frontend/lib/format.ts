/** Indian comma formatting: 1,23,456 */
export function fmt(n: number | null | undefined, decimals = 0): string {
  if (n == null) return "--";
  return n.toLocaleString("en-IN", {
    maximumFractionDigits: decimals,
    minimumFractionDigits: decimals,
  });
}

/** Signed with ▲/▼ and commas: ▲ +1,23,456 */
export function fmtSigned(n: number | null | undefined, decimals = 0): string {
  if (n == null) return "--";
  const s = fmt(Math.abs(n), decimals);
  if (n > 0) return `▲ +${s}`;
  if (n < 0) return `▼ -${s}`;
  return s;
}

/** Compact for chart axes: 1.2L, 45K, 100 */
export function fmtCompact(n: number): string {
  const abs = Math.abs(n);
  const sign = n < 0 ? "-" : "";
  if (abs >= 10000000) return sign + (abs / 10000000).toFixed(1) + "Cr";
  if (abs >= 100000) return sign + (abs / 100000).toFixed(1) + "L";
  if (abs >= 1000) return sign + (abs / 1000).toFixed(0) + "K";
  return n.toString();
}

/** Color class for directional values */
export function colorDir(n: number | null | undefined): string {
  if (n == null || n === 0) return "";
  return n > 0 ? "text-green-500" : "text-red-500";
}
