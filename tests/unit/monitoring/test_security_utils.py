"""
Tests for security utilities.
"""

import pytest
from monitoring.security_utils import (
    sanitize_headers,
    sanitize_string,
    sanitize_dict,
    sanitize_traceback,
    sanitize_url,
)


class TestSanitizeHeaders:
    """Tests for header sanitization"""
    
    def test_removes_authorization_header(self):
        """Test that Authorization header is removed"""
        headers = {
            "Authorization": "Bearer token123",
            "User-Agent": "Mozilla/5.0",
        }
        result = sanitize_headers(headers)
        
        assert "Authorization" not in result
        assert "User-Agent" in result
    
    def test_removes_cookie_header(self):
        """Test that Cookie header is removed"""
        headers = {
            "Cookie": "session=abc123",
            "Content-Type": "application/json",
        }
        result = sanitize_headers(headers)
        
        assert "Cookie" not in result
        assert "Content-Type" in result
    
    def test_removes_api_key_headers(self):
        """Test that API key headers are removed"""
        headers = {
            "X-API-Key": "secret123",
            "X-Auth-Token": "token456",
            "Accept": "application/json",
        }
        result = sanitize_headers(headers)
        
        assert "X-API-Key" not in result
        assert "X-Auth-Token" not in result
        assert "Accept" in result
    
    def test_case_insensitive(self):
        """Test that header matching is case insensitive"""
        headers = {
            "authorization": "Bearer token",
            "COOKIE": "session=abc",
            "User-Agent": "Mozilla",
        }
        result = sanitize_headers(headers)
        
        assert "authorization" not in result
        assert "COOKIE" not in result
        assert "User-Agent" in result
    
    def test_empty_headers(self):
        """Test handling of empty headers"""
        assert sanitize_headers({}) == {}
        assert sanitize_headers(None) == {}
    
    def test_preserves_safe_headers(self):
        """Test that safe headers are preserved"""
        headers = {
            "User-Agent": "Mozilla/5.0",
            "Content-Type": "application/json",
            "Accept": "text/html",
            "X-Request-ID": "req123",
        }
        result = sanitize_headers(headers)
        
        assert result == headers


class TestSanitizeString:
    """Tests for string sanitization"""
    
    def test_sanitizes_password(self):
        """Test that passwords are sanitized"""
        text = 'Database error with password="secret123" in connection'
        result = sanitize_string(text)
        
        assert "secret123" not in result
        assert "password=***" in result
    
    def test_sanitizes_token(self):
        """Test that tokens are sanitized"""
        text = "API call failed with token=abc123xyz"
        result = sanitize_string(text)
        
        assert "abc123xyz" not in result
        assert "token=***" in result
    
    def test_sanitizes_db_connection_string(self):
        """Test that database credentials are sanitized"""
        text = "Connection failed: postgresql://user:pass123@localhost/db"
        result = sanitize_string(text)
        
        assert "user" not in result
        assert "pass123" not in result
        assert "postgresql://***:***@localhost/db" in result
    
    def test_sanitizes_aws_keys(self):
        """Test that AWS keys are sanitized"""
        text = "AWS error with AKIAIOSFODNN7EXAMPLE key"
        result = sanitize_string(text)
        
        assert "AKIAIOSFODNN7EXAMPLE" not in result
        assert "AKIA***" in result
    
    def test_truncates_long_strings(self):
        """Test that long strings are truncated"""
        text = "x" * 1000
        result = sanitize_string(text, max_length=100)
        
        assert len(result) <= 103  # 100 + "..."
        assert result.endswith("...")
    
    def test_handles_none(self):
        """Test handling of None"""
        assert sanitize_string(None) is None
        assert sanitize_string("") == ""


class TestSanitizeDict:
    """Tests for dictionary sanitization"""
    
    def test_sanitizes_password_key(self):
        """Test that password keys are sanitized"""
        data = {
            "username": "john",
            "password": "secret123",
            "email": "john@example.com",
        }
        result = sanitize_dict(data)
        
        assert result["username"] == "john"
        assert result["password"] == "***"
        assert result["email"] == "john@example.com"
    
    def test_sanitizes_nested_dicts(self):
        """Test sanitization of nested dictionaries"""
        data = {
            "user": {
                "name": "john",
                "credentials": {
                    "password": "secret",
                    "api_key": "key123",
                }
            }
        }
        result = sanitize_dict(data)
        
        assert result["user"]["name"] == "john"
        assert result["user"]["credentials"]["password"] == "***"
        assert result["user"]["credentials"]["api_key"] == "***"
    
    def test_respects_max_depth(self):
        """Test that max depth is respected"""
        data = {
            "level1": {
                "level2": {
                    "level3": {
                        "level4": "too deep"
                    }
                }
            }
        }
        result = sanitize_dict(data, max_depth=2)
        
        assert "level1" in result
        assert "level2" in result["level1"]
        assert result["level1"]["level2"] == {"...": "max depth reached"}
    
    def test_sanitizes_lists(self):
        """Test sanitization of list values"""
        data = {
            "tokens": ["token1", "token2"],
            "users": ["alice", "bob"],
        }
        result = sanitize_dict(data)
        
        # 'tokens' key should be sanitized
        assert result["tokens"] == "***"
        # 'users' should be preserved
        assert result["users"] == ["alice", "bob"]


class TestSanitizeTraceback:
    """Tests for traceback sanitization"""
    
    def test_limits_traceback_lines(self):
        """Test that traceback is limited to max lines"""
        traceback = "\n".join([f"Line {i}" for i in range(100)])
        result = sanitize_traceback(traceback, max_lines=10)
        
        lines = result.split('\n')
        assert len(lines) <= 11  # 10 + "(truncated)"
        assert "... (truncated)" in lines[-1]
    
    def test_sanitizes_sensitive_data_in_traceback(self):
        """Test that sensitive data is removed from traceback"""
        traceback = '''Traceback (most recent call last):
  File "main.py", line 42, in connect
    conn = psycopg2.connect("postgresql://user:pass123@localhost/db")
  File "psycopg2.py", line 10, in connect
    raise DatabaseError("Connection failed")
DatabaseError: Connection failed
'''
        result = sanitize_traceback(traceback)
        
        assert "user" not in result or "pass123" not in result
        assert "postgresql://***:***@" in result
    
    def test_handles_empty_traceback(self):
        """Test handling of empty traceback"""
        assert sanitize_traceback("") == ""
        assert sanitize_traceback(None) == ""


class TestSanitizeUrl:
    """Tests for URL sanitization"""
    
    def test_sanitizes_token_parameter(self):
        """Test that token parameters are sanitized"""
        url = "/api/users?token=abc123&id=5"
        result = sanitize_url(url)
        
        assert "abc123" not in result
        assert "token=***" in result
        assert "id=5" in result
    
    def test_sanitizes_api_key_parameter(self):
        """Test that API key parameters are sanitized"""
        url = "/api/data?api_key=secret&limit=10"
        result = sanitize_url(url)
        
        assert "secret" not in result
        assert "api_key=***" in result
        assert "limit=10" in result
    
    def test_preserves_safe_parameters(self):
        """Test that safe parameters are preserved"""
        url = "/api/users?page=1&limit=20&sort=name"
        result = sanitize_url(url)
        
        assert result == url
    
    def test_handles_url_without_query(self):
        """Test URLs without query parameters"""
        url = "/api/users"
        result = sanitize_url(url)
        
        assert result == url
    
    def test_handles_empty_url(self):
        """Test handling of empty URL"""
        assert sanitize_url("") == ""
        assert sanitize_url(None) is None


class TestIntegration:
    """Integration tests combining multiple sanitization functions"""
    
    def test_full_request_sanitization(self):
        """Test sanitizing a complete request info dict"""
        request_info = {
            "method": "POST",
            "path": "/api/users?token=abc123",
            "headers": {
                "Authorization": "Bearer secret",
                "User-Agent": "Mozilla/5.0",
                "Cookie": "session=xyz",
            },
            "body": {
                "username": "john",
                "password": "secret123",
            }
        }
        
        # Sanitize each component
        sanitized = {
            "method": request_info["method"],
            "path": sanitize_url(request_info["path"]),
            "headers": sanitize_headers(request_info["headers"]),
            "body": sanitize_dict(request_info["body"]),
        }
        
        # Verify sanitization
        assert "token=***" in sanitized["path"]
        assert "Authorization" not in sanitized["headers"]
        assert "Cookie" not in sanitized["headers"]
        assert "User-Agent" in sanitized["headers"]
        assert sanitized["body"]["password"] == "***"
        assert sanitized["body"]["username"] == "john"