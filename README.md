# MyTradeView: Automated NIFTY ORB Trading Executor

`MyTradeView` is an institutional-grade, event-driven automated trading platform built in Python 3.10+ for personal quantitative trading on the National Stock Exchange of India (NSE) using the **FYERS API v3**.

It faithfully implements a validated **NIFTY Opening Range Breakout (ORB)** intraday options strategy originally modeled on **StockMock**.

---

## Key Features

- **Strict Strategy Fidelity:** Faithful implementation of the StockMock NIFTY ORB strategy (09:16 - 09:45 IST range, Monday/Thursday/Friday sessions, ATM strike selection, 1 lot, intraday).
- **Dual Risk Safeguards:**
  - Hard daily loss threshold: **-₹3,000** kill switch (emergency square-off and session lockout).
  - Profit protection trigger: **+₹3,000** MTM activates minimum locked floor of **+₹500** with configurable trailing logic.
- **Atomic Reversal Sequencing:** Guaranteed exit of existing option position before opening reverse position. Zero naked double exposure.
- **Three Execution Modes:**
  - `simulation`: Historical discrete event replay using 1-minute historical data.
  - `paper`: Real-time market feed via FYERS WebSockets with simulated order fills and slippage modeling.
  - `live`: Direct broker execution via FYERS API v3 (Hard-disabled by default, requires explicit confirmation).
- **Crash Recovery & Reconciliation:** State persisted in PostgreSQL (production) or SQLite (testing). Background reconciliation worker synchronizes local OMS with FYERS broker state every 5 seconds.

---

## Architecture Overview

Detailed architectural design is documented in [ARCHITECTURE.md](ARCHITECTURE.md).

```
MyTradeView/
├── ARCHITECTURE.md          # Comprehensive 12-module system architecture
├── IMPLEMENTATION_PLAN.md   # 10-phase implementation roadmap & milestones
├── CONFIGURATION.md         # Validated configuration reference & environment variables
├── RISK_CONTROLS.md         # Multi-tiered risk controls & kill switch specifications
├── pyproject.toml           # Modern Python packaging configuration
├── requirements.txt         # Dependencies list
├── .env.example             # Template for local environment configuration
├── config/                  # Pydantic v2 Settings and configuration schemas
├── src/
│   ├── common/              # Enums, exceptions, logging, IST time utilities
│   ├── core/                # Domain models (Ticks, Candles, Orders, Positions)
│   ├── strategy/            # Opening Range Breakout & Reversal engine
│   ├── data/                # Market data providers & Option symbol resolver
│   ├── backtest/            # Backtesting engine & StockMock parity validation
│   ├── risk/                # Pre-trade risk, MTM tracker, kill switch
│   ├── broker/              # FYERS API v3 REST & WebSocket adapter
│   ├── oms/                 # Order management system & reconciliation worker
│   ├── storage/             # PostgreSQL / SQLite async persistence layer
│   ├── scheduler/           # Market session scheduler (09:16 - 15:10 IST)
│   └── monitoring/          # Alerts (Telegram/Slack) & health checks
└── tests/
    ├── unit/                # Unit test suite (Strategy, Risk, OMS, Resolver)
    └── integration/         # Integration test suite (DB, Broker Adapter, Replay)
```

---

## Quick Start (Simulation / Local Testing)

### 1. Prerequisites
- Python 3.10+
- PostgreSQL 14+ (or local SQLite for development)
- Git

### 2. Installation
```bash
# Clone the repository
git clone <repo-url> MyTradeView
cd MyTradeView

# Create and activate virtual environment
python -m venv venv
.\venv\Scripts\Activate.ps1  # Windows PowerShell
# source venv/bin/activate    # Linux / macOS

# Install dependencies
pip install -r requirements.txt
```

### 3. Configuration
```bash
# Copy template configuration
cp .env.example .env

# Edit .env with your FYERS credentials and preferred database URL
```

### 4. Running Tests
```bash
pytest -v
```

---

## Safety & Disclaimer

This software executes financial transactions in derivative markets carrying substantial risk of financial loss. Live mode (`EXECUTION_MODE=live`) must never be engaged without completing backtesting, paper trading, and verifying pre-trade risk controls.

