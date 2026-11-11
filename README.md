# Signal Desk

Long/short signals for US stocks and crypto, with the backtest that produced them attached.

```
pip install -r requirements.txt
python -m uvicorn --app-dir . app.main:app --port 8000
```

Open http://127.0.0.1:8000. On Windows, `.\run.ps1` does both steps.

The dashboard follows your OS colour scheme on first load. The ☀ / ☾ button switches
it and remembers the choice.

On each visit the app diffs today's signals against the snapshot from your last visit and
shows a banner listing whatever flipped (e.g. `BTC-USD SHORT → LONG`), clickable straight
through to the chart. Dismiss it to acknowledge; the baseline then moves to now.

**Trade alerts** (the second tab) lists every signal change in the window you pick — 7, 30 or
90 days — newest first: what changed, when, the price then and now, the move since, and where
the symbol stands today. It is rebuilt from each symbol's position series, so the page has
real history instead of starting from your first visit. The badge on the tab is the count.

Each entry or flip has an **I took it** button. Pressing it writes the trade to **My trades** with
its entry price, a 2×ATR stop and a 3×ATR target recomputed at that price, plus a note recording
which alert it came from. The same alert cannot be logged twice — the journal is idempotent on
that source. My trades marks each position against the latest close (P&L, R multiple, days held)
and lets you close or delete it. Trades live in `data/trades.db`, separate from the bar cache so
clearing cached data never touches your book.

**Stats** (the fourth tab) pools every trade the engine closed on the history it has: win rate,
average win and loss, payoff, expectancy, profit factor and a date-clustered t-stat, split by
market and by side, with a per-symbol table sorted by expectancy. The pooled numbers are the
honest ones — averaging twenty noisy per-symbol results hides where the edge actually comes from.

No API keys. Price data comes from Yahoo Finance (stocks, and crypto as a fallback) and
Binance spot (crypto), cached in `data/cache.db` so a reload does not re-hit the network.

## What a signal is

Four independent strategy families vote `-1 / 0 / +1` on every daily bar:

| Family | Long vote | Short vote |
| --- | --- | --- |
| Trend | close > EMA200, EMA20 > EMA50, ADX ≥ 20 | mirror |
| Momentum | 63-day change > 0 and close > EMA50 | mirror |
| Breakout | close above the prior 55-bar high | close below the prior 55-bar low |
| Mean reversion | RSI ≤ 30 while above EMA200 | RSI ≥ 70 while below EMA200 |

Votes are weighted (breakout 1.5, the rest 1.0) and summed. A position opens at
`|score| ≥ 2`, closes when `|score| < 0.5`, and flips directly on a decisive opposite score.
Two regime gates block fresh entries: ATR% in the top decile of its trailing year, or ADX < 15.

Exits, chosen by `EXIT_MODE` in `app/config.py`:

| Mode | Rule | Target |
| --- | --- | --- |
| `partial` (default) | 2×ATR stop; half off at the target; remainder trails 3×ATR behind the best price | 3×ATR = 1.5R |
| `fixed` | 2×ATR stop, hard target, whole position out | 3×ATR = 1.5R |
| `trailing` | 2×ATR stop, pure 3×ATR chandelier trail | none |

Under `partial`, a round trip exits in two pieces but is reported as **one trade**: size-weighted
average exit, R summed across legs, and `target_hit` set when the first piece filled at the target.
That keeps win rate and expectancy comparable with the other two modes.

Once the target fills, the row reports the price it filled at (a ✓ in the table) and the remainder
is tracked by the trail — so `trail 3×ATR` in the Target column means the objective is already
banked, not that the row is missing a level.

Signals are computed on the close and are actionable at the next open. The backtest enforces
that lag, charges 2 bps per side on stocks and 15 bps on crypto, fills gaps at the open, and
checks stops against each bar's high/low.

## What the numbers are worth

Across the default universe (20 symbols, ten years of daily bars), with the `partial` exit:

| Metric | Value |
| --- | --- |
| Trades | 1,766 |
| Win rate | 42.1% |
| Expectancy | +0.16R per trade |
| Profit factor | 1.42 |
| t-stat, date-clustered | **4.09** |
| Crypto / stocks | +0.21R (t 3.04) / +0.12R (t 2.98) |
| Long / short | **+0.25R (t 5.22)** / −0.03R (t −1.03) |

The window matters more than anything else in this file. On three years the same rules scored
+0.12R with a t of 0.34 and the stock half looked worthless (+0.005R); over ten years the stock
half is +0.121R with a t of 2.98. The short sample was the misleading one, and that cut both ways.

Two things the breakdown says plainly:

- **The short side does not work** (−0.03R over 556 trades, t −1.03). Every bit of the edge is on
  the long side, which matches the known asymmetry between momentum and shorting.
- Both asset classes clear the bar once given enough history, so "crypto only" was a
  three-year artefact rather than a structural finding.

Treat the backtest as a filter for broken logic, not as a forecast. It is still in-sample over
one dataset; there is no parameter search, which removes one way to fool yourself
but does not create an edge.

## Layout

```
app/config.py       universe, parameters, cost model
app/indicators.py   EMA, RSI, ATR, ADX (Wilder), Donchian, Hurst
app/data.py         Yahoo + Binance fetch, SQLite cache
app/signals.py      votes, score, state machine, levels
app/backtest.py     bar-by-bar simulation, trade ledger, statistics
app/service.py      orchestration and caching
app/main.py         FastAPI routes
app/static/         dashboard (no framework, no CDN)
scripts/scan.py     terminal table; --symbol for one name
scripts/compare_exits.py  fixed vs trailing vs partial exit across the universe
tests/              indicator maths
```

## API

| Route | Purpose |
| --- | --- |
| `GET /api/signals?asset_class=all\|stock\|crypto` | table rows + counts |
| `GET /api/asset/{symbol}?bars=220` | candles, EMAs, entry/exit marks, full backtest |
| `GET /api/backtest/{symbol}` | statistics only |
| `GET /api/alerts?asset_class=all\|stock\|crypto&days=30` | every signal change in the window, newest first |
| `GET /api/trades` | the journal, marked against the latest close |
| `POST /api/trades` | log a trade `{symbol, side, entry?, stop?, target?, note?, source?}` |
| `POST /api/trades/{id}/close` | close at `{price?}`, defaulting to the latest close |
| `DELETE /api/trades/{id}` | remove a trade |
| `GET /api/universe`, `GET /api/health` | universe and liveness |

## Tests

```
python -m pytest tests -q
```

Covers EMA seeding, RSI extremes, Wilder ATR decay, ADX directional split on clean trends, and
the Donchian window boundary. One regression test pins the ADX bug from the reference project
this started from: it masked `+DM` before testing `-DM` against it, which inflates `-DI`.

## Deploying

`docs/DEPLOY.md` has the full path to GitHub and Vercel. The short version: the journal is the
only stateful part, so it runs on SQLite locally and on Postgres in production (set
`DATABASE_URL`, verified with `python scripts/check_postgres.py`). `vercel.json` and
`api/index.py` are ready; on Vercel the bar cache falls back to `/tmp` and background warming is
skipped, which is why the first request after idle takes a few extra seconds.

## Limits

Daily bars only, long/short with one unit of equity, no leverage, no borrow cost, no funding on
shorts, no slippage model beyond fixed basis points, and no intraday fills. Crypto pairs are
USD-quoted spot; equity data is split-adjusted but not dividend-adjusted. Nothing here routes
orders. Research tooling, not investment advice.
