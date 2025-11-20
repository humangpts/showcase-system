"""
Tests for Telegram reporter.
"""

import pytest
from unittest.mock import AsyncMock, Mock, patch
import httpx

from monitoring.telegram import TelegramReporter, MessageFormat, telegram_reporter
from monitoring.config import AlertLevel, monitoring_config


@pytest.fixture
def reporter():
    """Create test reporter"""
    original_token = monitoring_config.TELEGRAM_BOT_TOKEN
    original_chat = monitoring_config.TELEGRAM_CHAT_ID
    
    monitoring_config.TELEGRAM_BOT_TOKEN = "test_token_123"
    monitoring_config.TELEGRAM_CHAT_ID = "test_chat_456"
    
    reporter = TelegramReporter()
    
    yield reporter
    
    # Restore
    monitoring_config.TELEGRAM_BOT_TOKEN = original_token
    monitoring_config.TELEGRAM_CHAT_ID = original_chat


@pytest.mark.asyncio
async def test_reporter_initialization(reporter):
    """Test reporter initializes correctly"""
    assert reporter.bot_token == "test_token_123"
    assert reporter.chat_id == "test_chat_456"
    assert reporter.base_url == "https://api.telegram.org/bottest_token_123"


@pytest.mark.asyncio
async def test_connect_disconnect(reporter):
    """Test client connection management"""
    assert reporter.client is None
    
    await reporter.connect()
    assert reporter.client is not None
    
    await reporter.disconnect()
    assert reporter.client is None


@pytest.mark.asyncio
async def test_send_message_success(reporter):
    """Test successful message sending"""
    mock_response = Mock()
    mock_response.json.return_value = {"ok": True, "result": {"message_id": 123}}
    mock_response.raise_for_status = Mock()
    
    mock_client = AsyncMock()
    mock_client.post.return_value = mock_response
    
    reporter.client = mock_client
    
    result = await reporter.send_message(
        text="Test message",
        level=AlertLevel.INFO
    )
    
    assert result is True
    mock_client.post.assert_called_once()
    
    # Check payload
    call_args = mock_client.post.call_args
    payload = call_args[1]['json']
    
    assert payload['chat_id'] == "test_chat_456"
    assert payload['text'] == "Test message"
    assert payload['parse_mode'] == "Markdown"


@pytest.mark.asyncio
async def test_send_message_auto_connect(reporter):
    """Test that send_message auto-connects if needed"""
    with patch.object(reporter, 'connect', new_callable=AsyncMock) as mock_connect:
        mock_response = Mock()
        mock_response.json.return_value = {"ok": True}
        mock_response.raise_for_status = Mock()
        
        mock_client = AsyncMock()
        mock_client.post.return_value = mock_response
        
        with patch('httpx.AsyncClient', return_value=mock_client):
            result = await reporter.send_message("Test")
            
            # Should have called connect
            mock_connect.assert_called_once()


@pytest.mark.asyncio
async def test_send_message_truncation(reporter):
    """Test long messages are truncated"""
    long_text = "x" * 5000  # Longer than max length
    
    mock_response = Mock()
    mock_response.json.return_value = {"ok": True}
    mock_response.raise_for_status = Mock()
    
    mock_client = AsyncMock()
    mock_client.post.return_value = mock_response
    reporter.client = mock_client
    
    await reporter.send_message(long_text)
    
    call_args = mock_client.post.call_args
    sent_text = call_args[1]['json']['text']
    
    assert len(sent_text) <= monitoring_config.ALERT_MAX_MESSAGE_LENGTH
    assert "[Message truncated]" in sent_text


@pytest.mark.asyncio
async def test_send_message_disabled_monitoring(reporter):
    """Test message not sent when monitoring disabled"""
    monitoring_config.MONITORING_ENABLED = False
    
    result = await reporter.send_message("Test")
    
    assert result is False
    
    # Restore
    monitoring_config.MONITORING_ENABLED = True


@pytest.mark.asyncio
async def test_send_alert_basic(reporter):
    """Test send_alert basic functionality"""
    mock_response = Mock()
    mock_response.json.return_value = {"ok": True}
    mock_response.raise_for_status = Mock()
    
    mock_client = AsyncMock()
    mock_client.post.return_value = mock_response
    reporter.client = mock_client
    
    result = await reporter.send_alert(
        title="Test Alert",
        message="Something happened",
        level=AlertLevel.WARNING
    )
    
    assert result is True
    
    # Check message contains expected parts
    call_args = mock_client.post.call_args
    sent_text = call_args[1]['json']['text']
    
    assert "Test Alert" in sent_text
    assert "Something happened" in sent_text
    assert "⚠️" in sent_text  # Warning emoji


@pytest.mark.asyncio
async def test_send_alert_with_details(reporter):
    """Test send_alert with details dict"""
    mock_response = Mock()
    mock_response.json.return_value = {"ok": True}
    mock_response.raise_for_status = Mock()
    
    mock_client = AsyncMock()
    mock_client.post.return_value = mock_response
    reporter.client = mock_client
    
    result = await reporter.send_alert(
        title="Error",
        message="API failed",
        level=AlertLevel.CRITICAL,
        details={
            "Endpoint": "/api/test",
            "Status": 500,
            "User": "test@example.com"
        }
    )
    
    assert result is True
    
    call_args = mock_client.post.call_args
    sent_text = call_args[1]['json']['text']
    
    assert "Endpoint" in sent_text
    assert "/api/test" in sent_text
    assert "Status" in sent_text


@pytest.mark.asyncio
async def test_send_alert_with_exception(reporter):
    """Test send_alert with exception"""
    mock_response = Mock()
    mock_response.json.return_value = {"ok": True}
    mock_response.raise_for_status = Mock()
    
    mock_client = AsyncMock()
    mock_client.post.return_value = mock_response
    reporter.client = mock_client
    
    try:
        raise ValueError("Test error")
    except Exception as e:
        result = await reporter.send_alert(
            title="Exception",
            message="An error occurred",
            level=AlertLevel.CRITICAL,
            error=e
        )
    
    assert result is True
    
    call_args = mock_client.post.call_args
    sent_text = call_args[1]['json']['text']
    
    assert "ValueError" in sent_text
    assert "Test error" in sent_text


@pytest.mark.asyncio
async def test_send_health_alert(reporter):
    """Test send_health_alert"""
    mock_response = Mock()
    mock_response.json.return_value = {"ok": True}
    mock_response.raise_for_status = Mock()
    
    mock_client = AsyncMock()
    mock_client.post.return_value = mock_response
    reporter.client = mock_client
    
    components = {
        "Database": True,
        "Redis": False,
        "Queue": True
    }
    errors = ["Redis connection failed"]
    
    result = await reporter.send_health_alert(components, errors)
    
    assert result is True
    
    call_args = mock_client.post.call_args
    sent_text = call_args[1]['json']['text']
    
    assert "Database" in sent_text
    assert "Redis" in sent_text
    assert "✅" in sent_text
    assert "❌" in sent_text


@pytest.mark.asyncio
async def test_send_daily_report(reporter):
    """Test send_daily_report"""
    mock_response = Mock()
    mock_response.json.return_value = {"ok": True}
    mock_response.raise_for_status = Mock()
    
    mock_client = AsyncMock()
    mock_client.post.return_value = mock_response
    reporter.client = mock_client
    
    stats = {
        "users": {
            "new": 10,
            "active": 50,
            "total": 100
        },
        "projects": {
            "created": 5,
            "updated": 20,
            "total": 50
        },
        "errors": {
            "total": 3,
            "by_type": {
                "ValueError": 2,
                "KeyError": 1
            }
        }
    }
    
    result = await reporter.send_daily_report(stats)
    
    assert result is True
    
    call_args = mock_client.post.call_args
    sent_text = call_args[1]['json']['text']
    
    assert "Daily Report" in sent_text
    assert "Users" in sent_text
    assert "Projects" in sent_text
    assert "Errors" in sent_text


@pytest.mark.asyncio
async def test_http_error_handling(reporter):
    """Test handling of HTTP errors"""
    mock_client = AsyncMock()
    mock_client.post.side_effect = httpx.HTTPError("Connection failed")
    reporter.client = mock_client
    
    with pytest.raises(httpx.HTTPError):
        await reporter.send_message("Test")


@pytest.mark.asyncio
async def test_thread_id_support(reporter):
    """Test thread_id is included when configured"""
    monitoring_config.TELEGRAM_THREAD_ID = 123
    reporter = TelegramReporter()
    
    mock_response = Mock()
    mock_response.json.return_value = {"ok": True}
    mock_response.raise_for_status = Mock()
    
    mock_client = AsyncMock()
    mock_client.post.return_value = mock_response
    reporter.client = mock_client
    
    await reporter.send_message("Test")
    
    call_args = mock_client.post.call_args
    payload = call_args[1]['json']
    
    assert payload['message_thread_id'] == 123
    
    # Restore
    monitoring_config.TELEGRAM_THREAD_ID = None


@pytest.mark.asyncio
async def test_disable_notification(reporter):
    """Test disable_notification parameter"""
    mock_response = Mock()
    mock_response.json.return_value = {"ok": True}
    mock_response.raise_for_status = Mock()
    
    mock_client = AsyncMock()
    mock_client.post.return_value = mock_response
    reporter.client = mock_client
    
    await reporter.send_message("Test", disable_notification=True)
    
    call_args = mock_client.post.call_args
    payload = call_args[1]['json']
    
    assert payload['disable_notification'] is True


def test_emoji_map(reporter):
    """Test emoji mapping exists"""
    assert AlertLevel.CRITICAL in reporter.emoji_map
    assert AlertLevel.WARNING in reporter.emoji_map
    assert AlertLevel.INFO in reporter.emoji_map
    assert "error" in reporter.emoji_map
    assert "success" in reporter.emoji_map