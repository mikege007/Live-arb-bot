
import asyncio
import random
import time
from dataclasses import dataclass
from typing import Optional, Tuple, List

# ===============================
# CONFIG
# ===============================

SEED = 123
RUNTIME_SECONDS = 40

# Shock settings (major plays/news)
SHOCK_FREQ_SEC = (4, 8)            # random interval between shocks
BOOK_UPDATE_LAG_MS = 450           # BookB lags BookA by this much after a shock

# Book execution frictions
BOOK_LATENCY_MS = {"BookA": (80, 180), "BookB": (90, 220)}
BOOK_REJECT_PROB = {"BookA": 0.06, "BookB": 0.08}

# Strategy knobs
TOTAL_OUTLAY = 200.0               # total capital across both legs when arbing
MIN_ARBITRAGE_EDGE = 0.005         # 0.5% minimum locked ROI required
MAX_CONCURRENT = 2
PRINT_TRADES = True

# ===============================
# UTILS
# ===============================

def now_ms():
    return int(time.time() * 1000)

def dec_from_american(a: int) -> float:
    if a > 0:
        return 1.0 + a/100.0
    else:
        return 1.0 + 100.0/abs(a)

def imp_prob_from_dec(o: float) -> float:
    return 1.0 / o

def american_str(a: int) -> str:
    return f"{'+' if a>0 else ''}{a}"

def solve_equalized_stakes(oA: float, oB: float, total: float) -> Tuple[float, float, float]:
    """
    Solve stakes SA (on A) and SB (on B) s.t. payoff if A wins equals payoff if B wins,
    given total capital constraint SA + SB = total.
    For decimal odds oA, oB: payoff_A = SA * oA; payoff_B = SB * oB.
    We want SA*oA = SB*oB and SA+SB=total => SA = total * oB / (oA + oB); SB = total - SA.
    Returns (SA, SB, locked_profit).
    """
    SA = total * oB / (oA + oB)
    SB = total - SA
    locked = SA*(oA-1) - SB  # equal by construction
    return SA, SB, locked

# ===============================
# MODELS
# ===============================

@dataclass
class Quote:
    book: str
    event_id: str
    side: str      # "A" or "B"
    decimal: float
    ts_ms: int

@dataclass
class Ticket:
    book: str
    event_id: str
    side: str
    stake: float
    decimal_odds: float
    accepted: bool
    ts_ms: int
    note: str = ""

@dataclass
class Trade:
    event_id: str
    A_book: str
    B_book: str
    stake_A: float
    stake_B: float
    oA: float
    oB: float
    profit_locked: float
    roi: float
    ts_ms: int

# ===============================
# FEED: SHOCKS WITH BOOK LAG
# ===============================

class ShockFeed:
    """
    Keeps fair odds for a 2-way market. On shocks, flips/tilts the fair odds sharply.
    BookA updates immediately; BookB updates after BOOK_UPDATE_LAG_MS, creating a stale window.
    """
    def __init__(self, event_id: str = "EVT1"):
        self.event_id = event_id
        self.fair_dec_A = 2.0    # start near pick'em
        self.fair_dec_B = 2.0
        self.subscribers = []

    def subscribe(self, cb):
        self.subscribers.append(cb)

    async def run(self):
        end_t = time.time() + RUNTIME_SECONDS
        next_shock = time.time() + random.uniform(*SHOCK_FREQ_SEC)
        while time.time() < end_t:
            t = time.time()
            if t >= next_shock:
                await self._emit_shock()
                next_shock = t + random.uniform(*SHOCK_FREQ_SEC)
            await asyncio.sleep(0.02)

    async def _emit_shock(self):
        # Flip or tilt odds strongly
        fav = random.choice(["A", "B"])
        fav_dec = random.uniform(1.30, 1.70)
        dog_dec = max(1.05, 1.0 / (1.0 - 1.0/fav_dec))
        if fav == "A":
            self.fair_dec_A, self.fair_dec_B = fav_dec, dog_dec
        else:
            self.fair_dec_A, self.fair_dec_B = dog_dec, fav_dec

        ts = now_ms()

        # BookA updates immediately to near-fair with a little juice
        A_dec_A = max(1.05, self.fair_dec_A * random.uniform(0.98, 1.02))
        A_dec_B = max(1.05, self.fair_dec_B * random.uniform(0.98, 1.02))

        qA_A = Quote("BookA", self.event_id, "A", A_dec_A, ts)
        qA_B = Quote("BookA", self.event_id, "B", A_dec_B, ts)
        self._emit([qA_A, qA_B])

        # BookB lags; emit stale pre-shock quotes first (mirror of BookA before shock)
        # Create stale by swapping which side is favored compared to BookA
        stale_A = max(1.05, self.fair_dec_B * random.uniform(0.98, 1.02))
        stale_B = max(1.05, self.fair_dec_A * random.uniform(0.98, 1.02))
        qB_A_stale = Quote("BookB", self.event_id, "A", stale_A, ts)
        qB_B_stale = Quote("BookB", self.event_id, "B", stale_B, ts)
        self._emit([qB_A_stale, qB_B_stale])

        # After lag, BookB catches up
        await asyncio.sleep(BOOK_UPDATE_LAG_MS / 1000.0)
        ts2 = now_ms()
        B_dec_A = max(1.05, self.fair_dec_A * random.uniform(0.98, 1.03))
        B_dec_B = max(1.05, self.fair_dec_B * random.uniform(0.98, 1.03))
        qB_A = Quote("BookB", self.event_id, "A", B_dec_A, ts2)
        qB_B = Quote("BookB", self.event_id, "B", B_dec_B, ts2)
        self._emit([qB_A, qB_B])

    def _emit(self, quotes: List[Quote]):
        for cb in self.subscribers:
            cb(quotes)

# ===============================
# BOOK EXECUTION (MOCK)
# ===============================

class MockBook:
    def __init__(self, name: str):
        self.name = name

    async def place(self, event_id: str, side: str, stake: float, decimal_odds: float) -> Ticket:
        lat = random.randint(*BOOK_LATENCY_MS[self.name])
        await asyncio.sleep(lat/1000.0)
        if random.random() < BOOK_REJECT_PROB[self.name]:
            return Ticket(self.name, event_id, side, stake, decimal_odds, False, now_ms(), "rejected")
        return Ticket(self.name, event_id, side, stake, decimal_odds, True, now_ms())

# ===============================
# SHOCK-ARB BOT
# ===============================

class ShockArbBot:
    def __init__(self):
        self.quotes = {}  # (book, side) -> Quote
        self.open_trades = 0
        self.trades: List[Trade] = []
        self.books = {"BookA": MockBook("BookA"), "BookB": MockBook("BookB")}

    def on_quotes(self, quotes: List[Quote]):
        for q in quotes:
            self.quotes[(q.book, q.side)] = q

    def find_opportunity(self) -> Optional[Tuple[str, str, float, float]]:
        # Opposite sides across books with implied prob sum < 1 by threshold
        combos = [
            (("BookA","A"), ("BookB","B")),
            (("BookB","A"), ("BookA","B"))
        ]
        best = None
        best_edge = 0.0
        for (b1,s1),(b2,s2) in combos:
            q1 = self.quotes.get((b1,s1))
            q2 = self.quotes.get((b2,s2))
            if not q1 or not q2:
                continue
            p_sum = imp_prob_from_dec(q1.decimal) + imp_prob_from_dec(q2.decimal)
            edge = 1.0 - p_sum
            if edge > best_edge:
                best_edge = edge
                best = (b1,b2,q1.decimal,q2.decimal)
        if best and best_edge >= MIN_ARBITRAGE_EDGE:
            return best
        return None

    async def try_trade(self, event_id: str):
        if self.open_trades >= MAX_CONCURRENT:
            return
        opp = self.find_opportunity()
        if not opp:
            return
        bA, bB, oA, oB = opp
        SA, SB, locked = solve_equalized_stakes(oA, oB, TOTAL_OUTLAY)
        roi = locked / TOTAL_OUTLAY

        self.open_trades += 1
        t1 = await self.books[bA].place(event_id, "A", SA, oA)
        t2 = await self.books[bB].place(event_id, "B", SB, oB)
        self.open_trades -= 1

        if not (t1.accepted and t2.accepted):
            # In a real bot, you would mitigate orphan risk here
            return

        tr = Trade(event_id, bA, bB, SA, SB, oA, oB, locked, roi, now_ms())
        self.trades.append(tr)
        if PRINT_TRADES:
            print(f"[TRADE] {bA} A@{oA:.2f} stake {SA:.2f}  |  {bB} B@{oB:.2f} stake {SB:.2f}  "
                  f"locked ${locked:.2f}  ROI {roi:.2%}")

    def summary(self):
        if not self.trades:
            print("\nNo completed arbitrages.")
            return
        n = len(self.trades)
        avg_roi = sum(t.roi for t in self.trades)/n
        total_locked = sum(t.profit_locked for t in self.trades)
        print("\n==== SHOCK-ARB SESSION SUMMARY ====")
        print(f"trades: {n}")
        print(f"avg locked ROI: {avg_roi:.2%}")
        print(f"total locked profit (assuming perfect fill): ${total_locked:.2f}")
        print("===================================\n")

# ===============================
# MAIN
# ===============================

async def main():
    random.seed(SEED)
    feed = ShockFeed("EVT1")
    bot = ShockArbBot()
    feed.subscribe(bot.on_quotes)

    async def engine():
        end_t = time.time() + RUNTIME_SECONDS
        while time.time() < end_t:
            await bot.try_trade("EVT1")
            await asyncio.sleep(0.01)
        bot.summary()

    await asyncio.gather(feed.run(), engine())

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Interrupted.")
