# Changelog

## 1.0.0 - 2026-11-09

First public release.

- Autonomous loop: scan, analyst review, risk verdict, execution, exit management
- 7-factor explainable quant score (trend, structure, momentum, MACD, RSI, volume, relative strength) with a market-regime filter
- Analysts: rules (offline), Claude (Anthropic API), Claude Code (subscription), Ollama (local)
- Risk engine: ATR sizing, bracket stops at the broker, trailing stops, daily loss halt, drawdown kill switch, PDT guard, no margin
- Alpaca broker + market data + news; local paper simulator
- Backtester that replays history through the live engine
- Live HUD dashboard (zero dependencies, strict CSP, token auth)
- Telegram / Discord / Slack alerts
- Docker image (non-root, read-only)
