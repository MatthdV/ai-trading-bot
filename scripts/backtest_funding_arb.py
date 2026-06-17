"""
Backtest delta-neutre de la stratégie Funding Rate Arbitrage.

Modèle réaliste :
- Long spot + Short perp simultanément → exposition prix ≈ 0
- P&L = funding income collecté toutes les 8h (longs paient les shorts)
- Coûts : fee d'entrée + fee de sortie (spot + futures)
- Risque résiduel : funding rate flip négatif (les shorts paient les longs)

Usage :
    python scripts/backtest_funding_arb.py
    python scripts/backtest_funding_arb.py --pair BTC/USDT --entry 0.10 --exit 0.03
    python scripts/backtest_funding_arb.py --train-end 2024-06-30 (walk-forward)
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

# --- Config par défaut ---
DATA_DIR = Path("freqtrade_config/data/binance/futures")
DEFAULT_PAIRS = ["BTC/USDT", "ETH/USDT", "SOL/USDT", "BNB/USDT", "XRP/USDT"]

FEE_TAKER = 0.0004       # 0.04% par leg (spot + futures) = 0.08% aller-retour
FEE_ROUND_TRIP = FEE_TAKER * 4  # spot entry + spot exit + fut entry + fut exit

PERIODS_PER_YEAR = 3 * 365  # 1 095 périodes de 8h


def pair_to_filename(pair: str) -> str:
    return pair.replace("/", "_").replace(":USDT", "")


def load_funding(pair: str) -> pd.DataFrame | None:
    fname = pair_to_filename(pair)
    path = DATA_DIR / f"{fname}_USDT-1h-funding_rate.feather"
    if not path.exists():
        print(f"  ⚠  Funding rate introuvable : {path}")
        return None
    df = pd.read_feather(path)
    # Freqtrade stocke le funding rate dans la colonne 'open'
    df = df[["date", "open"]].rename(columns={"open": "funding_rate"})
    df["date"] = pd.to_datetime(df["date"], utc=True)
    # Garde uniquement les lignes où un paiement a lieu (toutes les 8h)
    df = df[df["date"].dt.hour.isin([0, 8, 16])].copy()
    df = df.sort_values("date").reset_index(drop=True)
    return df


@dataclass
class Position:
    entry_date: pd.Timestamp
    cumulative_funding: float = 0.0  # Funding collecté depuis l'entrée


@dataclass
class BacktestResult:
    pair: str
    trades: list[dict] = field(default_factory=list)

    def add_trade(
        self,
        entry: pd.Timestamp,
        exit_: pd.Timestamp,
        funding_collected: float,
        exit_reason: str,
    ) -> None:
        net = funding_collected - FEE_ROUND_TRIP
        self.trades.append({
            "pair": self.pair,
            "entry_date": entry,
            "exit_date": exit_,
            "duration_days": (exit_ - entry).total_seconds() / 86400,
            "funding_gross": round(funding_collected, 6),
            "fees": round(FEE_ROUND_TRIP, 6),
            "net_profit_pct": round(net * 100, 4),
            "exit_reason": exit_reason,
        })


def run_backtest(
    pair: str,
    funding_df: pd.DataFrame,
    entry_threshold_annual: float = 0.15,
    exit_threshold_annual: float = 0.03,
    funding_window: int = 9,          # périodes de 8h pour le rolling mean
    timerange_start: pd.Timestamp | None = None,
    timerange_end: pd.Timestamp | None = None,
) -> BacktestResult:
    result = BacktestResult(pair=pair)

    df = funding_df.copy()
    if timerange_start:
        df = df[df["date"] >= timerange_start]
    if timerange_end:
        df = df[df["date"] <= timerange_end]
    df = df.reset_index(drop=True)

    if df.empty:
        return result

    # Rolling mean annualisé
    df["funding_mean"] = df["funding_rate"].rolling(window=funding_window, min_periods=1).mean()
    df["funding_annual"] = df["funding_mean"] * PERIODS_PER_YEAR

    # Simulation barre par barre
    pos: Position | None = None

    for _, row in df.iterrows():
        fr_annual = row["funding_annual"]
        date = row["date"]
        fr_period = row["funding_rate"]  # Taux réel pour cette période de 8h

        if pos is None:
            # Pas en position : chercher un signal d'entrée
            if fr_annual > entry_threshold_annual:
                pos = Position(entry_date=date)
        else:
            # En position : collecter le funding de cette période
            # On collecte uniquement si le funding est positif (on est short)
            pos.cumulative_funding += fr_period

            # Vérifier sortie
            if fr_annual < exit_threshold_annual:
                result.add_trade(
                    entry=pos.entry_date,
                    exit_=date,
                    funding_collected=pos.cumulative_funding,
                    exit_reason="funding_too_low",
                )
                pos = None

    # Fermer la position ouverte à la fin de la période
    if pos is not None and not df.empty:
        result.add_trade(
            entry=pos.entry_date,
            exit_=df["date"].iloc[-1],
            funding_collected=pos.cumulative_funding,
            exit_reason="end_of_period",
        )

    return result


def summarize(results: list[BacktestResult], label: str) -> dict:
    all_trades = []
    for r in results:
        all_trades.extend(r.trades)

    if not all_trades:
        print(f"\n{label}: Aucun trade.")
        return {}

    df = pd.DataFrame(all_trades)
    total_profit = df["net_profit_pct"].sum()
    wins = df[df["net_profit_pct"] > 0]
    losses = df[df["net_profit_pct"] <= 0]

    # Sharpe (basé sur les P&L en % par trade)
    mean_p = df["net_profit_pct"].mean()
    std_p = df["net_profit_pct"].std()
    sharpe = (mean_p / std_p * (len(df) ** 0.5)) if std_p > 0 else 0.0

    # Annualized return estimate
    if not df.empty:
        total_days = (df["exit_date"].max() - df["entry_date"].min()).days
        years = total_days / 365
        annualized = (total_profit / 100) / years * 100 if years > 0 else 0.0
    else:
        annualized = 0.0

    print(f"\n{'═' * 60}")
    print(f"  {label}")
    print(f"{'═' * 60}")
    print(f"  Trades                : {len(df)}")
    print(f"  Wins / Losses         : {len(wins)} / {len(losses)}")
    print(f"  Win rate              : {len(wins) / len(df) * 100:.1f}%")
    print(f"  Avg net profit/trade  : {mean_p:.4f}%")
    print(f"  Total net profit      : {total_profit:.2f}%")
    print(f"  Annualized (rough)    : {annualized:.1f}%/an")
    print(f"  Avg duration          : {df['duration_days'].mean():.1f} jours")
    print(f"  Sharpe (per-trade)    : {sharpe:.2f}")
    print(f"  Avg funding gross     : {df['funding_gross'].mean() * 100:.4f}%/trade")
    print(f"  Exit reasons          : {df['exit_reason'].value_counts().to_dict()}")

    if total_days := (df["exit_date"].max() - df["entry_date"].min()).days:
        print(f"  Période               : {df['entry_date'].min().date()} → {df['exit_date'].max().date()} ({total_days} jours)")

    return {
        "trades": len(df),
        "win_rate": len(wins) / len(df),
        "total_profit_pct": total_profit,
        "annualized_pct": annualized,
        "sharpe": sharpe,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Backtest Funding Rate Arbitrage (delta-neutre)")
    parser.add_argument("--pairs", nargs="+", default=DEFAULT_PAIRS)
    parser.add_argument("--entry", type=float, default=0.15, help="Seuil d'entrée annualisé (défaut: 0.15 = 15%%/an)")
    parser.add_argument("--exit", type=float, default=0.03, help="Seuil de sortie annualisé (défaut: 0.03 = 3%%/an)")
    parser.add_argument("--window", type=int, default=9, help="Fenêtre rolling mean en périodes 8h (défaut: 9 = 3 jours)")
    parser.add_argument("--train-end", default="2024-06-30", help="Fin de la période train (walk-forward)")
    args = parser.parse_args()

    train_end = pd.Timestamp(args.train_end, tz="UTC")
    oos_start = train_end + pd.Timedelta(hours=8)

    print(f"\nFunding Rate Arbitrage Backtest — delta-neutre")
    print(f"Paramètres : entry={args.entry * 100:.1f}%/an, exit={args.exit * 100:.1f}%/an, window={args.window} périodes")
    print(f"Walk-forward : Train → {train_end.date()}  |  OOS {oos_start.date()} →")

    train_results = []
    oos_results = []

    for pair in args.pairs:
        print(f"\n  Chargement {pair} …", end="")
        df = load_funding(pair)
        if df is None:
            continue
        print(f" {len(df)} périodes")

        train = run_backtest(
            pair, df,
            entry_threshold_annual=args.entry,
            exit_threshold_annual=args.exit,
            funding_window=args.window,
            timerange_end=train_end,
        )
        oos = run_backtest(
            pair, df,
            entry_threshold_annual=args.entry,
            exit_threshold_annual=args.exit,
            funding_window=args.window,
            timerange_start=oos_start,
        )
        train_results.append(train)
        oos_results.append(oos)

    summarize(train_results, f"TRAIN — 2020-01 → {train_end.date()}")
    summarize(oos_results, f"OOS   — {oos_start.date()} → aujourd'hui")


if __name__ == "__main__":
    main()
