# Contributing

Thanks for helping make JARVIS better!

```bash
git clone https://github.com/begad-tamim/jarvis && cd jarvis
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev,ai]"
pytest -q && ruff check jarvis tests
jarvis demo
```

## Ground rules

1. **Zero required dependencies.** The core must run on a stock Python 3.11+. Optional extras are fine.
2. **The LLM proposes, code decides.** Never let model output set a size, a price or skip a stop.
3. **Risk changes need tests.** Anything touching `risk.py` or order placement needs a test proving the guardrail still holds.
4. **Same engine everywhere.** Live, demo and backtest all run through `Engine`, so there's no separate backtest logic to drift.

## Good first issues

- A new brain in `jarvis/brains.py` (any agent CLI or local model). Add a fake-CLI test like the ones in `tests/test_brains.py`.
- A new channel in `jarvis/channels/` (WhatsApp, Discord, Slack).

- A new broker adapter (Interactive Brokers, Tradier, Binance testnet…). Implement the `Broker` protocol in `jarvis/brokers/__init__.py`.
- New quant factors in `strategy.py`, with a test.
- Dashboard improvements in `jarvis/web/`. No frameworks, and keep the strict CSP.
