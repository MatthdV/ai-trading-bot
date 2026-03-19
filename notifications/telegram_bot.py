#!/usr/bin/env python3
"""
Telegram Notifier
Send trading alerts and updates via Telegram
"""

import os
import logging
from typing import Optional

logger = logging.getLogger(__name__)

class TelegramNotifier:
    """Send notifications to Telegram"""
    
    def __init__(self):
        # Get Telegram bot token from environment
        self.bot_token = os.getenv('TELEGRAM_BOT_TOKEN')
        self.chat_id = os.getenv('TELEGRAM_CHAT_ID', '7912122801')
        
        if not self.bot_token:
            logger.warning("Telegram bot token not found, notifications disabled")
        else:
            logger.info("Telegram notifier initialized")
    
    async def send_message(self, message: str) -> bool:
        """Send message to Telegram"""
        if not self.bot_token:
            logger.debug(f"Would send: {message}")
            return False
        
        try:
            import aiohttp
            
            url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
            payload = {
                'chat_id': self.chat_id,
                'text': message,
                'parse_mode': 'HTML'
            }
            
            async with aiohttp.ClientSession() as session:
                async with session.post(url, json=payload) as response:
                    if response.status == 200:
                        logger.info("Telegram message sent")
                        return True
                    else:
                        logger.error(f"Failed to send Telegram message: {response.status}")
                        return False
                        
        except Exception as e:
            logger.error(f"Error sending Telegram message: {e}")
            return False
    
    async def send_trade_alert(self, symbol: str, action: str, qty: float, price: float, pnl: Optional[float] = None):
        """Send trade execution alert"""
        emoji = "🟢" if action == "buy" else "🔴"
        message = f"{emoji} <b>{action.upper()} {symbol}</b>\n"
        message += f"Qty: {qty:.2f}\n"
        message += f"Price: ${price:.2f}"
        
        if pnl is not None:
            pnl_emoji = "✅" if pnl > 0 else "❌"
            message += f"\nP&L: {pnl_emoji} {pnl:+.2f}%"
        
        await self.send_message(message)
    
    async def send_daily_summary(self, portfolio_value: float, daily_pnl: float, positions: list):
        """Send daily portfolio summary"""
        emoji = "📈" if daily_pnl >= 0 else "📉"
        message = f"{emoji} <b>Daily Summary</b>\n\n"
        message += f"Portfolio: ${portfolio_value:,.2f}\n"
        message += f"Daily P&L: {daily_pnl:+.2f}%\n"
        message += f"Open positions: {len(positions)}"
        
        if positions:
            message += "\n\n<b>Positions:</b>"
            for pos in positions[:5]:  # Show top 5
                message += f"\n• {pos['symbol']}: {pos['qty']} @ ${pos['avg_entry']:.2f}"
        
        await self.send_message(message)
    
    async def send_risk_alert(self, message: str):
        """Send risk management alert"""
        await self.send_message(f"⚠️ <b>RISK ALERT</b>\n\n{message}")
