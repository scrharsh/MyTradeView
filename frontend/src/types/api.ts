export interface SystemHealthResponse {
  status: string;
  execution_mode: string;
  timestamp: string;
  broker_connected: boolean;
  broker_status: string;
  market_data_healthy: boolean;
  market_data_stale: boolean;
  websocket_state: string;
  database_connected: boolean;
  active_session_id?: string | null;
  last_reconciliation_time?: string | null;
  is_reconciled: boolean;
}

export interface StrategyParameterDef {
  name: string;
  param_type: string;
  default_value: any;
  description: string;
  is_required: boolean;
  allowed_values?: any[] | null;
}

export interface StrategyTemplateResponse {
  template_type: string;
  display_name: string;
  description: string;
  supported_underlyings: string[];
  supported_order_types: number[];
  parameters: Record<string, StrategyParameterDef>;
}

export interface StrategyValidateResponse {
  is_valid: boolean;
  validated_parameters: Record<string, any>;
  errors: string[];
  unresolved_decisions: string[];
}

export interface StrategyConfigVersionResponse {
  config_id: string;
  version: number;
  underlying_symbol: string;
  parameters: Record<string, any>;
  is_active: boolean;
  created_at: string;
}

export interface CandleResponseItem {
  timestamp: string;
  open: number | string;
  high: number | string;
  low: number | string;
  close: number | string;
  volume: number;
}

export interface CandlesResponse {
  symbol: string;
  resolution: string;
  provenance: string;
  is_synthetic: boolean;
  candle_count: number;
  candles: CandleResponseItem[];
}

export interface InstrumentResponseItem {
  symbol: string;
  exchange: string;
  instrument_type: string;
  underlying: string;
  strike?: number | string | null;
  option_type?: string | null;
  expiry_date?: string | null;
  lot_size: number;
  tick_size: number | string;
  is_active: boolean;
}

export interface OpeningRangeResponse {
  is_established: boolean;
  high?: number | string | null;
  low?: number | string | null;
  range_width?: number | string | null;
  start_time: string;
  end_time: string;
  candle_count: number;
}

export interface SessionActiveResponse {
  session_id: string;
  trading_date: string;
  execution_mode: string;
  phase: string;
  is_active: boolean;
  started_at: string;
  ended_at?: string | null;
  strategy_state: string;
  opening_range: OpeningRangeResponse;
  active_contract_symbol?: string | null;
}

export interface RiskSnapshotResponse {
  session_id: string;
  timestamp: string;
  realized_pnl: number | string;
  unrealized_pnl: number | string;
  total_mtm: number | string;
  peak_mtm: number | string;
  current_drawdown: number | string;
  max_daily_loss_limit: number | string;
  is_circuit_breaker_active: boolean;
  circuit_breaker_reason?: string | null;
  is_kill_switch_active: boolean;
  kill_switch_reason?: string | null;
  is_profit_protection_active: boolean;
  profit_protection_status: string;
  profit_floor: number | string;
  open_positions_count: number;
  is_data_stale: boolean;
  is_synthetic?: boolean;
  data_source?: string;
}

export interface OrderTransitionResponseItem {
  from_status: string;
  to_status: string;
  filled_qty: number;
  avg_price: number | string;
  message: string;
  timestamp: string;
}

export interface FillResponseItem {
  fill_id: string;
  client_order_id: string;
  broker_order_id?: string | null;
  symbol: string;
  side: string;
  fill_price: number | string;
  fill_qty: number;
  timestamp: string;
}

export interface OrderResponseItem {
  client_order_id: string;
  broker_order_id?: string | null;
  session_id: string;
  symbol: string;
  side: string;
  order_type: string;
  product_type: string;
  qty: number;
  filled_qty: number;
  remaining_qty: number;
  oms_status: string;
  status: string;
  avg_fill_price: number | string;
  limit_price: number | string;
  tag: string;
  status_message: string;
  submitted_at: string;
  updated_at: string;
  transitions: OrderTransitionResponseItem[];
  fills: FillResponseItem[];
  is_synthetic?: boolean;
  data_source?: string;
}

export interface PositionResponseItem {
  position_id: string;
  session_id: string;
  symbol: string;
  product_type: string;
  net_qty: number;
  buy_qty: number;
  sell_qty: number;
  avg_buy_price: number | string;
  avg_sell_price: number | string;
  current_ltp: number | string;
  realized_pnl: number | string;
  unrealized_pnl: number | string;
  total_pnl: number | string;
  is_open: boolean;
  opened_at: string;
  closed_at?: string | null;
  is_synthetic?: boolean;
  data_source?: string;
}

export interface BacktestEquityPoint {
  timestamp: string;
  equity: number | string;
  drawdown: number | string;
}

export interface BacktestTradeItem {
  trade_id: string;
  symbol: string;
  side: string;
  entry_time: string;
  entry_price: number | string;
  exit_time: string;
  exit_price: number | string;
  qty: number;
  gross_pnl: number | string;
  transaction_costs: number | string;
  net_pnl: number | string;
  exit_reason: string;
  duration_minutes: number;
}

export interface BacktestSummaryResponse {
  run_id: string;
  dataset_id: string;
  start_date: string;
  end_date: string;
  strategy_name: string;
  strategy_version: number;
  is_synthetic: boolean;
  net_profit: number | string;
  total_trades: number;
  win_rate_pct: number | string;
  profit_factor: number | string;
  max_drawdown_amount: number | string;
  max_drawdown_pct: number | string;
  sharpe_ratio: number | string;
  expectancy: number | string;
  created_at: string;
}

export interface BacktestDetailsResponse {
  summary: BacktestSummaryResponse;
  metrics: Record<string, any>;
  diagnostics: Record<string, any>;
  equity_curve: BacktestEquityPoint[];
  trades: BacktestTradeItem[];
}

export interface BenchmarkResponse {
  matched_trades_count: number;
  discrepancy_count: number;
  pnl_diff: number | string;
  is_matched: boolean;
  summary: string;
}

export interface ReconciliationReport {
  is_synchronized: boolean;
  is_broker_available: boolean;
  timestamp: string;
  internal_orders_count: number;
  broker_orders_count: number;
  internal_positions_count: number;
  broker_positions_count: number;
  discrepancies: Array<{
    category: string;
    symbol_or_id: string;
    description: string;
    severity: string;
  }>;
  summary: string;
}

// Convenience Type Aliases for Workstation Components
export type BacktestDetailResponse = BacktestDetailsResponse;
export type BacktestSummaryItem = BacktestSummaryResponse;
export type StockMockBenchmarkResponse = BenchmarkResponse;
export type CandleItem = CandleResponseItem;
export type PositionItem = PositionResponseItem;
export type BrokerOrderItem = OrderResponseItem;
export type ReconciliationResultResponse = ReconciliationReport;
export type StrategyConfigItem = StrategyConfigVersionResponse;
export type StrategyValidationResult = StrategyValidateResponse;
export type StrategyTemplateItem = StrategyTemplateResponse;
export interface AuditEventItem {
  event_id?: string;
  session_id?: string;
  timestamp: string;
  event_type: string;
  component?: string;
  actor?: string;
  details: Record<string, any> | string;
}

