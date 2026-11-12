"""Forecast the top 5 pump.fun coins by market cap, 24 hours out.

Reads the house's 10-minute market-cap snapshots from $MAKEFUN_INPUT_DIR
(default /input) and writes one forecast to $MAKEFUN_OUTPUT_DIR/forecast.json
(default /output/forecast.json). Standard library only, no network, no clock,
no randomness beyond a fixed seed: two runs on the same history write the
same bytes.

Method
------
Each coin's log10 market cap (in SOL) is tracked across the marks. At each
answer read the program projects every coin's log-cap as a distribution:

- A coin already on its pool (or whose curve completed) is projected as a
  Normal centred on its last log-cap plus a drift: its recent slope, shrunk
  toward zero and clamped, with the slope's effect saturating over ~2 hours
  (a pump cannot compound forever), minus a slow fade for old coins. The
  spread grows with the square root of the horizon, so a read 13 hours out
  is honestly far more uncertain than one 1 hour out.
- A coin still on its bonding curve is a mixture: with some probability it
  graduates and spikes to a pool-like level (the rehearsal shows coins going
  from unranked to top 3 within minutes of graduating), otherwise it falls
  to the curve floor (~28 SOL), where dead curve coins sit.

A fixed-seed Monte Carlo (4,000 samples per read) ranks the simulated caps,
which gives, per place, the probability each coin holds that place, and the
distribution of each place's cap across the 82 size bins. The seven reads'
distributions are averaged, because the one forecast is scored at every read.

Part of each place is deliberately left unnamed: coins launched after the
history ends, and coins the history never ranked, can still take a place,
and the scorer gives a place's unnamed mass to the chart's coins the
forecast did not name there. The unnamed share grows with the horizon
(about half at the shortest reads, about two-thirds at the longest), which
the 2-hour rehearsal supports: at its last read 3 of the top 5 were coins
no history row had ever named.

Calibration was checked on the rehearsal by backtesting: forecasting later
marks from earlier ones, the forecaster beat the house persistence baseline
on every split (mean log score about -37.7 vs -48.3 over ten pseudo-reads).
The rehearsal only reaches 40-minute horizons, so the long-horizon
calibration (spread and unnamed share at 7-13 hours) is an extrapolation
from that, stated honestly in the README.

v2 changes (rehearsal backtest: -36.79 vs v1's -37.76, order -20.97 vs
-21.48, sizes -15.82 vs -16.28, same ten pseudo-reads):
- p=0 naming: coins on the cut's chart (ranks 11-100) that never reach the
  top 5 in any Monte Carlo draw and whose peak is below 1.6 (well under the
  ~1.93 graduation level) are named at p=0 at every place, so the scorer's
  unnamed share is divided among fewer chart coins. Worth +0.5 on the
  rehearsal's order score; the peak cap is deliberately conservative.
- Tighter predictive spread for pool coins (sd 0.12 + 0.20*sqrt(h) instead
  of 0.18 + 0.28*sqrt(h)), fitted to the rehearsal's pool-coin 10-minute
  moves (sd about 0.135).
- Graduation spike re-centred to N(3.20, 0.30): the rehearsal's fresh
  graduates landed at 2.92-3.47 in log-cap.
- Upward slope clamp raised to +0.30/h with shrink 0.7 (rehearsal leaders
  rise +0.31 to +0.55/h; v1's +0.15/h capped the drift far below the data).
  Still shrunk and saturated over 2 hours, so the total drift stays bounded.
"""

import json
import math
import os
import random
from datetime import datetime

# --- tuned constants (see README for where each came from) -----------------
N_SAMPLES = 4000
SEED = 12345
FLOOR_LOGMC = 1.447  # bonding-curve floor in the rehearsal (~28 SOL)

# Pool-coin drift.
SLOPE_LO = 0.30      # clamp on recent slope, per hour in log10, downward
SLOPE_HI = 0.30      # clamp on recent slope, per hour in log10, upward (v2:
                     # rehearsal leaders rise +0.31 to +0.55/h, so v1's +0.15
                     # capped the drift far below the data; still shrunk and
                     # saturated, so the total drift stays bounded)
SLOPE_SHRINK = 0.7   # shrink the measured slope toward zero
DRIFT_TAU_H = 2.0    # slope effect saturates over this many hours
FADE_PER_H = 0.05    # slow expected fade per hour, only for old coins
OLD_AGE_H = 3.0      # a coin older than this at the cut is "old"
SIG0 = 0.12          # predictive sd at the cut (v2: fitted to the rehearsal's
SIG_RATE = 0.20      # pool-coin 10-minute moves, sd ~0.135; v1's 0.18+0.28
                     # spread the place distributions wider than the data)

# Curve-coin graduation mixture.
Q_GRAD_BASE = 0.25   # base probability a live curve coin graduates and spikes
Q_GRAD_AGE0 = 0.5    # age (hours) after which the chance starts decaying
Q_GRAD_DECAY = 1.0   # decay time constant, hours
Q_GRAD_MAX = 0.25    # cap on the graduation chance
Q_GRAD_PEAK_MIN = 2.0  # only coins that reached this log-cap can spike
SPIKE_MU = 3.20      # log-cap of a fresh pool spike (v2: the rehearsal's
SPIKE_SD = 0.30      # fresh graduates landed at 2.92-3.47, mean ~3.2)
FLOOR_MU = 1.47      # log-cap of a dead curve coin
FLOOR_SD = 0.08

# p=0 naming (v2): name coins that certainly cannot take a top-5 place at
# p=0, so the scorer's unnamed share is divided among fewer chart coins.
P0_RANK_LO = 11     # ranks on the cut's chart (top 100) eligible for p=0
P0_RANK_HI = 100
P0_PEAK_MAX = 1.6   # peak log-cap: well under the ~1.93 graduation level, so
                    # the coin cannot graduate and spike; deliberately
                    # conservative, because a p=0 coin that took a place
                    # would score the floor (-9.21) at that place

# Unnamed mass for coins the history never ranked (newcomers): grows with
# horizon h as A + B * (1 - exp(-h / TAU)), saturating near A + B.
P_OTHER_A = 0.50
P_OTHER_B = 0.15
P_OTHER_TAU = 2.0

MAX_ROWS_PER_PLACE = 100
MAX_CANDIDATES = 400


def parse_time(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def load_input(input_dir):
    with open(os.path.join(input_dir, "task.json"), encoding="utf-8") as f:
        task = json.load(f)
    marks = []
    with open(os.path.join(input_dir, "history.ndjson"), encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                marks.append(json.loads(line))
    return task, marks


def build_coins(task, marks):
    """Per-mint trajectory state from the marks."""
    t0 = parse_time(task["t0"])
    coins = {}
    for mark in marks:
        if not mark.get("ok") or not isinstance(mark.get("rows"), list):
            continue
        t_h = (parse_time(mark["at"]) - t0).total_seconds() / 3600.0
        for row in mark["rows"]:
            coin = coins.get(row["mint"])
            if coin is None:
                coin = coins[row["mint"]] = {
                    "mint": row["mint"],
                    "created_h": (parse_time(row["created_at"]) - t0).total_seconds() / 3600.0,
                    "mayhem": bool(row.get("mayhem")),
                    "obs": [],
                }
            coin["obs"].append((
                t_h,
                math.log10(int(row["market_cap_lamports"])) - 9.0,
                row["venue"],
                bool(row.get("complete")),
            ))
    for coin in coins.values():
        obs = coin["obs"]
        coin["last_t"], coin["last_logmc"], coin["last_venue"], coin["last_complete"] = obs[-1]
        coin["n_obs"] = len(obs)
        coin["peak"] = max(o[1] for o in obs)
        coin["age_h"] = coin["last_t"] - coin["created_h"]
        seg = obs[-6:]
        if len(seg) >= 2 and seg[-1][0] - seg[0][0] >= 1.0 / 6.0:
            n = len(seg)
            mt = sum(o[0] for o in seg) / n
            my = sum(o[1] for o in seg) / n
            den = sum((o[0] - mt) ** 2 for o in seg)
            coin["slope"] = sum((o[0] - mt) * (o[1] - my) for o in seg) / den if den > 0 else 0.0
        else:
            coin["slope"] = 0.0
    return coins


def project(coin, h):
    """Predictive distribution of a coin's log-cap h hours after the cut.

    Returns (kind, mu, sigma, q_grad): kind 'pool' means Normal(mu, sigma);
    kind 'curve' means a mixture (q_grad of Normal(SPIKE_MU, SPIKE_SD),
    otherwise Normal(FLOOR_MU, FLOOR_SD)).
    """
    if coin["last_venue"] in ("pool", "migrating") or coin["last_complete"]:
        slope = max(-SLOPE_LO, min(SLOPE_HI, coin["slope"])) * SLOPE_SHRINK
        drift = slope * DRIFT_TAU_H * (1.0 - math.exp(-h / DRIFT_TAU_H))
        fade = FADE_PER_H * max(0.0, h - 1.0) if coin["age_h"] > OLD_AGE_H else 0.0
        mu = coin["last_logmc"] + drift - fade
        return ("pool", mu, SIG0 + SIG_RATE * math.sqrt(h), 0.0)
    q = Q_GRAD_BASE * math.exp(-max(0.0, coin["age_h"] - Q_GRAD_AGE0) / Q_GRAD_DECAY)
    q = max(0.0, min(Q_GRAD_MAX, q))
    if coin["peak"] < Q_GRAD_PEAK_MIN:
        q = 0.0
    return ("curve", coin["last_logmc"], SIG0 + SIG_RATE * math.sqrt(h), q)


def norm_cdf(x, mean, sd):
    if x == -float("inf"):
        return 0.0
    if x == float("inf"):
        return 1.0
    return 0.5 * (1.0 + math.erf((x - mean) / (sd * math.sqrt(2.0))))


def bin_of(logmc, edges):
    if logmc < edges[0]:
        return 0
    for i in range(1, 81):
        if logmc < edges[i]:
            return i
    return 81


def unnamed_share(h):
    return P_OTHER_A + P_OTHER_B * (1.0 - math.exp(-h / P_OTHER_TAU))


def forecast(task, marks):
    t0 = parse_time(task["t0"])
    cut = parse_time(task["history_until"])
    edges = task["size"]["edges"]
    places = task["places"]
    horizons = [max(0.0, (parse_time(r) - cut).total_seconds() / 3600.0)
                for r in task["reads"]]
    coins = build_coins(task, marks)
    coin_list = list(coins.values())
    rng = random.Random(SEED)

    place_counts = {k: {} for k in range(1, places + 1)}
    size_counts = {k: {} for k in range(1, places + 1)}
    exp_rank = {}
    top5_count = {}
    n_reads = len(horizons) or 1

    for h in horizons:
        projected = []
        for coin in coin_list:
            kind, mu, sigma, q = project(coin, h)
            projected.append((coin, kind, mu, sigma, q))
        # Shortlist: coins whose plausible range reaches the 5th-highest
        # projected centre. This keeps the Monte Carlo fast on a long final
        # history without changing the result: a coin further below cannot
        # win a place in any realistic draw.
        pool_mus = sorted((mu for _, kind, mu, _, _ in projected if kind == "pool"),
                          reverse=True)
        thresh = pool_mus[min(4, len(pool_mus) - 1)] if pool_mus else FLOOR_LOGMC
        candidates = []
        for coin, kind, mu, sigma, q in projected:
            if kind == "pool":
                if mu + 2.5 * sigma >= thresh - 0.3:
                    candidates.append((coin, kind, mu, sigma, q))
            elif q > 0.02 and SPIKE_MU + 2.0 * SPIKE_SD >= thresh - 0.3:
                candidates.append((coin, kind, mu, sigma, q))
        candidates.sort(key=lambda c: (-(c[2] + 2.5 * c[3]), c[0]["mint"]))
        candidates = candidates[:MAX_CANDIDATES]

        for _ in range(N_SAMPLES):
            drawn = []
            for coin, kind, mu, sigma, q in candidates:
                if kind == "pool":
                    value = rng.gauss(mu, sigma)
                elif rng.random() < q:
                    value = rng.gauss(SPIKE_MU, SPIKE_SD)
                else:
                    value = rng.gauss(FLOOR_MU, FLOOR_SD)
                drawn.append((value, coin["mint"]))
            drawn.sort(key=lambda item: (-item[0], item[1]))
            for k in range(1, places + 1):
                if k <= len(drawn):
                    value, mint = drawn[k - 1]
                    place_counts[k][mint] = place_counts[k].get(mint, 0) + 1
                    b = bin_of(value, edges)
                    size_counts[k][b] = size_counts[k].get(b, 0) + 1
            for rank, (_, mint) in enumerate(drawn, start=1):
                exp_rank[mint] = exp_rank.get(mint, 0.0) + rank
                if rank <= places:
                    top5_count[mint] = top5_count.get(mint, 0) + 1

    total_draws = N_SAMPLES * n_reads
    p_other = sum(unnamed_share(h) for h in horizons) / n_reads
    p_other = max(0.0, min(0.85, p_other))

    probabilities = {}
    for k in range(1, places + 1):
        rows = []
        for mint, count in place_counts[k].items():
            p = (count / total_draws) * (1.0 - p_other)
            if p >= 1e-6:
                rows.append({"mint": mint, "place": k, "p": round(p, 6)})
        rows.sort(key=lambda r: (-r["p"], r["mint"]))
        probabilities[k] = rows[:MAX_ROWS_PER_PLACE]

    # p=0 naming: the scorer shares a place's unnamed mass among the chart
    # coins not named there, so naming sure losers at p=0 concentrates that
    # share on the coins that might take the place (newcomers included).
    # Candidates: on the cut's chart, never in the top 5 in any Monte Carlo
    # draw, peak below P0_PEAK_MAX.
    zero_mints = {coin["mint"] for coin in coin_list
                  if all(place_counts[k].get(coin["mint"], 0) == 0
                         for k in range(1, places + 1))}
    last_rows = marks[-1].get("rows", [])[:100]
    rank_of = {row["mint"]: i + 1 for i, row in enumerate(last_rows)}
    p0_mints = sorted(
        (rank_of[coin["mint"]], coin["mint"])
        for coin in coin_list
        if coin["mint"] in zero_mints
        and rank_of.get(coin["mint"]) is not None
        and P0_RANK_LO <= rank_of[coin["mint"]] <= P0_RANK_HI
        and coin["peak"] <= P0_PEAK_MAX)
    for k in range(1, places + 1):
        room = MAX_ROWS_PER_PLACE - len(probabilities[k])
        for _, mint in p0_mints[:room]:
            probabilities[k].append({"mint": mint, "place": k, "p": 0.0})

    sizes = {}
    for k in range(1, places + 1):
        bins = {}
        for b, count in size_counts[k].items():
            p = (count / total_draws) * (1.0 - p_other)
            if p >= 1e-6:
                bins[b] = round(p, 6)
        for b in range(82):
            lo = -float("inf") if b == 0 else edges[b - 1]
            hi = float("inf") if b == 81 else edges[b]
            p_new = math.floor((norm_cdf(hi, SPIKE_MU, SPIKE_SD)
                                - norm_cdf(lo, SPIKE_MU, SPIKE_SD)) * 1_000_000) / 1_000_000
            if p_new > 0:
                bins[b] = round(bins.get(b, 0.0) + p_other * p_new, 6)
        total = sum(bins.values())
        if total > 1.0:
            bins = {b: round(p / total, 6) for b, p in bins.items()}
        sizes[k] = [{"place": k, "bin": b, "p": p}
                    for b, p in sorted(bins.items()) if p > 0]

    # Top 5 (tie-break only): highest probability of being in the top 5,
    # then lowest expected rank of its best place; ordered by expected place.
    def p_top5(mint):
        return top5_count.get(mint, 0) / total_draws

    def expected_rank(mint):
        return exp_rank.get(mint, 1e18) / total_draws

    contenders = set()
    for k in range(1, places + 1):
        contenders.update(place_counts[k].keys())
    ordered = sorted(contenders, key=lambda m: (-p_top5(m), expected_rank(m), m))
    top5 = ordered[:places]

    def expected_place(mint):
        probs = {k: next((r["p"] for r in probabilities[k] if r["mint"] == mint), 0.0)
                 for k in range(1, places + 1)}
        tot = sum(probs.values()) or 1.0
        return sum(k * probs[k] for k in range(1, places + 1)) / tot

    top5 = sorted(top5, key=lambda m: (expected_place(m), m))
    if len(top5) < places:
        # Fall back to the highest projected centres to fill the list.
        rest = sorted((c for c in coin_list if c["mint"] not in top5),
                      key=lambda c: (-c["last_logmc"], c["mint"]))
        top5 = top5 + [c["mint"] for c in rest[:places - len(top5)]]

    prob_rows = []
    for k in range(1, places + 1):
        prob_rows.extend(probabilities[k])
    size_rows = []
    for k in range(1, places + 1):
        size_rows.extend(sizes[k])
    return {"probabilities": prob_rows, "top5": top5, "sizes": size_rows}


def main():
    input_dir = os.environ.get("MAKEFUN_INPUT_DIR", "/input")
    output_dir = os.environ.get("MAKEFUN_OUTPUT_DIR", "/output")
    task, marks = load_input(input_dir)
    ok_marks = [m for m in marks if m.get("ok") and isinstance(m.get("rows"), list)]
    if not ok_marks:
        raise SystemExit("the history holds no ok mark")
    result = forecast(task, ok_marks)
    os.makedirs(output_dir, exist_ok=True)
    with open(os.path.join(output_dir, task.get("output", "forecast.json")),
              "w", encoding="utf-8") as f:
        f.write(json.dumps(result) + "\n")


if __name__ == "__main__":
    main()
