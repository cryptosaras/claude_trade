"""Market regime detection from BTC_USDT 1h candles: BULL / BEAR / SIDE."""
import pandas as pd

from . import indicators as ind


def detect(df_1h: pd.DataFrame, cfg: dict) -> tuple[str, float, dict]:
    """df_1h: BTC 1h OHLCV, ascending index. Returns (label, confidence, meta)."""
    if len(df_1h) < cfg["ema_slow"] + 10:
        return "SIDE", 0.0, {"reason": "insufficient history"}
    c = df_1h["c"]
    ema_fast = ind.ema(c, cfg["ema_fast"])
    ema_slow = ind.ema(c, cfg["ema_slow"])
    adx = ind.adx(df_1h, cfg["adx_period"])
    # slope of slow EMA over last 24 bars, normalized
    slope = (ema_slow.iloc[-1] - ema_slow.iloc[-24]) / ema_slow.iloc[-24]
    a = float(adx.iloc[-1])
    trending = a >= cfg["adx_trend_min"]
    above = c.iloc[-1] > ema_slow.iloc[-1] and ema_fast.iloc[-1] > ema_slow.iloc[-1]
    below = c.iloc[-1] < ema_slow.iloc[-1] and ema_fast.iloc[-1] < ema_slow.iloc[-1]
    if trending and above and slope > 0.001:
        label = "BULL"
    elif trending and below and slope < -0.001:
        label = "BEAR"
    else:
        label = "SIDE"
    # confidence: how decisively conditions are met
    conf = min(1.0, abs(slope) * 200 + a / 60)
    meta = {"adx": round(a, 1), "ema_slope_24h": round(float(slope), 5),
            "close": float(c.iloc[-1])}
    return label, round(conf, 2), meta
