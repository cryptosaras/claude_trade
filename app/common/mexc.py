"""MEXC futures (contract) public API client. No API key needed for market data."""
import time

import httpx


class Mexc:
    def __init__(self, base_url: str = "https://contract.mexc.com", rps: float = 5.0):
        self.client = httpx.Client(base_url=base_url, timeout=20)
        self.min_interval = 1.0 / rps
        self._last = 0.0

    def _get(self, path: str, params: dict | None = None) -> dict:
        wait = self.min_interval - (time.time() - self._last)
        if wait > 0:
            time.sleep(wait)
        self._last = time.time()
        for attempt in range(3):
            try:
                r = self.client.get(path, params=params)
                if r.status_code == 429:
                    time.sleep(2 ** (attempt + 1))
                    continue
                r.raise_for_status()
                data = r.json()
                if not data.get("success", True):
                    raise RuntimeError(f"MEXC error on {path}: {data}")
                return data
            except (httpx.TransportError, httpx.HTTPStatusError):
                if attempt == 2:
                    raise
                time.sleep(2 ** attempt)
        raise RuntimeError(f"MEXC request failed: {path}")

    def contracts(self) -> list[dict]:
        return self._get("/api/v1/contract/detail")["data"]

    def tickers(self) -> list[dict]:
        return self._get("/api/v1/contract/ticker")["data"]

    def funding_rate(self, symbol: str) -> dict:
        return self._get(f"/api/v1/contract/funding_rate/{symbol}")["data"]

    def klines(self, symbol: str, interval: str, start: int, end: int) -> list[tuple]:
        """interval: Min1|Min5|Min15|Min60|Hour4|Day1. start/end unix seconds.
        Returns [(ts_sec, o, h, l, c, vol), ...] ascending."""
        data = self._get(
            f"/api/v1/contract/kline/{symbol}",
            {"interval": interval, "start": start, "end": end},
        )["data"]
        if not data or not data.get("time"):
            return []
        return list(zip(
            data["time"], data["open"], data["high"], data["low"],
            data["close"], data["vol"],
        ))
