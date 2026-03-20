#!/usr/bin/env python3
"""
Alpaca API Client
Handles all interactions with Alpaca Markets API
Uses requests with connection pooling, timeout, and caching.
"""

import os
import json
import time
import logging
from typing import Optional, Dict, List, Any

import pandas as pd
import requests

logger = logging.getLogger(__name__)

REQUEST_TIMEOUT = 10  # seconds


class _BarCache:
    """Simple in-memory cache with TTL for bar data."""

    def __init__(self, ttl: int = 60):
        self._ttl = ttl
        self._store: Dict[str, tuple] = {}  # key → (timestamp, data)

    def get(self, key: str) -> Optional[List[Dict[str, Any]]]:
        entry = self._store.get(key)
        if entry is None:
            return None
        ts, data = entry
        if time.monotonic() - ts > self._ttl:
            del self._store[key]
            return None
        return data

    def set(self, key: str, data: List[Dict[str, Any]]) -> None:
        self._store[key] = (time.monotonic(), data)


class AlpacaClient:
    """Alpaca API client for trading operations"""

    def __init__(self):
        self.api_key = os.getenv('ALPACA_API_KEY')
        self.secret_key = os.getenv('ALPACA_SECRET_KEY')
        self.base_url = os.getenv('ALPACA_BASE_URL', 'https://paper-api.alpaca.markets/v2')

        if not self.api_key or not self.secret_key:
            raise ValueError("Alpaca API credentials not found in environment")

        self._session = requests.Session()
        self._session.headers.update({
            'APCA-API-KEY-ID': self.api_key,
            'APCA-API-SECRET-KEY': self.secret_key,
            'Content-Type': 'application/json',
        })

        self._bar_cache = _BarCache(ttl=60)

        logger.info(f"Alpaca client initialized: {self.base_url}")

    # ------------------------------------------------------------------
    # Internal request helper
    # ------------------------------------------------------------------

    def _request(
        self,
        method: str,
        endpoint: str,
        params: Optional[Dict] = None,
        data: Optional[Dict] = None,
    ) -> Any:
        """Make authenticated request to Alpaca API"""
        url = f"{self.base_url}/{endpoint}"

        kwargs: Dict[str, Any] = {'timeout': REQUEST_TIMEOUT}
        if params:
            kwargs['params'] = params
        if data and method in ('POST', 'PUT'):
            kwargs['json'] = data

        try:
            resp = self._session.request(method, url, **kwargs)
            resp.raise_for_status()
            return resp.json() if resp.content else None
        except requests.exceptions.HTTPError as exc:
            status = exc.response.status_code if exc.response is not None else 0
            body = exc.response.text if exc.response is not None else ''
            logger.error(f"API error {status}: {body}")
            if status == 404:
                return None
            raise
        except requests.exceptions.Timeout:
            logger.error(f"Request timeout ({REQUEST_TIMEOUT}s): {method} {endpoint}")
            raise
        except requests.exceptions.ConnectionError as exc:
            logger.error(f"Connection error: {exc}")
            raise

    # ------------------------------------------------------------------
    # Account & positions
    # ------------------------------------------------------------------

    def get_account(self) -> Dict[str, Any]:
        return self._request('GET', 'account')

    def get_positions(self) -> List[Dict[str, Any]]:
        result = self._request('GET', 'positions')
        return result if result else []

    def get_position(self, symbol: str) -> Optional[Dict[str, Any]]:
        return self._request('GET', f'positions/{symbol}')

    # ------------------------------------------------------------------
    # Orders
    # ------------------------------------------------------------------

    def submit_order(
        self,
        symbol: str,
        qty: float,
        side: str,
        type: str = 'market',
        time_in_force: str = 'day',
        limit_price: Optional[float] = None,
        stop_price: Optional[float] = None,
    ) -> Dict[str, Any]:
        data: Dict[str, Any] = {
            'symbol': symbol,
            'qty': str(qty),
            'side': side,
            'type': type,
            'time_in_force': time_in_force,
        }
        if limit_price:
            data['limit_price'] = str(limit_price)
        if stop_price:
            data['stop_price'] = str(stop_price)

        return self._request('POST', 'orders', data=data)

    def get_orders(self, status: str = 'open') -> List[Dict[str, Any]]:
        result = self._request('GET', 'orders', params={'status': status})
        return result if result else []

    def cancel_order(self, order_id: str) -> None:
        self._request('DELETE', f'orders/{order_id}')

    # ------------------------------------------------------------------
    # Market data
    # ------------------------------------------------------------------

    def get_bars(
        self,
        symbol: str,
        timeframe: str = '1D',
        limit: int = 100,
        start: Optional[str] = None,
        end: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        cache_key = f"{symbol}:{timeframe}:{limit}:{start}:{end}"
        cached = self._bar_cache.get(cache_key)
        if cached is not None:
            return cached

        params: Dict[str, str] = {
            'symbols': symbol,
            'timeframe': timeframe,
            'limit': str(limit),
        }
        if start:
            params['start'] = start
        if end:
            params['end'] = end

        response = self._request('GET', 'stocks/bars', params=params)
        bars = response['bars'].get(symbol, []) if response and 'bars' in response else []

        self._bar_cache.set(cache_key, bars)
        return bars

    def get_ohlcv_dataframe(
        self,
        symbol: str,
        timeframe: str = '1D',
        limit: int = 100,
        start: Optional[str] = None,
        end: Optional[str] = None,
    ) -> pd.DataFrame:
        """Return OHLCV data as a DataFrame with DatetimeIndex.

        Compatible with ``BaseStrategy.analyze()``: columns are
        ``open``, ``high``, ``low``, ``close``, ``volume``.
        """
        bars = self.get_bars(symbol, timeframe=timeframe, limit=limit, start=start, end=end)
        if not bars:
            return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])

        df = pd.DataFrame(bars)

        # Alpaca v2 bars use short keys (t/o/h/l/c/v)
        rename_map: Dict[str, str] = {}
        if "t" in df.columns:
            rename_map["t"] = "timestamp"
        if "o" in df.columns:
            rename_map["o"] = "open"
        if "h" in df.columns:
            rename_map["h"] = "high"
        if "l" in df.columns:
            rename_map["l"] = "low"
        if "c" in df.columns:
            rename_map["c"] = "close"
        if "v" in df.columns:
            rename_map["v"] = "volume"
        if rename_map:
            df = df.rename(columns=rename_map)

        # Build DatetimeIndex
        if "timestamp" in df.columns:
            df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
            df = df.set_index("timestamp").sort_index()

        # Keep only OHLCV columns
        ohlcv_cols = [c for c in ["open", "high", "low", "close", "volume"] if c in df.columns]
        df = df[ohlcv_cols]

        # Ensure numeric types
        for col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

        return df

    def get_latest_quote(self, symbol: str) -> Dict[str, Any]:
        response = self._request('GET', f'stocks/{symbol}/quotes/latest')
        return response.get('quote', {}) if response else {}

    def get_latest_trade(self, symbol: str) -> Dict[str, Any]:
        response = self._request('GET', f'stocks/{symbol}/trades/latest')
        return response.get('trade', {}) if response else {}

    # ------------------------------------------------------------------
    # Market status
    # ------------------------------------------------------------------

    def is_market_open(self) -> bool:
        clock = self._request('GET', 'clock')
        return clock.get('is_open', False) if clock else False

    def get_clock(self) -> Dict[str, Any]:
        return self._request('GET', 'clock') or {}

    def get_assets(self, status: str = 'active') -> List[Dict[str, Any]]:
        result = self._request('GET', 'assets', params={'status': status})
        return result if result else []
