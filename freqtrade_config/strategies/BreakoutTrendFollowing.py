from freqtrade.strategy import IStrategy, DecimalParameter, IntParameter
import talib.abstract as ta
from pandas import DataFrame


class BreakoutTrendFollowing(IStrategy):
    """
    Breakout Trend-Following — Turtle Trading System 2 (Donchian Channel)

    Params classiques Turtle System 2 sur 4h :
    - Entrée  : breakout du plus haut Donchian 480 bougies (= 80 jours)
    - Sortie  : close sous le plus bas Donchian 240 bougies (= 40 jours)
    - Filtre  : close > EMA(200) — pas de long en bear market
    - Filtre  : ADX > 25 — tendance établie, pas un range
    - Stop    : -10% hard stop (faux breakouts)

    Pas d'Hyperopt — les paramètres sont les defaults historiques Turtle.
    La stratégie ne trade PAS en bear/range : le filtre EMA(200) la protège.
    Elle est conçue pour capturer les grandes tendances bull crypto.
    """

    INTERFACE_VERSION = 3

    can_short = False

    timeframe = '4h'
    startup_candle_count: int = 700  # EMA(200) + Donchian(480) + ATR(180) warmup

    # ROI désactivé — sortie uniquement via Donchian ou hard stop
    minimal_roi = {"0": 100}

    # Hard stop -10% — pas de trailing stop (Turtle classique)
    stoploss = -0.10
    trailing_stop = False

    # Paramètres Turtle System 2 — fixes, pas d'Hyperopt
    # 480 bougies 4h = 80 jours (~55j Turtle S2 arrondi conservateur)
    # 240 bougies 4h = 40 jours (sortie Turtle S2 = 20j, ici plus conservateur)
    donchian_entry = IntParameter(240, 960, default=480, space="buy", optimize=False)
    donchian_exit = IntParameter(120, 480, default=240, space="sell", optimize=False)
    adx_min = DecimalParameter(15, 30, default=25.0, space="buy", optimize=False)

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        # Donchian channel — shift(1) pour éviter le look-ahead
        dataframe['dc_high'] = dataframe['high'].shift(1).rolling(
            window=self.donchian_entry.value
        ).max()
        dataframe['dc_low'] = dataframe['low'].shift(1).rolling(
            window=self.donchian_exit.value
        ).min()

        # ADX — filtre tendance forte
        dataframe['adx'] = ta.ADX(dataframe, timeperiod=14)

        # EMA(200) — filtre macro bear market
        dataframe['ema200'] = ta.EMA(dataframe, timeperiod=200)

        return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe.loc[
            (dataframe['close'] > dataframe['ema200']) &    # marché haussier
            (dataframe['close'] > dataframe['dc_high']) &   # breakout Donchian 80j
            (dataframe['adx'] > self.adx_min.value),        # tendance confirmée
            'enter_long'] = 1

        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        # Sortie : close sous le plus bas Donchian 40j (fin de tendance)
        dataframe.loc[
            dataframe['close'] < dataframe['dc_low'],
            'exit_long'] = 1

        return dataframe
