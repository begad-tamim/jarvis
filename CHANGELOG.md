# Changelog

## 2.0.1 - 2026-11-09

A console that feels alive.

- **Brief me** answers in one line plus a live briefing card (needs you / in progress / ready)
- Answers type in word by word; the console only follows when you are at the bottom, so long answers never jump
- "Thinking" row shows the brain, the project and a live timer
- One-time boot sequence, count-up stats, project dividers in the conversation, speaking indicator for voice
- Shorter, sharper brain answers (about 150 words, no tool chatter)
- Home P&L tile follows the live markets feed
- New demo GIF and screenshots, recorded frame by frame

## 2.0.0 - 2026-11-09

JARVIS becomes your personal AI system. Trading is now one module.

- **Projects**: discovers your git repos, shows live status (dirty, ahead, behind, stale, TODOs) and a free daily briefing
- **Any brain**: Claude Code, Codex, Gemini CLI, Ollama, Claude API, any command (`brain = "cmd"`) or offline. Runs inside the active project, read-only by default
- **Assistant**: one conversation shared by the HUD, phone, Telegram and calls. Instant local intents (brief, open, status, remember, tasks, markets, call me)
- **Voice**: talk to JARVIS in the browser and hear it answer
- **Phone**: installable PWA, Telegram bot (allow-listed), Twilio calls both ways with signed webhooks
- **New HUD**: Home briefing, project workspace with notes and tasks, Ask console, Markets
- `jarvis demo` now includes a sample workspace of 7 real git repos
- New commands: `jarvis` / `up`, `ask`, `brief`, `projects`, `brains`, `call`. `run` is now `trade` (alias kept)
- 67 tests


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
