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

## v2 changes

The v1 entry scored -37.66 on the official practice forecast
(order -19.77, sizes -17.88), well behind the leaders (about -31.8 to
-32.4, sizes about -14.1 to -14.2): the gap was mostly the size score.
v2 changes, all fitted to the rehearsal history, not to the practice
answer (which the kit does not contain):

- **p=0 naming**: coins on the cut's chart (ranks 11–100) that never reach
  the top 5 in any Monte Carlo draw and whose peak log-cap is below 1.6 —
  well under the ~1.93 graduation level, so they cannot graduate and
  spike — are named at p=0 at every place. The scorer divides a place's
  unnamed mass among the chart coins not named there, so this concentrates
  it on the coins that might actually take the place (newcomers included).
  The peak cap is deliberately conservative: a p=0 coin that took a place
  would score the floor (-9.21) there.
- **Tighter pool-coin spread**: sd 0.12 + 0.20·√h instead of 0.18 +
  0.28·√h, fitted to the rehearsal's pool-coin 10-minute moves (sd ~0.135).
- **Graduation spike re-centred** to N(3.20, 0.30): the rehearsal's fresh
  graduates landed at 2.92–3.47 in log-cap.
- **Upward slope clamp raised** to +0.30/h with shrink 0.7 (rehearsal
  leaders rise +0.31 to +0.55/h; v1's +0.15/h capped the drift far below
  the data). Still shrunk and saturated over 2 hours, so the total drift
  stays bounded.

## Validation (rehearsal backtest)

The kit holds no answer data, so the check is a backtest: forecast later
marks from earlier ones and score them with a local replica of the
competition's log-score (order + size, floor 1e-4). The replica was verified
against v1's reported numbers (v1: -37.8, baseline: -48.3).

| history | pseudo-answers | v2 | v1 | house baseline |
|---|---|---|---|---|
| to 20:10 | 20:20–20:50 (4 reads) | -42.1 | -42.2 | -59.2 |
| to 20:20 | 20:30–20:50 (3 reads) | -38.3 | -39.7 | -50.0 |
| to 20:30 | 20:40–20:50 (2 reads) | -29.0 | -30.6 | -33.5 |
| to 20:40 | 20:50 (1 read) | -26.7 | -28.4 | -29.9 |
| **mean over 10 reads** | | **-36.8** | **-37.8** | **-48.3** |

v2 beats v1 on both components (order -21.0 vs -21.5, sizes -15.8 vs
-16.3). The gains concentrate at the later cuts — the mature-history
regime the final resembles — while the earliest, most chaotic cut is
nearly unchanged (-42.1 vs -42.2). The house baseline scores -40.41 on
the official practice (reads 21:00–22:00, after the growth settled); the
backtest reads are harsher, taken during the explosive first hour when 4
of 5 top places were coins no history row had named.

## Honest limits

- The rehearsal only reaches 40-minute horizons. The long-horizon
  calibration (spread and unnamed share at 7–13 hours) is an extrapolation
  from that, not a measurement. The final answer reads are 7–13 hours
  after the cut, a regime no available data covers.
- The full-history rehearsal run gives the two known in-history coins of
  the last practice read (Google, MIHAO VI) about 0.03–0.05 at every place,
  and leaves about 0.53 of each place unnamed — the check that the
  forecaster does not over-trust any single coin.
- v2's rehearsal gain (+1.0) is far smaller than the practice gap to the
  leaders (-37.66 vs about -31.8, a ~5.8 gap, mostly sizes). The rehearsal
  cannot measure the improvement that matters most for the final: at its
  early cuts 4 of 5 top places are coins no history row had named, so no
  tuning of known coins can fix those reads. v2's edge over v1 is real on
  both components, but a top-10 finish on the final is not implied by it.
- No model calls, no network: everything the program knows is in the
  history it is given.

## Run it

```sh
mkdir -p out
MAKEFUN_INPUT_DIR=<kit>/input MAKEFUN_OUTPUT_DIR=out python3 main.py
```

Writes `out/forecast.json`. Two runs on the same history write the same
bytes.
