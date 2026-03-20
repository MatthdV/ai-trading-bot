"""
Télécharge l'historique des funding rates Binance Futures pour les paires configurées.
Sauvegarde en parquet dans freqtrade_config/data/funding_rates/.

Usage :
    python scripts/download_funding_rates.py
    python scripts/download_funding_rates.py --since 2020-01-01 --pairs BTC/USDT ETH/USDT
"""

import argparse
import time
from datetime import datetime, timezone
from pathlib import Path

import ccxt
import pandas as pd


OUTPUT_DIR = Path("freqtrade_config/data/funding_rates")
DEFAULT_PAIRS = ["BTC/USDT", "ETH/USDT", "SOL/USDT", "BNB/USDT", "XRP/USDT"]
# Binance funding rates sont versées toutes les 8h (00:00, 08:00, 16:00 UTC)
FUNDING_INTERVAL_MS = 8 * 3600 * 1000


def pair_to_futures_symbol(pair: str) -> str:
    """BTC/USDT → BTC/USDT:USDT (format CCXT linear perp)"""
    base, quote = pair.split("/")
    return f"{base}/{quote}:{quote}"


def output_path(pair: str) -> Path:
    """BTC/USDT → freqtrade_config/data/funding_rates/BTC_USDT_funding.parquet"""
    safe = pair.replace("/", "_")
    return OUTPUT_DIR / f"{safe}_funding.parquet"


def download_pair(exchange: ccxt.Exchange, pair: str, since_ms: int) -> pd.DataFrame:
    symbol = pair_to_futures_symbol(pair)
    print(f"  Téléchargement {symbol} depuis {datetime.fromtimestamp(since_ms / 1000, tz=timezone.utc).date()} …")

    all_records: list[dict] = []
    now_ms = exchange.milliseconds()

    while since_ms < now_ms - FUNDING_INTERVAL_MS:
        try:
            batch = exchange.fetch_funding_rate_history(symbol, since=since_ms, limit=1000)
        except ccxt.BadSymbol:
            print(f"  ⚠  {symbol} non disponible sur Binance Futures — skip")
            return pd.DataFrame()

        if not batch:
            break

        all_records.extend(batch)
        last_ts = batch[-1]["timestamp"]

        if last_ts >= now_ms - FUNDING_INTERVAL_MS:
            break
        since_ms = last_ts + 1
        time.sleep(0.2)  # Rate limit

    if not all_records:
        print(f"  Aucune donnée pour {symbol}")
        return pd.DataFrame()

    df = pd.DataFrame([
        {
            "date": pd.Timestamp(r["timestamp"], unit="ms", tz="UTC"),
            "symbol": pair,
            "funding_rate": float(r["fundingRate"]),
        }
        for r in all_records
    ])
    df = df.sort_values("date").drop_duplicates("date").reset_index(drop=True)
    print(f"  ✓  {len(df)} entrées ({df['date'].iloc[0].date()} → {df['date'].iloc[-1].date()})")
    return df


def main() -> None:
    parser = argparse.ArgumentParser(description="Télécharge les funding rates Binance Futures")
    parser.add_argument("--since", default="2020-01-01", help="Date de début (YYYY-MM-DD)")
    parser.add_argument("--pairs", nargs="+", default=DEFAULT_PAIRS)
    args = parser.parse_args()

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    since_dt = datetime.strptime(args.since, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    since_ms = int(since_dt.timestamp() * 1000)

    exchange = ccxt.binance({"options": {"defaultType": "future"}})
    exchange.load_markets()

    for pair in args.pairs:
        path = output_path(pair)

        # Reprise : si fichier existant, télécharge seulement les nouvelles données
        if path.exists():
            existing = pd.read_parquet(path)
            last_ts = int(existing["date"].max().timestamp() * 1000)
            fetch_since = last_ts + 1
            print(f"{pair} — reprise depuis {existing['date'].max().date()}")
        else:
            existing = pd.DataFrame()
            fetch_since = since_ms

        new_df = download_pair(exchange, pair, fetch_since)

        if new_df.empty and existing.empty:
            continue

        combined = pd.concat([existing, new_df], ignore_index=True)
        combined = combined.sort_values("date").drop_duplicates("date").reset_index(drop=True)
        combined.to_parquet(path, index=False)
        print(f"  Sauvegardé : {path} ({len(combined)} lignes total)\n")

    print("✅ Funding rates téléchargés.")


if __name__ == "__main__":
    main()
