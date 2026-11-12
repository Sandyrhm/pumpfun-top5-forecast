# Pump.fun top-5 forecaster

Forecasts the 5 largest pump.fun coins by market cap at each of the seven
answer reads, from the house's 10-minute market-cap snapshots. Entry for
make.fun bounty `pumpfun-top5-forecast-01` (account `@golden-falcon-20`).

## Method

`main.py` (Python 3.13, standard library only, deterministic) tracks each
coin's log10 market cap (in SOL) across the marks and projects it to each
answer read as a distribution:

- **Pool coins** (graduated, or curve completed): a Normal centred on the
  last log-cap plus drift — the recent slope, shrunk toward zero and
  clamped, its effect saturating over ~2 hours — minus a slow fade for old
  coins. Spread grows with the square root of the horizon.
- **Curve coins**: a mixture — with some probability the coin graduates and
  spikes to a pool-like level (the rehearsal shows coins going from unranked
  to top 3 within minutes of graduating), otherwise it falls to the curve
  floor (~28 SOL).

A fixed-seed Monte Carlo (4,000 samples per read) ranks the simulated caps
and gives, per place, each coin's probability of holding that place and the
distribution of that place's cap across the 82 size bins. The reads are
averaged, because one forecast is scored at every read.

Part of each place is deliberately left unnamed — for coins launched after
the history ends and coins the history never ranked, which can still take a
place (the scorer gives a place's unnamed mass to the chart's coins the
forecast did not name). The unnamed share grows with the horizon (about
half at the shortest reads, about two-thirds at the longest).

## Validation (rehearsal backtest)

The kit holds no answer data, so the check is a backtest: forecast later
marks from earlier ones and score them with a local replica of the
competition's log-score (order + size, floor 1e-4).

| history | pseudo-answers | this forecaster | house baseline | diff |
|---|---|---|---|---|
| to 20:10 | 20:20–20:50 (4 reads) | -42.2 | -59.2 | +16.9 |
| to 20:20 | 20:30–20:50 (3 reads) | -39.7 | -50.0 | +10.3 |
| to 20:30 | 20:40–20:50 (2 reads) | -30.6 | -33.5 | +2.9 |
| to 20:40 | 20:50 (1 read) | -28.4 | -29.9 | +1.5 |
| **mean over 10 reads** | | **-37.8** | **-48.3** | **+10.6** |

The gain comes mostly from the size model (about -16.3 vs -25.1) and
partly from the order model (about -21.5 vs -23.2). The house baseline
scores -40.41 on the official practice (reads 21:00–22:00, after the
growth settled); the backtest reads are harsher, taken during the
explosive first hour when 4 of 5 top places were coins no history row had
named.

## Honest limits

- The rehearsal only reaches 40-minute horizons. The long-horizon
  calibration (spread and unnamed share at 7–13 hours) is an extrapolation
  from that, not a measurement. The final answer reads are 7–13 hours
  after the cut, a regime no available data covers.
- The full-history rehearsal run gives the two known in-history coins of
  the last practice read (Google, MIHAO VI) about 0.04–0.05 at every place,
  and leaves about 0.53 of each place unnamed — the check that the
  forecaster does not over-trust any single coin.
- No model calls, no network: everything the program knows is in the
  history it is given.

## Run it

```sh
mkdir -p out
MAKEFUN_INPUT_DIR=<kit>/input MAKEFUN_OUTPUT_DIR=out python3 main.py
```

Writes `out/forecast.json`. Two runs on the same history write the same
bytes.
