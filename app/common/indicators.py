"""Indicator helpers on pandas Series/DataFrames (columns: o,h,l,c,v; index: ts)."""
import numpy as np
import pandas as pd


def ema(s: pd.Series, n: int) -> pd.Series:
    return s.ewm(span=n, adjust=False).mean()


def rsi(s: pd.Series, n: int = 14) -> pd.Series:
    d = s.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    rs = up / dn.replace(0, np.nan)
    return (100 - 100 / (1 + rs)).fillna(50.0)


def atr(df: pd.DataFrame, n: int = 14) -> pd.Series:
    prev_c = df["c"].shift()
    tr = pd.concat(
        [df["h"] - df["l"], (df["h"] - prev_c).abs(), (df["l"] - prev_c).abs()], axis=1
    ).max(axis=1)
    return tr.ewm(alpha=1 / n, adjust=False).mean()


def adx(df: pd.DataFrame, n: int = 14) -> pd.Series:
    up = df["h"].diff()
    dn = -df["l"].diff()
    plus_dm = pd.Series(np.where((up > dn) & (up > 0), up, 0.0), index=df.index)
    minus_dm = pd.Series(np.where((dn > up) & (dn > 0), dn, 0.0), index=df.index)
    tr = atr(df, n)
    plus_di = 100 * plus_dm.ewm(alpha=1 / n, adjust=False).mean() / tr.replace(0, np.nan)
    minus_di = 100 * minus_dm.ewm(alpha=1 / n, adjust=False).mean() / tr.replace(0, np.nan)
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    return dx.ewm(alpha=1 / n, adjust=False).mean().fillna(0.0)


def bollinger(s: pd.Series, n: int = 20, k: float = 2.0):
    mid = s.rolling(n).mean()
    std = s.rolling(n).std()
    return mid - k * std, mid, mid + k * std


def day_vwap(df: pd.DataFrame) -> pd.Series:
    """VWAP anchored to each UTC day."""
    day = df.index.floor("D")
    tp = (df["h"] + df["l"] + df["c"]) / 3
    pv = (tp * df["v"]).groupby(day).cumsum()
    vv = df["v"].groupby(day).cumsum().replace(0, np.nan)
    return (pv / vv).fillna(df["c"])


def resample(df: pd.DataFrame, tf: str) -> pd.DataFrame:
    """Resample 1m OHLCV to e.g. '5min', '15min', '1h'. Drops incomplete last bar? No —
    keeps it; strategies act on the latest (possibly partial) bar deliberately."""
    o = df.resample(tf).agg(
        {"o": "first", "h": "max", "l": "min", "c": "last", "v": "sum"}
    )
    return o.dropna(subset=["c"])
