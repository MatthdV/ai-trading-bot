#!/usr/bin/env python3
"""
News Analyzer Module
Scrapes financial news and analyzes sentiment using LLM
"""

import os
import sys
import json
import logging
import urllib.request
import urllib.parse
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional
from dataclasses import dataclass

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

@dataclass
class NewsItem:
    """News article with metadata"""
    title: str
    source: str
    published_at: str
    url: str
    symbols: List[str]
    sentiment: float = 0.0  # -1.0 to +1.0
    relevance: float = 0.0   # 0.0 to 1.0

@dataclass
class MarketEvent:
    """Major market event"""
    event_type: str  # 'fed', 'earnings', 'cpi', 'nfp', etc.
    date: str
    description: str
    impact: str  # 'high', 'medium', 'low'
    sentiment: float = 0.0

class NewsAnalyzer:
    """Analyze financial news and market sentiment"""
    
    def __init__(self):
        self.watchlist = [
            'AAPL', 'MSFT', 'GOOGL', 'AMZN', 'TSLA',
            'NVDA', 'META', 'NFLX', 'AMD', 'CRM',
            'BABA', 'UBER', 'COIN', 'PLTR', 'RKLB'
        ]
        
        # Economic calendar - major events to watch
        self.economic_events = self._load_economic_calendar()
        
        logger.info(f"News Analyzer initialized with {len(self.watchlist)} symbols")
    
    def _load_economic_calendar(self) -> List[MarketEvent]:
        """Load major upcoming economic events"""
        # This would ideally come from an API
        # For now, manual tracking of known events
        events = []
        
        # FOMC meetings 2026 (approximate)
        fomc_dates = [
            '2026-03-19', '2026-04-30', '2026-06-11',
            '2026-07-30', '2026-09-17', '2026-10-29', '2026-12-10'
        ]
        
        for date in fomc_dates:
            events.append(MarketEvent(
                event_type='fed',
                date=date,
                description='FOMC Meeting - Interest Rate Decision',
                impact='high'
            ))
        
        return events
    
    def get_news_for_symbol(self, symbol: str) -> List[NewsItem]:
        """Get recent news for a specific symbol"""
        news_items = []
        
        try:
            # Use Brave Search API for news
            query = f"{symbol} stock news today"
            news_items = self._search_news(query, symbol)
            
        except Exception as e:
            logger.error(f"Error fetching news for {symbol}: {e}")
        
        return news_items
    
    def _search_news(self, query: str, symbol: str) -> List[NewsItem]:
        """Search for news using web search"""
        items = []
        
        try:
            # Simple approach: use existing web_search skill results
            # In production, this would use a dedicated news API
            
            # For now, return empty and rely on manual sentiment analysis
            # This is a placeholder for the full implementation
            pass
            
        except Exception as e:
            logger.error(f"News search error: {e}")
        
        return items
    
    def analyze_sentiment(self, text: str) -> float:
        """
        Analyze sentiment of text using keyword matching
        Returns score from -1.0 (very negative) to +1.0 (very positive)
        """
        text_lower = text.lower()
        
        # Positive keywords
        positive_words = [
            'surge', 'soar', 'jump', 'rally', 'bull', 'breakthrough',
            'beat', 'exceed', 'strong', 'growth', 'profit', 'gain',
            'upgrade', 'buy', 'outperform', 'moon', 'rocket',
            'record', 'high', 'surpass', 'crush', 'smash'
        ]
        
        # Negative keywords
        negative_words = [
            'crash', 'plunge', 'drop', 'fall', 'bear', 'sell-off',
            'miss', 'weak', 'loss', 'decline', 'dump', 'panic',
            'downgrade', 'sell', 'underperform', 'tank', 'plummet',
            'low', 'fail', 'disappoint', 'concern', 'warning'
        ]
        
        positive_count = sum(1 for word in positive_words if word in text_lower)
        negative_count = sum(1 for word in negative_words if word in text_lower)
        
        total = positive_count + negative_count
        if total == 0:
            return 0.0
        
        # Calculate sentiment score
        sentiment = (positive_count - negative_count) / total
        
        # Normalize to -1 to +1
        return max(-1.0, min(1.0, sentiment))
    
    def check_upcoming_events(self, days_ahead: int = 7) -> List[MarketEvent]:
        """Check for major market events in the next N days"""
        today = datetime.now().date()
        upcoming = []
        
        for event in self.economic_events:
            event_date = datetime.strptime(event.date, '%Y-%m-%d').date()
            days_until = (event_date - today).days
            
            if 0 <= days_until <= days_ahead:
                upcoming.append(event)
        
        return upcoming
    
    def get_market_sentiment(self) -> Dict[str, Any]:
        """Get overall market sentiment"""
        try:
            # Check VIX (volatility index) as fear gauge
            # In production, this would fetch real VIX data
            
            # For now, return neutral
            return {
                'overall_sentiment': 0.0,  # -1 to +1
                'vix_level': 'neutral',     # low, neutral, high
                'risk_on_off': 'neutral',   # risk_on, neutral, risk_off
                'events_this_week': len(self.check_upcoming_events(7)),
                'last_updated': datetime.now().isoformat()
            }
        except Exception as e:
            logger.error(f"Error getting market sentiment: {e}")
            return {'overall_sentiment': 0.0, 'error': str(e)}
    
    def adjust_signal_confidence(
        self,
        symbol: str,
        technical_confidence: float,
        action: str
    ) -> tuple:
        """
        Adjust technical signal confidence based on news sentiment
        Returns: (adjusted_confidence, sentiment_score, reason)
        """
        # Get news for symbol
        news = self.get_news_for_symbol(symbol)
        
        # Calculate average sentiment from news
        if news:
            avg_sentiment = sum(n.sentiment for n in news) / len(news)
        else:
            avg_sentiment = 0.0
        
        # Check for upcoming earnings or events
        events = self.check_upcoming_events(7)
        symbol_events = [e for e in events if symbol in e.description.upper()]
        
        # Adjust confidence based on sentiment alignment
        adjusted_confidence = technical_confidence
        reason = "Technical signal only"
        
        if action == 'buy':
            if avg_sentiment > 0.5:
                # Strong positive news supports buy
                adjusted_confidence = min(1.0, technical_confidence * 1.2)
                reason = f"Technical + Positive news ({avg_sentiment:+.2f})"
            elif avg_sentiment < -0.5:
                # Negative news contradicts buy
                adjusted_confidence = technical_confidence * 0.7
                reason = f"Technical but Negative news ({avg_sentiment:+.2f})"
        
        elif action == 'sell':
            if avg_sentiment < -0.5:
                # Strong negative news supports sell
                adjusted_confidence = min(1.0, technical_confidence * 1.2)
                reason = f"Technical + Negative news ({avg_sentiment:+.2f})"
            elif avg_sentiment > 0.5:
                # Positive news contradicts sell
                adjusted_confidence = technical_confidence * 0.7
                reason = f"Technical but Positive news ({avg_sentiment:+.2f})"
        
        # Reduce confidence before major events
        if symbol_events:
            high_impact_events = [e for e in symbol_events if e.impact == 'high']
            if high_impact_events:
                adjusted_confidence *= 0.8
                reason += f" | Caution: {high_impact_events[0].event_type} soon"
        
        return adjusted_confidence, avg_sentiment, reason
    
    def get_trading_recommendation(self) -> Dict[str, Any]:
        """Get overall trading recommendation based on market conditions"""
        sentiment = self.get_market_sentiment()
        events = self.check_upcoming_events(7)
        
        recommendation = {
            'trade_allowed': True,
            'max_position_size_pct': 1.0,  # 100% of normal
            'cash_reserve_pct': 0.20,      # 20% minimum
            'reason': 'Normal conditions'
        }
        
        # High impact events this week
        high_impact = [e for e in events if e.impact == 'high']
        if len(high_impact) >= 2:
            recommendation['trade_allowed'] = False
            recommendation['reason'] = f"Multiple high-impact events: {len(high_impact)}"
        elif len(high_impact) == 1:
            recommendation['max_position_size_pct'] = 0.5
            recommendation['cash_reserve_pct'] = 0.30
            recommendation['reason'] = f"High-impact event: {high_impact[0].description}"
        
        # Extreme sentiment
        if sentiment['overall_sentiment'] > 0.7:
            recommendation['reason'] += " | Euphoria detected, be cautious"
        elif sentiment['overall_sentiment'] < -0.7:
            recommendation['reason'] += " | Fear detected, opportunities may arise"
        
        return recommendation

# Singleton instance
_analyzer = None

def get_analyzer() -> NewsAnalyzer:
    """Get or create NewsAnalyzer instance"""
    global _analyzer
    if _analyzer is None:
        _analyzer = NewsAnalyzer()
    return _analyzer
