from freqtrade.strategy import IStrategy, DecimalParameter, IntParameter
import talib.abstract as ta
from pandas import DataFrame


class MomentumFreqtrade(IStrategy):
    """
    Momentum — MACD + EMA + ADX + Volume
    Portée depuis strategies/momentum.py
    """

    INTERFACE_VERSION = 3

    can_short = False

    timeframe = '1h'
    startup_candle_count: int = 200  # EMA(200) warmup

    stoploss = -0.03  # Hard stop 3%
    trailing_stop = True
    trailing_stop_positive = 0.01  # Active trailing après +1%
    trailing_stop_positive_offset = 0.02
    trailing_only_offset_is_reached = True

    # Paramètres optimisés via Hyperopt v2 — avec filtre EMA(200) (train 2024-01 → 2025-06, epoch #158)
    adx_entry = DecimalParameter(15, 30, default=20.74, space="buy")
    adx_exit = DecimalParameter(10, 20, default=18.725, space="sell")
    volume_min = DecimalParameter(0.8, 2.0, default=1.971, space="buy")

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        # MACD
        macd = ta.MACD(dataframe, fastperiod=12, slowperiod=26, signalperiod=9)
        dataframe['macd'] = macd['macd']
        dataframe['macd_signal'] = macd['macdsignal']
        dataframe['macd_hist'] = macd['macdhist']

        # MACD crossover detection
        dataframe['macd_cross_up'] = (
            (dataframe['macd'] > dataframe['macd_signal']) &
            (dataframe['macd'].shift(1) <= dataframe['macd_signal'].shift(1))
        )
        dataframe['macd_cross_down'] = (
            (dataframe['macd'] < dataframe['macd_signal']) &
            (dataframe['macd'].shift(1) >= dataframe['macd_signal'].shift(1))
        )

        # EMA
        dataframe['ema_fast'] = ta.EMA(dataframe, timeperiod=9)
        dataframe['ema_slow'] = ta.EMA(dataframe, timeperiod=21)

        # ADX
        dataframe['adx'] = ta.ADX(dataframe, timeperiod=14)

        # Relative volume
        dataframe['vol_avg'] = dataframe['volume'].rolling(window=20).mean()
        dataframe['rel_volume'] = dataframe['volume'] / dataframe['vol_avg']

        # Macro filter — EMA(200) pour éviter les trades en marché bear
        dataframe['ema200'] = ta.EMA(dataframe, timeperiod=200)

        return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe.loc[
            (dataframe['close'] > dataframe['ema200']) &  # filtre macro bear
            (dataframe['macd_cross_up']) &
            (dataframe['ema_fast'] > dataframe['ema_slow']) &
            (dataframe['adx'] > self.adx_entry.value) &
            (dataframe['rel_volume'] > self.volume_min.value),
            'enter_long'] = 1

        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        # Exit on opposite MACD cross
        dataframe.loc[dataframe['macd_cross_down'], 'exit_long'] = 1

        # Exit on ADX collapse
        dataframe.loc[dataframe['adx'] < self.adx_exit.value, 'exit_long'] = 1

        return dataframe
