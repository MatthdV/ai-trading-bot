#!/usr/bin/env python3
"""
validate_hyperopt.py — Validation walk-forward post-Hyperopt

Compare le Sharpe du backtest train vs OOS pour détecter l'overfitting.

Usage :
    python3 scripts/validate_hyperopt.py \
        --train freqtrade_config/backtest_results/<train_result>.zip \
        --oos   freqtrade_config/backtest_results/<oos_result>.zip \
        [--strategy MeanReversionFreqtrade]

Critères :
    PASS    : drop OOS/train < 30%
    WARNING : drop entre 30% et 50%
    FAIL    : drop > 50%
"""

import argparse
import json
import sys
import zipfile
from pathlib import Path


def load_sharpe(result_path: Path, strategy: str | None) -> tuple[str, float]:
    """Charge le Sharpe depuis un fichier zip de résultat Freqtrade."""
    with zipfile.ZipFile(result_path) as zf:
        json_file = next(n for n in zf.namelist() if n.endswith(".json") and "_config" not in n)
        with zf.open(json_file) as f:
            data = json.load(f)

    strategies: dict = data.get("strategy", {})
    if not strategies:
        print(f"[ERROR] Aucune stratégie trouvée dans {result_path.name}", file=sys.stderr)
        sys.exit(1)

    if strategy:
        if strategy not in strategies:
            available = list(strategies.keys())
            print(f"[ERROR] Stratégie '{strategy}' introuvable. Disponibles : {available}", file=sys.stderr)
            sys.exit(1)
        name = strategy
    else:
        name = list(strategies.keys())[0]
        if len(strategies) > 1:
            print(f"[INFO] Plusieurs stratégies trouvées, utilisation de '{name}'. Utilisez --strategy pour en choisir une.")

    sharpe: float = strategies[name].get("sharpe", float("nan"))
    return name, sharpe


def assess(train_sharpe: float, oos_sharpe: float) -> tuple[str, float]:
    """Retourne (verdict, drop_pct)."""
    if train_sharpe <= 0:
        # Sharpe train négatif = résultat déjà mauvais, pas exploitable
        return "FAIL", float("inf")

    drop = (train_sharpe - oos_sharpe) / abs(train_sharpe)

    if drop < 0.30:
        verdict = "PASS"
    elif drop < 0.50:
        verdict = "WARNING"
    else:
        verdict = "FAIL"

    return verdict, drop


def main() -> None:
    parser = argparse.ArgumentParser(description="Validation walk-forward post-Hyperopt")
    parser.add_argument("--train", required=True, type=Path, help="Zip du backtest train")
    parser.add_argument("--oos", required=True, type=Path, help="Zip du backtest OOS")
    parser.add_argument("--strategy", default=None, help="Nom de la stratégie à comparer")
    args = parser.parse_args()

    train_name, train_sharpe = load_sharpe(args.train, args.strategy)
    oos_name, oos_sharpe = load_sharpe(args.oos, args.strategy)

    if train_name != oos_name:
        print(f"[WARNING] Stratégies différentes : train={train_name}, OOS={oos_name}")

    verdict, drop = assess(train_sharpe, oos_sharpe)

    icons = {"PASS": "✅", "WARNING": "⚠️ ", "FAIL": "❌"}
    drop_str = f"{drop * 100:.1f}%" if drop != float("inf") else "N/A (train Sharpe ≤ 0)"

    print()
    print(f"  Stratégie   : {train_name}")
    print(f"  Sharpe train: {train_sharpe:.3f}  (2024-01 → 2025-06)")
    print(f"  Sharpe OOS  : {oos_sharpe:.3f}  (2025-07 → 2026-03)")
    print(f"  Drop        : {drop_str}")
    print()
    print(f"  {icons[verdict]} {verdict}")
    if verdict == "WARNING":
        print("     → Sharpe dégradé (30-50%). Réduire les espaces de paramètres ou allonger la période train.")
    elif verdict == "FAIL":
        print("     → Overfitting probable (drop > 50%). Supprimer les paramètres les plus larges (rsi_period, bb_period).")
    print()

    sys.exit(0 if verdict == "PASS" else 1)


if __name__ == "__main__":
    main()
