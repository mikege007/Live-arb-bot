
# Shock-Arb Sim (event-driven arbitrage)

This sim models **big in-game events** that cause **instant odds flips** at BookA, while **BookB lags** by `BOOK_UPDATE_LAG_MS`,
creating a **stale quote window**. The bot looks for cross-book pairs where the **sum of implied probabilities < 1** and
splits a fixed `TOTAL_OUTLAY` across both legs to **equalize payoffs**, locking profit *if both legs fill at those prices*.

> ⚠️ Sandbox only. No real sportsbook connections. For testing logic and timing.

## Run

```bash
python3 /mnt/data/live_shock_arb_sim.py
```

## Key knobs

- `SHOCK_FREQ_SEC`: how often big plays happen.
- `BOOK_UPDATE_LAG_MS`: lagging book's update delay.
- `BOOK_LATENCY_MS`, `BOOK_REJECT_PROB`: execution frictions.
- `TOTAL_OUTLAY`: capital per arbitrage.
- `MIN_ARBITRAGE_EDGE`: require a margin (e.g., 0.5%).

## Mechanics

- **ShockFeed**: emits a sharp move making one side a strong favorite; BookA updates immediately, BookB updates later.
- **ShockArbBot**: checks if `1/oA + 1/oB < 1 - MIN_ARBITRAGE_EDGE` and places both legs.
- Stakes equalize payoff: `SA = total * oB / (oA + oB)`, `SB = total - SA`, `locked = SA*(oA-1) - SB`.

## Next steps toward a real bot

1. Replace `MockBook` with official APIs (respect ToS).
2. Add orphan-leg mitigation (cash-out, micro-hedge).
3. Market mapping across books.
4. Real-time scoreboard feed to time shocks.
5. Persistence + monitoring.
