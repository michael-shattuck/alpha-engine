import asyncio
import time
import logging
import psycopg2
import httpx

from server.config import DATABASE_URL, BIRDEYE_API_KEY

log = logging.getLogger("backfill")

TOKENS = {
    "SOL": "So11111111111111111111111111111111111111112",
    "JUP": "JUPyiwrYJFskUPiHa7hkeR8VUtAeFoSYbKedZNsDvCN",
    "JTO": "jtojtomepa8beP8AuQc6eXt5FriJwfFMwQx2v2f9mCL",
    "PYTH": "HZ1JovNiVvGrGNiiYvEozEVgZ58xaU3RKwX8eACQBCt3",
    "W": "85VBFQZC9TZkfaptBWjvUw7YbZjy52A6mjtPGjstQAmQ",
    "SUI": "G1vJEgzepqhnVu35BN4jrkv3wVwkujYWFFCxhbEZ1CZr",
    "SEI": "5q2EfdKrV4oSaUGBWCMWjXbYNuSPEFMTig4kgFquTCkB",
}

BATCH_SIZE = 1000
RATE_LIMIT_DELAY = 0.5


def ensure_table():
    conn = psycopg2.connect(DATABASE_URL)
    conn.autocommit = True
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS ohlcv_1m (
            asset TEXT NOT NULL,
            timestamp BIGINT NOT NULL,
            open DOUBLE PRECISION NOT NULL,
            high DOUBLE PRECISION NOT NULL,
            low DOUBLE PRECISION NOT NULL,
            close DOUBLE PRECISION NOT NULL,
            volume DOUBLE PRECISION DEFAULT 0,
            PRIMARY KEY (asset, timestamp)
        )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_ohlcv_asset_ts ON ohlcv_1m(asset, timestamp DESC)")
    conn.close()


def insert_candles(asset: str, candles: list[dict]):
    if not candles:
        return 0
    conn = psycopg2.connect(DATABASE_URL)
    cur = conn.cursor()
    values = []
    for c in candles:
        values.append((
            asset,
            c["unixTime"],
            c["o"],
            c["h"],
            c["l"],
            c["c"],
            c.get("v", 0),
        ))
    cur.executemany("""
        INSERT INTO ohlcv_1m (asset, timestamp, open, high, low, close, volume)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (asset, timestamp) DO NOTHING
    """, values)
    inserted = cur.rowcount
    conn.commit()
    conn.close()
    return inserted


def get_latest_timestamp(asset: str) -> int:
    try:
        conn = psycopg2.connect(DATABASE_URL)
        cur = conn.cursor()
        cur.execute("SELECT MAX(timestamp) FROM ohlcv_1m WHERE asset = %s", (asset,))
        row = cur.fetchone()
        conn.close()
        return row[0] if row and row[0] else 0
    except Exception:
        return 0


async def backfill_asset(http: httpx.AsyncClient, asset: str, mint: str, days: int = 90):
    now = int(time.time())
    latest = get_latest_timestamp(asset)

    if latest > 0:
        start = latest + 60
        log.info(f"{asset}: resuming from {time.strftime('%Y-%m-%d %H:%M', time.gmtime(start))}")
    else:
        start = now - days * 86400
        log.info(f"{asset}: backfilling {days} days from {time.strftime('%Y-%m-%d', time.gmtime(start))}")

    total = 0
    cursor = start

    while cursor < now:
        chunk_end = min(cursor + BATCH_SIZE * 60, now)
        try:
            r = await http.get("https://public-api.birdeye.so/defi/ohlcv", params={
                "address": mint,
                "type": "1m",
                "time_from": cursor,
                "time_to": chunk_end,
            }, headers={"X-API-KEY": BIRDEYE_API_KEY, "x-chain": "solana"})

            if r.status_code == 429:
                log.warning(f"{asset}: rate limited, waiting 5s")
                await asyncio.sleep(5)
                continue

            if r.status_code != 200:
                log.error(f"{asset}: HTTP {r.status_code}: {r.text[:100]}")
                await asyncio.sleep(2)
                cursor = chunk_end
                continue

            items = r.json().get("data", {}).get("items", [])
            if items:
                inserted = insert_candles(asset, items)
                total += inserted
                newest = items[-1]["unixTime"]
                cursor = newest + 60
                if total % 5000 == 0:
                    log.info(f"{asset}: {total} candles stored, at {time.strftime('%Y-%m-%d %H:%M', time.gmtime(newest))}")
            else:
                cursor = chunk_end

        except Exception as e:
            log.error(f"{asset}: error: {e}")
            await asyncio.sleep(2)
            cursor = chunk_end

        await asyncio.sleep(RATE_LIMIT_DELAY)

    log.info(f"{asset}: backfill complete. {total} new candles.")
    return total


async def backfill_all(days: int = 90):
    ensure_table()
    async with httpx.AsyncClient(timeout=30) as http:
        for asset, mint in TOKENS.items():
            total = await backfill_asset(http, asset, mint, days)
            log.info(f"{asset}: {total} candles")


def get_stats():
    conn = psycopg2.connect(DATABASE_URL)
    cur = conn.cursor()
    cur.execute("""
        SELECT asset, COUNT(*) as cnt,
               MIN(timestamp) as oldest, MAX(timestamp) as newest
        FROM ohlcv_1m GROUP BY asset ORDER BY asset
    """)
    rows = cur.fetchall()
    conn.close()
    stats = []
    for asset, cnt, oldest, newest in rows:
        days = (newest - oldest) / 86400 if oldest and newest else 0
        stats.append({
            "asset": asset,
            "candles": cnt,
            "oldest": time.strftime("%Y-%m-%d", time.gmtime(oldest)) if oldest else "none",
            "newest": time.strftime("%Y-%m-%d", time.gmtime(newest)) if newest else "none",
            "days": round(days, 1),
        })
    return stats


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s")
    asyncio.run(backfill_all(90))
    for s in get_stats():
        print(f"  {s['asset']:5s}: {s['candles']:>8,} candles, {s['days']}d ({s['oldest']} to {s['newest']})")
