from freqtrade.strategy import IStrategy, DecimalParameter, IntParameter
import talib.abstract as ta
from pandas import DataFrame


class MeanReversionFreqtrade(IStrategy):
    """
    Mean Reversion — RSI + Bollinger + Z-Score
    Portée depuis strategies/mean_reversion.py
    """

    INTERFACE_VERSION = 3

    can_short = False

    # Timeframe
    timeframe = '1h'

    # Risk
    stoploss = -0.05  # stop_loss_pct=0.05
    trailing_stop = False

    # Exit timeout (168h = 7 jours)
    # Géré via custom_exit()

    # Paramètres optimisés via Hyperopt (train 2024-01 → 2025-06, epoch #16)
    rsi_period = IntParameter(10, 20, default=11, space="buy")
    rsi_oversold = DecimalParameter(25, 40, default=25.329, space="buy")
    rsi_overbought = DecimalParameter(60, 75, default=62.835, space="sell")
    bb_period = IntParameter(15, 30, default=30, space="buy")
    bb_std = DecimalParameter(1.0, 2.5, default=1.262, space="buy")
    zscore_entry = DecimalParameter(1.0, 2.5, default=1.36, space="buy")

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        # RSI
        dataframe['rsi'] = ta.RSI(dataframe, timeperiod=self.rsi_period.value)

        # Bollinger Bands
        bb = ta.BBANDS(dataframe, timeperiod=self.bb_period.value,
                       nbdevup=self.bb_std.value, nbdevdn=self.bb_std.value)
        dataframe['bb_upper'] = bb['upperband']
        dataframe['bb_middle'] = bb['middleband']
        dataframe['bb_lower'] = bb['lowerband']

        # Z-Score
        sma = dataframe['close'].rolling(window=20).mean()
        std = dataframe['close'].rolling(window=20).std()
        dataframe['zscore'] = (dataframe['close'] - sma) / std

        return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        # LONG: RSI oversold + below lower BB + z-score < -threshold
        dataframe.loc[
            (dataframe['rsi'] < self.rsi_oversold.value) &
            (dataframe['close'] < dataframe['bb_lower']) &
            (dataframe['zscore'] < -self.zscore_entry.value),
            'enter_long'] = 1

        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        # Take profit: z-score reverts to mean
        dataframe.loc[
            (dataframe['zscore'] > -0.2) & (dataframe['zscore'] < 0.2),
            'exit_long'] = 1

        return dataframe
