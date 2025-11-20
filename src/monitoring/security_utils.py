"""
Security utilities for monitoring module.
Handles sanitization of sensitive data before sending alerts.
"""

import re
from typing import Dict, Any, Optional


# Headers that should never be sent in alerts
SENSITIVE_HEADERS = {
    'authorization',
    'cookie',
    'set-cookie',
    'x-api-key',
    'x-auth-token',
    'api-key',
    'token',
    'apikey',
    'session',
    'x-session-id',
    'x-csrf-token',
    'proxy-authorization',
}

# Patterns that might contain sensitive data
SENSITIVE_PATTERNS = [
    (re.compile(r'password["\s:=]+[\w\-\.]+', re.IGNORECASE), 'password=***'),
    (re.compile(r'token["\s:=]+[\w\-\.]+', re.IGNORECASE), 'token=***'),
    (re.compile(r'key["\s:=]+[\w\-\.]+', re.IGNORECASE), 'key=***'),
    (re.compile(r'secret["\s:=]+[\w\-\.]+', re.IGNORECASE), 'secret=***'),
    (re.compile(r'api[_-]?key["\s:=]+[\w\-\.]+', re.IGNORECASE), 'api_key=***'),
    # Database connection strings
    (re.compile(r'postgresql://[^:]+:[^@]+@', re.IGNORECASE), 'postgresql://***:***@'),
    (re.compile(r'mysql://[^:]+:[^@]+@', re.IGNORECASE), 'mysql://***:***@'),
    (re.compile(r'mongodb://[^:]+:[^@]+@', re.IGNORECASE), 'mongodb://***:***@'),
    (re.compile(r'redis://[^:]+:[^@]+@', re.IGNORECASE), 'redis://***:***@'),
    # AWS keys
    (re.compile(r'AKIA[0-9A-Z]{16}', re.IGNORECASE), 'AKIA***'),
    (re.compile(r'aws_secret_access_key["\s:=]+[\w/+]+', re.IGNORECASE), 'aws_secret_access_key=***'),
]


def sanitize_headers(headers: Dict[str, Any]) -> Dict[str, str]:
    """
    Remove sensitive headers from request headers dict.
    
    Args:
        headers: Dictionary of HTTP headers
        
    Returns:
        Sanitized headers dictionary with sensitive headers removed
        
    Example:
        >>> headers = {
        ...     "Authorization": "Bearer token123",
        ...     "User-Agent": "Mozilla/5.0",
        ...     "Cookie": "session=abc"
        ... }
        >>> sanitized = sanitize_headers(headers)
        >>> sanitized
        {'User-Agent': 'Mozilla/5.0'}
    """
    if not headers:
        return {}
    
    safe_headers = {}
    
    for key, value in headers.items():
        # Check if header name is sensitive
        if key.lower() in SENSITIVE_HEADERS:
            continue
            
        # Also check if key contains sensitive keywords
        key_lower = key.lower()
        if any(sensitive in key_lower for sensitive in ['auth', 'token', 'key', 'secret', 'password']):
            continue
        
        # Include safe headers
        safe_headers[key] = str(value)
    
    return safe_headers


def sanitize_string(text: str, max_length: Optional[int] = None) -> str:
    """
    Remove sensitive patterns from string.
    
    Args:
        text: String that might contain sensitive data
        max_length: Optional maximum length to truncate to
        
    Returns:
        Sanitized string with sensitive patterns replaced
        
    Example:
        >>> text = "Connection failed: postgresql://user:pass123@localhost/db"
        >>> sanitize_string(text)
        'Connection failed: postgresql://***:***@localhost/db'
    """
    if not text:
        return text
    
    sanitized = text
    
    # Apply all sensitive patterns
    for pattern, replacement in SENSITIVE_PATTERNS:
        sanitized = pattern.sub(replacement, sanitized)
    
    # Truncate if needed
    if max_length and len(sanitized) > max_length:
        sanitized = sanitized[:max_length] + "..."
    
    return sanitized


def sanitize_dict(data: Dict[str, Any], max_depth: int = 3) -> Dict[str, Any]:
    """
    Recursively sanitize dictionary, removing sensitive values.
    
    Args:
        data: Dictionary to sanitize
        max_depth: Maximum recursion depth
        
    Returns:
        Sanitized dictionary
        
    Example:
        >>> data = {
        ...     "user": "john",
        ...     "password": "secret123",
        ...     "api_key": "key123"
        ... }
        >>> sanitize_dict(data)
        {'user': 'john', 'password': '***', 'api_key': '***'}
    """
    if max_depth <= 0:
        return {"...": "max depth reached"}
    
    if not isinstance(data, dict):
        return data
    
    sanitized = {}
    
    for key, value in data.items():
        key_lower = key.lower()
        
        # Check if key is sensitive
        if any(sensitive in key_lower for sensitive in 
               ['password', 'secret', 'token', 'key', 'auth', 'credential']):
            sanitized[key] = '***'
            continue
        
        # Recursively sanitize nested dicts
        if isinstance(value, dict):
            sanitized[key] = sanitize_dict(value, max_depth - 1)
        elif isinstance(value, str):
            sanitized[key] = sanitize_string(value)
        elif isinstance(value, (list, tuple)):
            # Sanitize list items
            sanitized[key] = [
                sanitize_string(item) if isinstance(item, str) else item
                for item in value
            ]
        else:
            sanitized[key] = value
    
    return sanitized


def sanitize_traceback(traceback_str: str, max_lines: int = 15) -> str:
    """
    Sanitize traceback by removing sensitive data and limiting lines.
    
    Args:
        traceback_str: Full traceback string
        max_lines: Maximum number of lines to include
        
    Returns:
        Sanitized and truncated traceback
    """
    if not traceback_str:
        return ""
    
    # Split into lines
    lines = traceback_str.split('\n')
    
    # Limit number of lines
    if max_lines > 0 and len(lines) > max_lines:
        lines = lines[:max_lines]
        lines.append("... (truncated)")
    
    # Sanitize each line
    sanitized_lines = [sanitize_string(line) for line in lines]
    
    return '\n'.join(sanitized_lines)


def sanitize_url(url: str) -> str:
    """
    Sanitize URL by removing query parameters that might be sensitive.
    
    Args:
        url: URL string
        
    Returns:
        Sanitized URL
        
    Example:
        >>> url = "/api/users?token=abc123&id=5"
        >>> sanitize_url(url)
        '/api/users?token=***&id=5'
    """
    if not url or '?' not in url:
        return url
    
    base, query = url.split('?', 1)
    
    # Parse query parameters
    params = []
    for param in query.split('&'):
        if '=' in param:
            key, value = param.split('=', 1)
            key_lower = key.lower()
            
            # Check if parameter is sensitive
            if any(sensitive in key_lower for sensitive in 
                   ['token', 'key', 'secret', 'password', 'auth']):
                params.append(f"{key}=***")
            else:
                params.append(param)
        else:
            params.append(param)
    
    return f"{base}?{'&'.join(params)}"