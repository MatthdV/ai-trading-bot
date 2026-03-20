"""
Funding Rate Arbitrage — Short perp + Long spot quand funding rate élevé.

Logique :
- Le funding rate Binance est payé toutes les 8h (00:00, 08:00, 16:00 UTC).
- Quand il est positif et élevé, les longs paient les shorts → on short le perp.
- En live : hedgé avec un long spot équivalent (delta-neutre, ~market-neutral).
- En backtest : on modélise le côté futures uniquement (risque directionnel présent).

Signal d'entrée :
  - Funding rate moyen glissant 3 jours (9 périodes × 8h) annualisé > seuil
  - Prix > BB lower (pas de crash en cours)

Signal de sortie :
  - Funding rate glissant tombe sous le seuil de rentabilité (exit le carry)
  - Stop -5% (protection directionnelle en backtest non-hedgé)

Données :
  - Freqtrade télécharge les funding rates nativement avec download-data --futures
  - Accessible via DataProvider + CandleType.FUNDING_RATE
  - Run : freqtrade download-data --config config_funding.json --timeframe 1h --timerange 20200101-
"""

import logging

import pandas as pd
import talib.abstract as ta
from freqtrade.constants import Config
from freqtrade.enums import CandleType
from freqtrade.strategy import DecimalParameter, IStrategy

logger = logging.getLogger(__name__)

# Funding rate annualisé : 3 paiements × 365 jours
PERIODS_PER_YEAR = 3 * 365  # 1 095 périodes de 8h par an


class FundingRateArbitrage(IStrategy):
    """
    Funding rate arbitrage sur futures Binance.
    Short le perp quand le taux de financement est élevé et persistant.
    """

    INTERFACE_VERSION = 3

    can_short = True  # Requiert Freqtrade mode futures

    timeframe = "1h"
    startup_candle_count: int = 100  # Bollinger(20) + buffer

    minimal_roi = {"0": 100}  # Sortie uniquement via signal

    stoploss = -0.05  # Protection directionnelle (annulée par le long spot en live)
    trailing_stop = False

    # Seuils de funding rate (annualisés)
    # Défauts : entrer à 15%/an (~0.041%/8h), sortir à 3%/an (~0.008%/8h)
    funding_entry_annual = DecimalParameter(0.05, 0.50, default=0.15, space="buy")
    funding_exit_annual = DecimalParameter(0.01, 0.10, default=0.03, space="sell")

    # Fenêtre glissante pour le funding rate moyen (périodes de 8h)
    # 9 périodes = 3 jours — lisse le bruit sans lag excessif
    FUNDING_WINDOW = 9  # fixe — pas d'Hyperopt pour rester interprétable

    def informative_pairs(self) -> list:
        """Déclare les paires de funding rate pour le fetch live (n'affecte pas le backtest)."""
        pairs = self.dp.current_whitelist()
        return [(pair, self.timeframe, CandleType.FUNDING_RATE) for pair in pairs]

    def populate_indicators(self, dataframe: pd.DataFrame, metadata: dict) -> pd.DataFrame:
        pair = metadata["pair"]

        # --- Funding rate natif Freqtrade ---
        # Freqtrade stocke le funding rate comme candles OHLCV
        # où open = funding_rate pour cette période de 8h
        funding_df = self.dp.get_pair_dataframe(pair, self.timeframe, CandleType.FUNDING_RATE)

        if funding_df is not None and not funding_df.empty:
            funding_df = funding_df[["date", "open"]].rename(columns={"open": "funding_rate"})
            funding_df = funding_df.sort_values("date")

            # Merge asof : associe chaque bougie 1h au dernier funding connu (8h)
            dataframe = dataframe.sort_values("date")
            merged = pd.merge_asof(
                dataframe[["date"]],
                funding_df,
                on="date",
                direction="backward",
            )
            dataframe["funding_rate"] = merged["funding_rate"].values

            # Rolling mean sur FUNDING_WINDOW périodes de 8h (= FUNDING_WINDOW × 8 candles 1h)
            window_candles = self.FUNDING_WINDOW * 8  # 72 candles 1h = 3 jours
            dataframe["funding_rate_mean"] = (
                dataframe["funding_rate"].rolling(window=window_candles, min_periods=1).mean()
            )
            dataframe["funding_annual"] = dataframe["funding_rate_mean"] * PERIODS_PER_YEAR
        else:
            logger.warning(
                f"Funding rate data manquant pour {pair}. "
                "Lance: freqtrade download-data --config freqtrade_config/config_funding.json "
                "--timeframe 1h --timerange 20200101-"
            )
            dataframe["funding_rate"] = 0.0
            dataframe["funding_rate_mean"] = 0.0
            dataframe["funding_annual"] = 0.0

        # --- Bollinger Bands (filtre anti-crash) ---
        bb = ta.BBANDS(dataframe, timeperiod=20, nbdevup=2.0, nbdevdn=2.0)
        dataframe["bb_lower"] = bb["lowerband"]
        dataframe["bb_middle"] = bb["middleband"]

        return dataframe

    def populate_entry_trend(self, dataframe: pd.DataFrame, metadata: dict) -> pd.DataFrame:
        # Short quand :
        # 1. Funding annualisé persistant > seuil (ex. 15%/an = ~0.041%/8h)
        # 2. Prix > BB lower (pas en crash — un crash annule le bénéfice du funding)
        dataframe.loc[
            (dataframe["funding_annual"] > self.funding_entry_annual.value)
            & (dataframe["close"] > dataframe["bb_lower"]),
            "enter_short",
        ] = 1
        return dataframe

    def populate_exit_trend(self, dataframe: pd.DataFrame, metadata: dict) -> pd.DataFrame:
        # Sortie quand le carry ne vaut plus (après fees)
        dataframe.loc[
            dataframe["funding_annual"] < self.funding_exit_annual.value,
            "exit_short",
        ] = 1
        return dataframe
