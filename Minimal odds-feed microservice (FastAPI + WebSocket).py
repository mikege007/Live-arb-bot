# odds_feed_service.py
# pip install fastapi uvicorn httpx
import os, time, asyncio, httpx
from fastapi import FastAPI, WebSocket
from typing import List, Dict

API_KEY = os.getenv("THEODDS_API_KEY")  # get one from the site
SPORTS   = ["basketball_nba", "americanfootball_nfl"]  # pick your leagues
REGIONS  = "us"                 # US books only
MARKETS  = "h2h,spreads,totals" # start with h2h; add others as needed
ODDS_FMT = "decimal"            # your bot wants decimal

app = FastAPI()
subscribers: List[WebSocket] = []
latest: Dict[str, dict] = {}   # key: event_id|book|market|side -> record

def normalize_event(ev, book, market, outcome) -> dict:
    return {
        "event_id": ev["id"],
        "sport_key": ev["sport_key"],
        "commence": ev.get("commence_time"),
        "book": book["key"],
        "market": market["key"],   # h2h/spreads/totals
        "number": market.get("point", None),
        "side": outcome["name"],
        "odds_dec": float(outcome["price"]),
        "ts_ms": int(time.time() * 1000),
    }

async def fetch_once(client, sport):
    url = f"https://api.the-odds-api.com/v4/sports/{sport}/odds"
    params = dict(apiKey=API_KEY, regions=REGIONS, markets=MARKETS, oddsFormat=ODDS_FMT)
    r = await client.get(url, params=params, timeout=8.0)
    r.raise_for_status()
    return r.json()

async def producer_loop():
    async with httpx.AsyncClient() as client:
        while True:
            for sport in SPORTS:
                try:
                    data = await fetch_once(client, sport)
                    updates = []
                    for ev in data:
                        for book in ev.get("bookmakers", []):
                            for mkt in book.get("markets", []):
                                for out in mkt.get("outcomes", []):
                                    rec = normalize_event(ev, book, mkt, out)
                                    key = f'{rec["event_id"]}|{rec["book"]}|{rec["market"]}|{rec["number"]}|{rec["side"]}'
                                    if latest.get(key) != rec:
                                        latest[key] = rec
                                        updates.append(rec)
                    if updates:
                        dead = []
                        for ws in subscribers:
                            try:
                                await ws.send_json({"type":"odds_batch","updates":updates})
                            except Exception:
                                dead.append(ws)
                        for ws in dead:
                            subscribers.remove(ws)
                except Exception:
                    pass
                await asyncio.sleep(1.0)   # tune per plan/limits
            await asyncio.sleep(0.5)

@app.on_event("startup")
async def _start():
    asyncio.create_task(producer_loop())

@app.get("/v1/snapshot")
def snapshot():
    # simple latest view for debugging
    return list(latest.values())[:500]

@app.websocket("/v1/stream")
async def ws(ws: WebSocket):
    await ws.accept()
    subscribers.append(ws)
    try:
        while True:
            await ws.receive_text()  # keepalive (optional)
    finally:
        if ws in subscribers:
            subscribers.remove(ws)






# Run:

# export THEODDS_API_KEY=your_key_here
# uvicorn odds_feed_service:app --host 0.0.0.0 --port 8080


# Your bot connects to ws://HOST:8080/v1/stream, receives batches like:

# {"type":"odds_batch","updates":[{"book":"fanduel","market":"h2h","side":"Team A","odds_dec":2.12, ...}]}

