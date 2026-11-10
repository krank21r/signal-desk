# Do liquidation traps work as a signal?

**Verdict: no — not as a vote in the engine.** The pattern is real in the tape but it
does not survive controls, and the part that looks tradable is one regime, not an edge.
The detector is kept in the repo because the forward log is worth running; it is not wired
into scoring.

## What a trap is, mechanically

A forced unwind leaves a footprint: price is pushed through a level where stops and leverage
sit, cannot hold there, and closes back inside the prior range. Longs are liquidated below
support (bullish trap, squeeze up); shorts above resistance (bearish trap, squeeze down).

The mechanic is well documented on the crypto side, with two clean cases: October 2025 saw
about $19bn of forced closures, roughly $16.7bn of them longs, while perpetual open interest
collapsed 43% from $217bn to $123bn; August 2026 ran the same machine in reverse with $1.74bn
of short liquidations in 24 hours and ETH +17.8% ([Bitsgap, liquidation cascades](https://bitsgap.com/blog/liquidation-cascades-explained)).

Equities have no public liquidation tape. The equivalent footprint is a sweep of a prior
extreme with heavy volume — a proxy, and treated as such below.

## Definition tested

Detector in `app/traps.py`, all thresholds fixed before measurement:

- price trades beyond the prior `N`-bar extreme (`N` = 20 or 55),
- penetration between **0.15 and 1.2 ATR** — a poke, not a genuine breakdown,
- close back inside the range, in the upper half (bullish) or lower half (bearish),
- volume ≥ **1.2×** its 20-day average,
- **5-bar cooldown** so a cluster counts once.

Entry is the **next open**, exit the close `h` bars later — the same no-look-ahead
convention as the main engine. Results are **excess** return: the trade's return minus the
drift of simply holding that direction for the same horizon, which isolates the trap from
"the market went up".

Baseline drift over 5/10/20 bars: stocks +0.67% / +1.15% / +2.54%, crypto +0.34% / +0.67% / +1.39%.

## Results

### Crypto — a 5-day bounce that dies

| level | h | n | hit | excess | t |
| --- | --- | --- | --- | --- | --- |
| 20-bar | 5 | 189 | 59.8% | **+1.76%** | **+2.21** |
| 20-bar | 10 | 189 | 47.1% | −2.11% | −1.82 |
| 20-bar | 20 | 189 | 51.9% | −3.12% | −1.51 |
| 55-bar | 5 | 116 | 62.1% | **+2.50%** | **+2.43** |
| 55-bar | 10 | 116 | 51.7% | −1.17% | −0.76 |
| 55-bar | 20 | 116 | 52.6% | −2.58% | −0.90 |

So: the squeeze pays for about a week, then reverses — consistent with the mechanic, since
forced flow is transient.

### Stocks — the opposite of the hypothesis

| level | h | n | hit | excess | t |
| --- | --- | --- | --- | --- | --- |
| 20-bar | 5 | 124 | 37.9% | **−1.34%** | **−2.87** |
| 20-bar | 20 | 124 | 42.7% | −0.12% | −0.15 |
| 55-bar | 5 | 78 | 39.7% | **−1.37%** | **−2.09** |

A sweep-and-reclaim in a stock is followed by *weakness*, not a bounce. Adding the 200-day
trend filter makes it worse, not better (−1.63% at 5 days, t = −2.22).

## The two controls that decided it

**Placebo** — heavy-volume reversal bars that closed off the extreme but never swept it:

| class | sample | 5-day excess | t |
| --- | --- | --- | --- |
| crypto | trap | +1.76% | +2.21 |
| crypto | placebo | −0.79% | −1.21 |
| stock | trap | −1.34% | −2.87 |
| stock | placebo | −0.27% | −0.80 |

The trap does beat the placebo at 5 days in crypto (+2.55pp spread) and undershoots it in
stocks — so the pattern is specific, not just "volatility". That is what made the year split
worth running.

**Regime** — crypto trap, 5-day excess by year:

| level | 2024 | 2025 | 2026 |
| --- | --- | --- | --- |
| 20-bar | +1.68% (t 0.94, n 48) | **+3.81% (t 2.76, n 77)** | −0.65% (t −0.69, n 64) |
| 55-bar | +2.47% (t 0.91, n 29) | **+5.41% (t 3.92, n 50)** | −1.42% (t −1.04, n 37) |

The entire effect is 2025. In the most recent year it is flat to negative. A signal that
worked in one regime and stopped is a regime bet, not an edge.

Twenty-four cells were tested across level, trend filter and horizon; at that width one or
two will look significant by chance, which is another reason not to promote the crypto cell.

## Data limits found

- **Open interest: 31 daily rows per symbol** (Binance `/futures/data/openInterestHist` is
  capped at roughly the last 30 days, regardless of `limit`). A multi-year OI-flush test is
  therefore impossible on public data; only 6 crypto events could be joined to OI.
- **Funding: 500 rows of 8h ≈ 166 days**, extendable by paging `startTime`.

Consequence: the *direct* evidence of forced closure — open interest falling on the sweep
bar — cannot be backtested here. It can only be logged forward, or bought from a vendor.

## What would change the verdict

1. Log trap events daily with OI change and funding, then measure after 60–90 sessions.
2. Split by regime up front, and require the effect in the most recent year, not the pooled
   sample.
3. If a vendor with multi-year OI is available, rerun the OI-flush split — that is the one
   test with a real mechanism behind it and no data to run on today.

## Reproduce

```
python scripts/research_traps.py            # the matrix
python scripts/research_trap_controls.py    # placebo and year split
python -m pytest tests/test_traps.py -q     # detector behaviour
```

## Falsification test — the final word

The stock result was put through four criteria fixed in advance, with date
clustering so symbols moving together on one day count once.

**Bullish sweeps, faded (short 5 bars).** 54 events over 39 days.

| measure | mean | t |
| --- | --- | --- |
| pooled | +1.04% | 1.51 |
| date-clustered | +0.60% | 0.75 |
| first half | +0.52% | 0.35 |
| second half | +0.66% | 1.05 |
| placebo, same direction | −0.68% | −1.58 |

It beats the placebo (+1.72pp, Welch t 2.12) and the median agrees with the mean
(+1.21%), so the pattern is neither noise nor one outlier — but the pooled t of
1.51 was inflated by same-day correlation, and **neither half reaches t = 2**.
FAILS criterion 1.

**Bearish sweeps, faded (long 5 bars).** 70 events over 61 days. Clustered
+1.58% (t 2.23), which looked like a pass — until the placebo: generic
heavy-volume weak closes return **+1.20% (t 2.28)**. The sweep adds 0.45pp with
a Welch t of 0.55, i.e. nothing. All of the apparent edge sits in the second
half (first half t 0.54). FAILS criteria 1 and 3.

That second failure is the point of running the control: the bearish side is a
generic bounce wearing a sweep costume.

**Both sides closed. No trap-based signal goes into scoring.** The detector and
its scripts stay as logging utilities, and that is all they are.
