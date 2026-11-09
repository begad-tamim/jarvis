# Security Policy

JARVIS can place real orders with real money, so security is a feature, not an afterthought.

## Reporting a vulnerability

Please **do not open a public issue**. Use GitHub's private
[security advisory form](https://github.com/begad-tamim/jarvis-trading-agent/security/advisories/new).
You'll get a reply within 72 hours.

## What JARVIS does to keep you safe

| Threat | Defense |
|---|---|
| Leaked API keys | Secrets only from environment / `.env` (gitignored, permission-checked). Never in config, logs, errors or the dashboard. Adapters override `__repr__` so keys can't end up in tracebacks. |
| Accidental real-money trading | Paper by default. Live needs `mode = "live"` **and** `JARVIS_LIVE_TRADING=I_UNDERSTAND_THE_RISKS`. The broker URL is chosen in code, never from input. |
| Hallucinating / prompt-injected LLM | Strict JSON schema, unknown symbols dropped, model never sets quantity, price or stop. News is fenced as untrusted data. Conviction can only shrink a position. |
| Runaway losses | Per-trade risk sizing, position caps, no margin, daily loss halt, drawdown kill switch, PDT guard, cooldowns. Exits always allowed, even when paused. |
| Process dies mid-trade | Bracket orders put the stop **at the broker**. |
| Someone else drives your dashboard | Binds to 127.0.0.1. Every API call needs a 256-bit bearer token in a header (no cookies, so no CSRF). Host allow-list blocks DNS rebinding. Strict CSP with no inline scripts or styles, no third-party requests. |
| Duplicate orders on retries | Order POSTs are never auto-retried, and every order carries a unique `client_order_id`. |
| Tampering with history | The journal is SQLite with `0600` permissions and append-only events. |
| Supply chain | Zero required dependencies. CI runs CodeQL, and Dependabot watches the workflow actions. |
| Container escape / privilege | Docker image runs as non-root, read-only filesystem, all capabilities dropped, port published on localhost only. |

## Your part

- `chmod 600 .env`, and never commit it.
- Use **paper keys** until you've watched JARVIS for weeks.
- Give your broker API key the minimum permissions available, and turn on 2FA at your broker.
- Don't expose the dashboard to the internet. If you need remote access, use a VPN such as Tailscale or WireGuard.
