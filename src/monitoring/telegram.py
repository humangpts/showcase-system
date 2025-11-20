"""
Telegram notification service for monitoring.
Handles sending alerts and reports to Telegram.
"""

import logging
import asyncio
import re
from typing import Optional, Dict, Any, List
from datetime import datetime
from enum import Enum

import httpx
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type
)

from .config import monitoring_config, AlertLevel


logger = logging.getLogger(__name__)


class MessageFormat(str, Enum):
    """Telegram message format types"""
    MARKDOWN = "Markdown"
    MARKDOWN_V2 = "MarkdownV2"
    HTML = "HTML"


def escape_markdown(text: str) -> str:
    """
    Escape special characters for Telegram Markdown.
    
    Telegram Markdown special characters: _ * [ ] ( ) ~ ` > # + - = | { } . !
    
    Args:
        text: Text to escape
        
    Returns:
        Escaped text safe for Markdown
    """
    # Characters that need escaping in Telegram Markdown
    special_chars = ['_', '*', '[', ']', '(', ')', '~', '`', '>', '#', '+', '-', '=', '|', '{', '}', '.', '!']
    
    for char in special_chars:
        text = text.replace(char, f'\\{char}')
    
    return text


class TelegramReporter:
    """
    Telegram bot client for sending monitoring alerts.
    Uses httpx for async HTTP requests.
    """
    
    def __init__(self):
        self.bot_token = monitoring_config.TELEGRAM_BOT_TOKEN
        self.chat_id = monitoring_config.TELEGRAM_CHAT_ID
        self.thread_id = monitoring_config.TELEGRAM_THREAD_ID
        self.base_url = f"https://api.telegram.org/bot{self.bot_token}"
        self.client: Optional[httpx.AsyncClient] = None
        
        # Rate limiting state
        self._rate_limit_lock = asyncio.Lock()
        self._last_send_time = 0.0
        self._min_interval = 0.1  # 100ms between messages
        
        # Emoji mapping for visual alerts
        self.emoji_map = {
            AlertLevel.CRITICAL: "🔴",
            AlertLevel.WARNING: "⚠️",
            AlertLevel.INFO: "ℹ️",
            "error": "❌",
            "success": "✅",
            "database": "🗄️",
            "redis": "📦",
            "disk": "💾",
            "memory": "🧠",
            "queue": "📋",
            "user": "👤",
            "project": "📁",
            "time": "⏰",
            "chart": "📊",
        }
    
    async def __aenter__(self):
        """Async context manager entry"""
        await self.connect()
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit"""
        await self.disconnect()
    
    async def connect(self):
        """Initialize HTTP client"""
        if not self.client:
            self.client = httpx.AsyncClient(timeout=10.0)
    
    async def disconnect(self):
        """Close HTTP client"""
        if self.client:
            await self.client.aclose()
            self.client = None
    
    async def _rate_limit_wait(self):
        """Wait to respect rate limits"""
        async with self._rate_limit_lock:
            now = asyncio.get_event_loop().time()
            time_since_last = now - self._last_send_time
            
            if time_since_last < self._min_interval:
                await asyncio.sleep(self._min_interval - time_since_last)
            
            self._last_send_time = asyncio.get_event_loop().time()
    
    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(min=1, max=10),
        retry=retry_if_exception_type(httpx.HTTPError)
    )
    async def send_message(
        self,
        text: str,
        level: AlertLevel = AlertLevel.INFO,
        parse_mode: MessageFormat = MessageFormat.MARKDOWN,
        disable_notification: bool = False,
        reply_markup: Optional[Dict[str, Any]] = None
    ) -> bool:
        """
        Send message to Telegram.
        
        Args:
            text: Message text (will be escaped if using Markdown)
            level: Alert level for emoji and formatting
            parse_mode: Telegram parse mode
            disable_notification: Silent notification
            reply_markup: Optional inline keyboard
            
        Returns:
            True if sent successfully
        """
        if not monitoring_config.is_enabled:
            logger.debug(f"Monitoring disabled, skipping message: {text[:100]}")
            return False
        
        # Ensure client is connected
        if not self.client:
            await self.connect()
        if not self.client:
            logger.error("TelegramReporter HTTP client is not initialized.")
            return False
        
        # Rate limiting
        await self._rate_limit_wait()
        
        # Truncate if too long
        if len(text) > monitoring_config.ALERT_MAX_MESSAGE_LENGTH:
            text = text[:monitoring_config.ALERT_MAX_MESSAGE_LENGTH - 100]
            text += "\n\n... *[Message truncated]*"
        
        # Prepare payload
        payload = {
            "chat_id": self.chat_id,
            "text": text,
            "parse_mode": parse_mode.value,
            "disable_notification": disable_notification,
        }
        
        # Add thread_id if configured (for topic groups)
        if self.thread_id:
            payload["message_thread_id"] = self.thread_id
        
        # Add reply markup if provided
        if reply_markup:
            payload["reply_markup"] = reply_markup
        
        try:
            response = await self.client.post(
                f"{self.base_url}/sendMessage",
                json=payload
            )
            response.raise_for_status()
            
            result = response.json()
            if not result.get("ok"):
                logger.error(f"Telegram API error: {result}")
                return False
            
            return True
            
        except httpx.HTTPStatusError as e:
            # Check for rate limiting
            if e.response.status_code == 429:
                retry_after = int(e.response.headers.get('Retry-After', 60))
                logger.warning(f"Telegram rate limit hit, retry after {retry_after}s")
                await asyncio.sleep(retry_after)
                raise  # Will be retried
            
            logger.error(f"HTTP error sending Telegram message: {e}")
            # Don't retry for client errors
            if e.response.status_code < 500:
                return False
            raise  # Will be retried for 5xx
            
        except httpx.HTTPError as e:
            logger.error(f"Failed to send Telegram message: {e}")
            raise  # Will be retried
        except Exception as e:
            logger.error(f"Unexpected error sending Telegram message: {e}")
            return False
    
    async def send_alert(
        self,
        title: str,
        message: str,
        level: AlertLevel = AlertLevel.WARNING,
        details: Optional[Dict[str, Any]] = None,
        error: Optional[Exception] = None,
        traceback_str: Optional[str] = None
    ) -> bool:
        """
        Send formatted alert message.
        
        Args:
            title: Alert title
            message: Main message
            level: Alert severity
            details: Additional details dict
            error: Optional exception object
            traceback_str: Optional traceback string
            
        Returns:
            True if sent successfully
        """
        # Build formatted message
        emoji = self.emoji_map.get(level, "📢")
        
        lines = [
            f"{emoji} *{escape_markdown(title)}*",
            f"_{escape_markdown(monitoring_config.MONITORING_ENV.upper())}_",
            "",
            escape_markdown(message),
        ]
        
        # Add details if provided
        if details:
            lines.append("\n*Details:*")
            for key, value in details.items():
                # Escape key and value separately
                safe_key = escape_markdown(str(key))
                safe_value = escape_markdown(str(value))
                lines.append(f"• {safe_key}: `{safe_value}`")
        
        # Add error info if provided
        if error:
            error_type = escape_markdown(type(error).__name__)
            error_msg = escape_markdown(str(error)[:500])  # Limit error message length
            lines.append(f"\n*Error:* `{error_type}: {error_msg}`")
        
        # Add traceback if provided
        if traceback_str:
            # Truncate traceback if needed
            max_lines = monitoring_config.ALERT_MAX_TRACEBACK_LINES
            if max_lines > 0:
                tb_lines = traceback_str.split("\n")[:max_lines]
                tb_text = "\n".join(tb_lines)
                
                lines.append("\n*Traceback:*")
                # Use code block - no escaping needed inside ```
                lines.append(f"```\n{tb_text}\n```")
        
        # Add timestamp
        timestamp = datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S UTC')
        lines.append(f"\n{self.emoji_map['time']} _{escape_markdown(timestamp)}_")
        
        # Send message
        full_text = "\n".join(lines)
        
        # Disable notification for INFO level
        disable_notification = level == AlertLevel.INFO
        
        return await self.send_message(
            text=full_text,
            level=level,
            disable_notification=disable_notification
        )
    
    async def send_health_alert(
        self,
        components: Dict[str, bool],
        errors: List[str]
    ) -> bool:
        """
        Send system health alert.
        
        Args:
            components: Dict of component_name -> is_healthy
            errors: List of error messages
            
        Returns:
            True if sent successfully
        """
        # Determine overall level
        level = AlertLevel.CRITICAL if any(not h for h in components.values()) else AlertLevel.WARNING
        
        # Build status lines
        status_lines = []
        for component, is_healthy in components.items():
            emoji = "✅" if is_healthy else "❌"
            icon = self.emoji_map.get(component.lower(), "")
            status = "OK" if is_healthy else "FAILED"
            status_lines.append(f"{emoji} {icon} {escape_markdown(component)}: {status}")
        
        # Build message
        message = "\n".join(status_lines)
        
        if errors:
            message += "\n\n*Errors:*\n"
            message += "\n".join(f"• {escape_markdown(e)}" for e in errors)
        
        return await self.send_alert(
            title="System Health Check",
            message=message,
            level=level
        )
    
    async def send_daily_report(
        self,
        stats: Dict[str, Any]
    ) -> bool:
        """
        Send daily statistics report.
        
        Args:
            stats: Dictionary with daily statistics
            
        Returns:
            True if sent successfully
        """
        lines = [
            f"{self.emoji_map['chart']} *Daily Report*",
            f"_{escape_markdown(monitoring_config.MONITORING_ENV.upper())}_",
            f"_Date: {escape_markdown(datetime.utcnow().strftime('%Y-%m-%d'))}_",
            "",
        ]
        
        # User stats
        if "users" in stats:
            lines.append(f"{self.emoji_map['user']} *Users*")
            lines.append(f"• New: {stats['users'].get('new', 0)}")
            lines.append(f"• Active: {stats['users'].get('active', 0)}")
            lines.append(f"• Total: {stats['users'].get('total', 0)}")
            lines.append("")
        
        # Project stats
        if "projects" in stats:
            lines.append(f"{self.emoji_map['project']} *Projects*")
            lines.append(f"• Created: {stats['projects'].get('created', 0)}")
            lines.append(f"• Updated: {stats['projects'].get('updated', 0)}")
            lines.append(f"• Total: {stats['projects'].get('total', 0)}")
            lines.append("")
        
        # Error stats
        if "errors" in stats:
            lines.append(f"{self.emoji_map['error']} *Errors*")
            lines.append(f"• Total: {stats['errors'].get('total', 0)}")
            if stats['errors'].get('by_type'):
                lines.append("• By type:")
                for error_type, count in stats['errors']['by_type'].items():
                    safe_type = escape_markdown(error_type)
                    lines.append(f"  \\- {safe_type}: {count}")
            lines.append("")
        
        # System stats
        if "system" in stats:
            lines.append("*System*")
            uptime = escape_markdown(str(stats['system'].get('uptime', 'N/A')))
            lines.append(f"• Uptime: {uptime}")
            lines.append(f"• Disk usage: {stats['system'].get('disk_usage', 'N/A')}%")
            lines.append(f"• Memory usage: {stats['system'].get('memory_usage', 'N/A')}%")
        
        full_text = "\n".join(lines)
        
        return await self.send_message(
            text=full_text,
            level=AlertLevel.INFO,
            disable_notification=True
        )


# Global singleton instance
telegram_reporter = TelegramReporter()


# Convenience functions
async def send_critical_alert(title: str, message: str, **kwargs):
    """Send critical alert"""
    return await telegram_reporter.send_alert(
        title=title,
        message=message,
        level=AlertLevel.CRITICAL,
        **kwargs
    )


async def send_warning_alert(title: str, message: str, **kwargs):
    """Send warning alert"""
    return await telegram_reporter.send_alert(
        title=title,
        message=message,
        level=AlertLevel.WARNING,
        **kwargs
    )


async def send_info_message(text: str):
    """Send informational message"""
    return await telegram_reporter.send_message(
        text=text,
        level=AlertLevel.INFO,
        disable_notification=True
    )