"""
Monitoring automatique des dry-runs Freqtrade.

Lit les DBs SQLite et calcule :
- Sharpe rolling 7 jours
- P&L cumulé
- Drawdown max
- Alerte si Sharpe rolling < 1.0 ou drawdown > 5%

Usage :
    python scripts/monitor_dryrun.py
    python scripts/monitor_dryrun.py --alert-only  # affiche uniquement les alertes
"""

from __future__ import annotations

import argparse
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

FUTURES_DB = Path("freqtrade_config/tradesv3_futures_dryrun.sqlite")
BREAKOUT_DB = Path("freqtrade_config/tradesv3.sqlite")

SHARPE_MIN = 1.0
DRAWDOWN_MAX = 0.05  # 5%


def load_trades(db_path: Path) -> pd.DataFrame:
    if not db_path.exists():
        return pd.DataFrame()

    conn = sqlite3.connect(db_path)
    try:
        df = pd.read_sql_query(
            """
            SELECT
                id,
                pair,
                open_date,
                close_date,
                profit_ratio,
                profit_abs,
                is_open,
                stake_amount,
                open_rate,
                close_rate
            FROM trades
            ORDER BY open_date ASC
            """,
            conn,
            parse_dates=["open_date", "close_date"],
        )
    except Exception as e:
        print(f"  Erreur lecture DB {db_path}: {e}")
        return pd.DataFrame()
    finally:
        conn.close()

    if df.empty:
        return df

    df["open_date"] = pd.to_datetime(df["open_date"], utc=True, errors="coerce")
    df["close_date"] = pd.to_datetime(df["close_date"], utc=True, errors="coerce")
    return df


def compute_rolling_sharpe(closed_trades: pd.DataFrame, days: int = 7) -> float:
    if closed_trades.empty:
        return 0.0

    cutoff = datetime.now(tz=timezone.utc) - timedelta(days=days)
    recent = closed_trades[closed_trades["close_date"] >= cutoff]

    if len(recent) < 2:
        return 0.0

    pnl = recent["profit_ratio"]
    mean = pnl.mean()
    std = pnl.std()
    if std == 0:
        return 0.0
    return float(mean / std * (len(pnl) ** 0.5))


def compute_max_drawdown(closed_trades: pd.DataFrame) -> float:
    if closed_trades.empty:
        return 0.0

    cumulative = (1 + closed_trades["profit_ratio"]).cumprod()
    rolling_max = cumulative.cummax()
    drawdown = (cumulative - rolling_max) / rolling_max
    return float(drawdown.min())  # negative value


def print_report(label: str, db_path: Path, alert_only: bool) -> dict:
    df = load_trades(db_path)

    if df.empty:
        if not alert_only:
            print(f"\n  {label}: Aucune donnée (DB introuvable ou vide)")
        return {}

    closed = df[df["is_open"] == 0].copy()
    open_trades = df[df["is_open"] == 1]

    total_profit_pct = closed["profit_ratio"].sum() * 100 if not closed.empty else 0.0
    wins = len(closed[closed["profit_ratio"] > 0])
    losses = len(closed[closed["profit_ratio"] <= 0])
    win_rate = wins / len(closed) * 100 if len(closed) > 0 else 0.0
    rolling_sharpe = compute_rolling_sharpe(closed)
    max_dd = compute_max_drawdown(closed)

    alerts = []
    if rolling_sharpe < SHARPE_MIN and len(closed) >= 3:
        alerts.append(f"Sharpe rolling 7j = {rolling_sharpe:.2f} < {SHARPE_MIN} ⚠️")
    if max_dd < -DRAWDOWN_MAX:
        alerts.append(f"Max drawdown = {max_dd * 100:.1f}% > {DRAWDOWN_MAX * 100:.0f}% ⚠️")

    if alert_only:
        if alerts:
            print(f"\n🚨 ALERTES [{label}]")
            for a in alerts:
                print(f"   {a}")
        return {"alerts": alerts}

    print(f"\n{'═' * 60}")
    print(f"  {label}")
    print(f"{'═' * 60}")
    print(f"  Trades fermés       : {len(closed)}")
    print(f"  Trades ouverts      : {len(open_trades)}")
    print(f"  Wins / Losses       : {wins} / {losses}  ({win_rate:.1f}% WR)")
    print(f"  P&L total           : {total_profit_pct:+.2f}%")
    print(f"  Sharpe rolling 7j   : {rolling_sharpe:.2f}")
    print(f"  Max drawdown        : {max_dd * 100:.1f}%")
    if not closed.empty:
        print(f"  Avg profit/trade    : {closed['profit_ratio'].mean() * 100:.3f}%")
        last_close = closed["close_date"].max()
        print(f"  Dernier trade       : {last_close.strftime('%Y-%m-%d %H:%M UTC') if pd.notna(last_close) else 'N/A'}")

    if not open_trades.empty:
        print(f"\n  Positions ouvertes :")
        for _, t in open_trades.iterrows():
            print(f"    [{t['pair']}] depuis {t['open_date'].strftime('%Y-%m-%d %H:%M') if pd.notna(t['open_date']) else 'N/A'} @ {t['open_rate']:.4f}")

    if alerts:
        print(f"\n  🚨 ALERTES :")
        for a in alerts:
            print(f"     {a}")
    else:
        print(f"\n  ✅ Aucune alerte")

    return {
        "closed": len(closed),
        "open": len(open_trades),
        "total_profit_pct": total_profit_pct,
        "sharpe_7d": rolling_sharpe,
        "max_drawdown": max_dd,
        "alerts": alerts,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Monitor dry-run Freqtrade")
    parser.add_argument("--alert-only", action="store_true", help="Affiche uniquement les alertes")
    args = parser.parse_args()

    now = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    if not args.alert_only:
        print(f"\nFreqtrade Dry-Run Monitor — {now}")
        print("=" * 60)

    futures_report = print_report("FundingRateArbitrage (Futures)", FUTURES_DB, args.alert_only)
    breakout_report = print_report("BreakoutTrendFollowing (Spot)", BREAKOUT_DB, args.alert_only)

    if not args.alert_only:
        all_alerts = futures_report.get("alerts", []) + breakout_report.get("alerts", [])
        if all_alerts:
            print(f"\n{'═' * 60}")
            print(f"  RÉSUMÉ ALERTES ({len(all_alerts)} alertes actives)")
            print(f"{'═' * 60}")
        else:
            print(f"\n✅ Tout nominal — {now}")


if __name__ == "__main__":
    main()
