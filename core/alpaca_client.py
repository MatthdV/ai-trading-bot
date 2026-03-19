#!/usr/bin/env python3
"""
Alpaca API Client (using standard library only)
Handles all interactions with Alpaca Markets API
"""

import os
import json
import urllib.request
import urllib.error
import logging
from typing import Optional, Dict, List, Any

logger = logging.getLogger(__name__)

class AlpacaClient:
    """Alpaca API client for trading operations"""
    
    def __init__(self):
        self.api_key = os.getenv('ALPACA_API_KEY')
        self.secret_key = os.getenv('ALPACA_SECRET_KEY')
        self.base_url = os.getenv('ALPACA_BASE_URL', 'https://paper-api.alpaca.markets/v2')
        
        if not self.api_key or not self.secret_key:
            raise ValueError("Alpaca API credentials not found in environment")
        
        logger.info(f"Alpaca client initialized: {self.base_url}")
    
    def _request(self, method: str, endpoint: str, params: Optional[Dict] = None, data: Optional[Dict] = None) -> Any:
        """Make authenticated request to Alpaca API"""
        url = f"{self.base_url}/{endpoint}"
        
        # Add query params
        if params:
            query_string = '&'.join([f"{k}={v}" for k, v in params.items()])
            url = f"{url}?{query_string}"
        
        # Create request
        req = urllib.request.Request(url, method=method)
        req.add_header('APCA-API-KEY-ID', self.api_key)
        req.add_header('APCA-API-SECRET-KEY', self.secret_key)
        req.add_header('Content-Type', 'application/json')
        
        # Add body for POST/PUT
        if data and method in ['POST', 'PUT']:
            req.data = json.dumps(data).encode('utf-8')
        
        try:
            with urllib.request.urlopen(req) as response:
                return json.loads(response.read().decode('utf-8'))
        except urllib.error.HTTPError as e:
            logger.error(f"API error {e.code}: {e.read().decode('utf-8')}")
            if e.code == 404:
                return None
            raise
    
    def get_account(self) -> Dict[str, Any]:
        """Get account information"""
        return self._request('GET', 'account')
    
    def get_positions(self) -> List[Dict[str, Any]]:
        """Get all open positions"""
        result = self._request('GET', 'positions')
        return result if result else []
    
    def get_position(self, symbol: str) -> Optional[Dict[str, Any]]:
        """Get position for specific symbol"""
        return self._request('GET', f'positions/{symbol}')
    
    def submit_order(
        self,
        symbol: str,
        qty: float,
        side: str,
        type: str = 'market',
        time_in_force: str = 'day',
        limit_price: Optional[float] = None,
        stop_price: Optional[float] = None
    ) -> Dict[str, Any]:
        """Submit a new order"""
        data = {
            'symbol': symbol,
            'qty': str(qty),
            'side': side,
            'type': type,
            'time_in_force': time_in_force
        }
        
        if limit_price:
            data['limit_price'] = str(limit_price)
        if stop_price:
            data['stop_price'] = str(stop_price)
        
        return self._request('POST', 'orders', data=data)
    
    def get_orders(self, status: str = 'open') -> List[Dict[str, Any]]:
        """Get orders"""
        result = self._request('GET', 'orders', params={'status': status})
        return result if result else []
    
    def cancel_order(self, order_id: str) -> None:
        """Cancel an order"""
        self._request('DELETE', f'orders/{order_id}')
    
    def get_bars(
        self,
        symbol: str,
        timeframe: str = '1D',
        limit: int = 100,
        start: Optional[str] = None,
        end: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """Get historical price bars"""
        params = {
            'symbols': symbol,
            'timeframe': timeframe,
            'limit': str(limit)
        }
        if start:
            params['start'] = start
        if end:
            params['end'] = end
        
        response = self._request('GET', 'stocks/bars', params=params)
        if response and 'bars' in response:
            return response['bars'].get(symbol, [])
        return []
    
    def get_latest_quote(self, symbol: str) -> Dict[str, Any]:
        """Get latest quote for symbol"""
        response = self._request('GET', f'stocks/{symbol}/quotes/latest')
        return response.get('quote', {}) if response else {}
    
    def get_latest_trade(self, symbol: str) -> Dict[str, Any]:
        """Get latest trade for symbol"""
        response = self._request('GET', f'stocks/{symbol}/trades/latest')
        return response.get('trade', {}) if response else {}
    
    def is_market_open(self) -> bool:
        """Check if market is currently open"""
        clock = self._request('GET', 'clock')
        return clock.get('is_open', False) if clock else False
    
    def get_clock(self) -> Dict[str, Any]:
        """Get market clock"""
        return self._request('GET', 'clock') or {}
    
    def get_assets(self, status: str = 'active') -> List[Dict[str, Any]]:
        """Get list of tradable assets"""
        result = self._request('GET', 'assets', params={'status': status})
        return result if result else []
