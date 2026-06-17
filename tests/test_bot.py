#!/usr/bin/env python3
"""
Test Trading Bot Components
Verify all modules work correctly
"""

import os
import sys
import asyncio

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.position_sizer import KellyPositionSizer
from risk.manager import RiskManager, RiskConfig
from strategies.base import Direction, Signal

def test_kelly_sizer():
    """Test Kelly position sizer"""
    print("\n🧪 Testing Kelly Position Sizer...")

    sizer = KellyPositionSizer(fractional_kelly=0.25)

    # Test calculation
    portfolio_value = 100000
    confidence = 0.8

    position_size = sizer.calculate(
        portfolio_value=portfolio_value,
        confidence=confidence,
        win_rate=0.55,
        avg_win=0.04,
        avg_loss=0.02
    )

    print(f"  Portfolio: ${portfolio_value:,.2f}")
    print(f"  Confidence: {confidence:.0%}")
    print(f"  Position size: ${position_size:,.2f}")
    print(f"  Position pct: {position_size/portfolio_value:.2%}")

    # Test shares calculation
    shares = sizer.calculate_shares(
        portfolio_value=portfolio_value,
        current_price=150.0,
        confidence=confidence
    )
    print(f"  Shares at $150: {shares}")

    # Test stats
    stats = sizer.get_kelly_stats()
    print(f"  Full Kelly: {stats['full_kelly']:.2%}")
    print(f"  Quarter Kelly: {stats['quarter_kelly']:.2%}")

    print("  ✅ Kelly sizer test passed")
    return True

def test_risk_manager():
    """Test advanced risk manager"""
    print("\n🧪 Testing Risk Manager (advanced)...")

    rm = RiskManager(RiskConfig(
        max_position_pct=0.05,
        max_simultaneous_positions=5,
        max_sector_positions=3,
        max_daily_loss_pct=0.02,
        max_portfolio_drawdown_pct=0.10,
    ))

    # Test check_trade with no existing positions — should pass
    signal = Signal(symbol="AAPL", direction=Direction.LONG, strength=0.7, strategy_name="test")
    allowed, reason = rm.check_trade(signal, 100000, {})
    assert allowed, f"Expected trade allowed, got: {reason}"
    print("  check_trade (empty positions): OK")

    # Test ATR-based stop-loss calculation
    stop_loss, take_profit = rm.calculate_stops(100.0, Direction.LONG, 2.0)
    assert stop_loss == 96.0, f"Expected SL=96.0, got {stop_loss}"
    assert take_profit == 108.0, f"Expected TP=108.0, got {take_profit}"
    print(f"  ATR stops: SL=${stop_loss}, TP=${take_profit}")

    # Test Kelly sizing
    size = rm.calculate_size(100000, win_rate=0.55, avg_win=0.04, avg_loss=0.02)
    assert 0 < size <= 5000, f"Kelly size ${size} out of expected range"
    print(f"  Kelly sizing: ${size:,.2f}")

    print("  ✅ Risk manager test passed")
    return True

def test_alpaca_connection():
    """Test Alpaca API connection"""
    print("\n🧪 Testing Alpaca API Connection...")

    try:
        from core.alpaca_client import AlpacaClient

        client = AlpacaClient()
        print("  Client initialized")

        # Test account access
        account = client.get_account()
        print(f"  Account ID: {account.get('id', 'N/A')}")
        print(f"  Portfolio value: ${float(account.get('portfolio_value', 0)):,.2f}")
        print(f"  Cash: ${float(account.get('cash', 0)):,.2f}")
        print(f"  Buying power: ${float(account.get('buying_power', 0)):,.2f}")

        # Test market status
        is_open = client.is_market_open()
        print(f"  Market open: {is_open}")

        print("  ✅ Alpaca connection test passed")
        return True

    except Exception as e:
        print(f"  ❌ Alpaca connection failed: {e}")
        return False

async def main():
    """Run all tests"""
    print("=" * 60)
    print("🤖 Trading Bot Test Suite")
    print("=" * 60)

    results = []

    # Test Kelly sizer
    try:
        results.append(test_kelly_sizer())
    except Exception as e:
        print(f"  ❌ Kelly sizer test failed: {e}")
        results.append(False)

    # Test risk manager
    try:
        results.append(test_risk_manager())
    except Exception as e:
        print(f"  ❌ Risk manager test failed: {e}")
        results.append(False)

    # Test Alpaca connection
    try:
        results.append(test_alpaca_connection())
    except Exception as e:
        print(f"  ❌ Alpaca connection test failed: {e}")
        results.append(False)

    # Summary
    print("\n" + "=" * 60)
    passed = sum(results)
    total = len(results)
    print(f"📊 Test Results: {passed}/{total} passed")

    if passed == total:
        print("🎉 All tests passed! Trading bot is ready.")
        return 0
    else:
        print("⚠️  Some tests failed. Check configuration.")
        return 1

if __name__ == '__main__':
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
