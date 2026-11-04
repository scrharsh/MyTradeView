export function formatINR(value: number | string | null | undefined): string {
  const amount = Number(value ?? 0);
  return new Intl.NumberFormat("en-IN", { style: "currency", currency: "INR", maximumFractionDigits: 2 }).format(amount);
}

export function formatNumber(value: number | string | null | undefined, maximumFractionDigits = 2): string {
  return new Intl.NumberFormat("en-IN", { maximumFractionDigits }).format(Number(value ?? 0));
}

export function formatPercent(value: number | string | null | undefined): string {
  return `${Number(value ?? 0).toFixed(2)}%`;
}

export function getPnlColor(value: number | string | null | undefined): string {
  return Number(value ?? 0) >= 0 ? "text-emerald-400" : "text-rose-400";
}

export function formatISTTime(value: string | Date): string {
  return new Date(value).toLocaleTimeString("en-IN", { timeZone: "Asia/Kolkata" });
}

export function formatISTDate(value: string | Date): string {
  return new Date(value).toLocaleDateString("en-IN", { timeZone: "Asia/Kolkata" });
}

export function formatISTDateTime(value: string | Date): string {
  return `${formatISTDate(value)} ${formatISTTime(value)}`;
}
