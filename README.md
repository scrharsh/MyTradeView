# MyTradeView — Automated NIFTY ORB Live Trading Workstation

MyTradeView is a **live automated algorithmic trading workstation** built in Python 3.10+ for executing intraday NIFTY options strategies through the user's own **FYERS brokerage account using FYERS API v3**.

The platform is designed to operate as a real trading system, connecting directly to live NSE market data and the FYERS broker infrastructure to identify trading opportunities, manage positions, execute orders, and monitor portfolio-level risk in real time.

Its primary objective is to automate the execution of a defined **NIFTY Opening Range Breakout (ORB) strategy**, based on the strategy logic modeled on StockMock, while maintaining strict execution discipline, broker-state synchronization, and automated risk protection.

MyTradeView is engineered around actual brokerage execution rather than simulated trading. Historical replay, backtesting, and paper execution are supporting capabilities used for strategy validation, development, and system verification.

**Primary operating objective:** Real-money intraday trading through the user's own FYERS account, with automated order execution, position management, reversal sequencing, and capital protection.

### Execution Architecture

MyTradeView is designed as a live-first trading system with supporting validation environments.

- **Live Execution (Primary):** Connects to FYERS API v3 for real-time market data, order placement, modification, cancellation, position tracking, and broker reconciliation. Trades are executed through the user's own authorized FYERS brokerage account.
- **Historical Simulation (Validation):** Replays historical market data to validate strategy behavior, execution sequencing, and risk-management logic.
- **Paper Execution (Testing):** Uses live market data with simulated order execution to verify system behavior without placing real orders.

Live execution requires valid FYERS authentication, explicit authorization, and operational risk checks.