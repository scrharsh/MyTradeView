import type {
  AuditEventItem,
  BacktestDetailsResponse,
  BacktestSummaryResponse,
  BenchmarkResponse,
  CandlesResponse,
  PositionResponseItem,
  ReconciliationReport,
  RiskSnapshotResponse,
  SessionActiveResponse,
  StrategyConfigVersionResponse,
  StrategyTemplateResponse,
  StrategyValidateResponse,
  SystemHealthResponse,
  OrderResponseItem,
  MarketDataStatusResponse,
} from "../types/api";

const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000/api/v1";
type ActionResponse = { message?: string; [key: string]: unknown };
type AuthResponse = { login_url?: string; authenticated?: boolean; reason?: string; [key: string]: unknown };

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
  });
  if (!response.ok) {
    const body = await response.text();
    throw new Error(body || `Request failed (${response.status})`);
  }
  return response.json() as Promise<T>;
}

export const api = {
  getHealth: () => request<SystemHealthResponse>("/health"),
  getMarketDataStatus: () => request<MarketDataStatusResponse>("/market-data/status"),
  getLiveFeedStatus: () => request<Record<string, unknown>>("/market-data/live/status"),
  startLiveFeed: () => request<Record<string, unknown>>("/market-data/live/start", { method: "POST" }),
  stopLiveFeed: () => request<Record<string, unknown>>("/market-data/live/stop", { method: "POST" }),
  getActiveSession: () => request<SessionActiveResponse>("/sessions/active"),
  getOrders: () => request<OrderResponseItem[]>("/orders"),
  getPositions: () => request<PositionResponseItem[]>("/positions"),
  squareOffPosition: (symbol: string) =>
    request<PositionResponseItem>(`/orders/positions/${encodeURIComponent(symbol)}/square-off`, { method: "POST" }),
  getRiskSnapshot: () => request<RiskSnapshotResponse>("/risk/snapshot"),
  getAuditEvents: (limit = 50) => request<AuditEventItem[]>(`/risk/audit-events?limit=${limit}`),
  triggerKillSwitch: (reason: string) =>
    request<Record<string, unknown>>("/risk/kill-switch", { method: "POST", body: JSON.stringify({ reason }) }),
  runReconciliation: () => request<ReconciliationReport>("/reconciliation/run", { method: "POST" }),
  getLatestReconciliation: () => request<ReconciliationReport>("/reconciliation/latest"),
  clearOrders: () => request<ActionResponse>("/sessions/clear-orders", { method: "POST" }),
  seedDemoOrders: () => request<ActionResponse>("/sessions/seed-demo", { method: "POST" }),
  getCandles: (symbol: string, resolution: string) =>
    request<CandlesResponse>(`/market-data/candles?symbol=${encodeURIComponent(symbol)}&resolution=${encodeURIComponent(resolution)}`),
  getStrategyTemplates: () => request<StrategyTemplateResponse[]>("/strategies/templates"),
  getStrategyConfigs: () => request<StrategyConfigVersionResponse[]>("/strategies"),
  getActiveStrategyConfig: () => request<StrategyConfigVersionResponse>("/strategies/active"),
  validateStrategy: (body: unknown) =>
    request<StrategyValidateResponse>("/strategies/validate", { method: "POST", body: JSON.stringify(body) }),
  createStrategyConfig: (body: unknown) =>
    request<StrategyConfigVersionResponse>("/strategies", { method: "POST", body: JSON.stringify(body) }),
  createStrategyVersion: (configId: string, body: unknown) =>
    request<StrategyConfigVersionResponse>(`/strategies/${encodeURIComponent(configId)}/versions`, { method: "POST", body: JSON.stringify(body) }),
  activateStrategyConfig: (configId: string) =>
    request<StrategyConfigVersionResponse>(`/strategies/${encodeURIComponent(configId)}/activate`, { method: "POST" }),
  getBacktestRuns: () => request<BacktestSummaryResponse[]>("/backtest/runs"),
  getBacktestRun: (runId: string) => request<BacktestDetailsResponse>(`/backtest/${encodeURIComponent(runId)}`),
  runBacktest: (body: unknown) => request<BacktestDetailsResponse>("/backtest/run", { method: "POST", body: JSON.stringify(body) }),
  benchmarkStockMock: (runId: string, stockmockCsvContent: string) =>
    request<BenchmarkResponse>("/backtest/benchmark", { method: "POST", body: JSON.stringify({ run_id: runId, stockmock_csv_content: stockmockCsvContent }) }),
  getFyersLoginUrl: () => request<AuthResponse>("/auth/fyers/login-url"),
  exchangeFyersCode: (auth_code_or_url: string) =>
    request<AuthResponse>("/auth/fyers/exchange", { method: "POST", body: JSON.stringify({ auth_code_or_url }) }),
  validateFyers: () => request<AuthResponse>("/auth/fyers/status?validate=true"),
  fyersLogout: () => request<AuthResponse>("/auth/fyers/logout", { method: "POST" }),
};
