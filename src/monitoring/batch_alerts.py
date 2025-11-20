"""
Batch alerts for non-critical warnings.
Sends aggregated reports instead of individual alerts.

NOTE: This module is designed to work with background task schedulers.
You need to schedule send_batch_alerts() to run periodically.

Example with ARQ:
    from arq import cron
    from monitoring.batch_alerts import send_batch_alerts
    
    class WorkerSettings:
        cron_jobs = [
            cron(send_batch_alerts, minute={0, 15, 30, 45})  # Every 15 min
        ]

Example with APScheduler:
    from apscheduler.schedulers.asyncio import AsyncIOScheduler
    from monitoring.batch_alerts import send_batch_alerts
    
    scheduler = AsyncIOScheduler()
    scheduler.add_job(
        send_batch_alerts, 
        'interval', 
        minutes=15,
        args=[{}]  # Pass empty context dict
    )
"""

import logging
import json
from datetime import datetime
from typing import List, Dict, Any, Optional
from collections import defaultdict

from monitoring.config import monitoring_config
from monitoring.telegram import telegram_reporter, AlertLevel
from monitoring import get_redis_adapter


logger = logging.getLogger(__name__)


async def send_batch_alerts(ctx: Optional[dict] = None):
    """
    Send batched alerts for slow requests and other warnings.
    
    This function should be called periodically by your task scheduler.
    Runs every BATCH_WINDOW_MINUTES (configured in settings).
    
    Args:
        ctx: Context dict (for ARQ compatibility). Can be None or empty dict.
    """
    if not monitoring_config.is_enabled:
        return
    
    logger.info("Processing batch alerts...")
    
    try:
        # Collect all batch data
        slow_requests = await _collect_slow_requests_batch()
        task_warnings = await _collect_task_warnings_batch()
        
        # Build and send summary if there's data
        if slow_requests or task_warnings:
            await _send_batch_summary(slow_requests, task_warnings)
        else:
            logger.debug("No batch alerts to send")
            
    except Exception as e:
        logger.error(f"Failed to process batch alerts: {e}")


async def _collect_slow_requests_batch() -> List[Dict[str, Any]]:
    """Collect slow requests from the current batch"""
    redis_adapter = get_redis_adapter()
    if not redis_adapter:
        return []
    
    try:
        # Get current hour batch
        batch_key = monitoring_config.get_redis_key(
            "slow_requests_batch",
            datetime.utcnow().strftime("%Y-%m-%d-%H")
        )
        
        # Get all items from the batch
        batch_items = await redis_adapter.lrange(batch_key, 0, -1)
        
        # Clear the batch after reading
        await redis_adapter.delete(batch_key)
        
        # Parse and aggregate
        requests_by_endpoint = defaultdict(list)
        
        for item in batch_items:
            try:
                data = json.loads(item)
                endpoint = data['path']
                requests_by_endpoint[endpoint].append({
                    'time': data['time'],
                    'user': data['user'],
                    'timestamp': data['timestamp']
                })
            except (json.JSONDecodeError, KeyError) as e:
                logger.warning(f"Failed to parse batch item: {e}")
        
        # Format for return
        result = []
        for endpoint, requests in requests_by_endpoint.items():
            # Sort by time (slowest first)
            requests.sort(key=lambda x: x['time'], reverse=True)
            
            result.append({
                'endpoint': endpoint,
                'count': len(requests),
                'max_time': max(r['time'] for r in requests),
                'avg_time': sum(r['time'] for r in requests) / len(requests),
                'samples': requests[:3]  # Top 3 slowest
            })
        
        # Sort by count
        result.sort(key=lambda x: x['count'], reverse=True)
        
        return result
        
    except Exception as e:
        logger.error(f"Failed to collect slow requests batch: {e}")
        return []


async def _collect_task_warnings_batch() -> Dict[str, Any]:
    """Collect task-related warnings from the batch period"""
    redis_adapter = get_redis_adapter()
    if not redis_adapter:
        return {}
    
    try:
        today = datetime.utcnow().strftime("%Y-%m-%d")
        
        # Collect failed tasks
        failed_tasks = {}
        failure_pattern = monitoring_config.get_redis_key(
            "stats", today, "tasks:failure:*"
        )
        
        cursor = 0
        while True:
            cursor, keys = await redis_adapter.scan(
                cursor, 
                match=failure_pattern,
                count=100
            )
            
            for key in keys:
                task_name = key.split(":")[-1]
                count = await redis_adapter.get(key)
                if count and int(count) > 0:
                    failed_tasks[task_name] = int(count)
            
            if cursor == 0:
                break
        
        # Collect slow tasks
        slow_tasks = {}
        slow_pattern = monitoring_config.get_redis_key("tasks", "slow", "*")
        
        cursor = 0
        while True:
            cursor, keys = await redis_adapter.scan(
                cursor,
                match=slow_pattern,
                count=100
            )
            
            for key in keys:
                task_name = key.split(":")[-1]
                slow_tasks[task_name] = True  # Just mark as slow
            
            if cursor == 0:
                break
        
        return {
            'failed': failed_tasks,
            'slow': list(slow_tasks.keys())
        }
        
    except Exception as e:
        logger.error(f"Failed to collect task warnings: {e}")
        return {}


async def _send_batch_summary(
    slow_requests: List[Dict[str, Any]],
    task_warnings: Dict[str, Any]
):
    """Send aggregated summary of warnings"""

    if (not slow_requests 
        and not task_warnings.get('failed') 
        and not task_warnings.get('slow')):
        logger.debug("No issues to report in batch summary, skipping")
        return
    
    try:
        lines = [
            f"⚠️ *Batch Alert Summary*",
            f"_{monitoring_config.MONITORING_ENV.upper()}_",
            f"_Period: Last {monitoring_config.BATCH_WINDOW_MINUTES} minutes_",
            "",
        ]
        
        # Slow requests section
        if slow_requests:
            lines.append("*🌐 Slow Requests:*")
            
            for req in slow_requests[:5]:  # Top 5 endpoints
                lines.append(
                    f"• `{req['endpoint']}`: {req['count']} requests, "
                    f"max {req['max_time']:.1f}s, avg {req['avg_time']:.1f}s"
                )
                
                # Show samples
                for sample in req['samples']:
                    time_ago = int(
                        (datetime.utcnow().timestamp() - sample['timestamp']) / 60
                    )
                    lines.append(
                        f"  - {sample['time']:.1f}s by {sample['user']} "
                        f"({time_ago}m ago)"
                    )
            
            if len(slow_requests) > 5:
                lines.append(f"  _...and {len(slow_requests) - 5} more endpoints_")
            
            lines.append("")
        
        # Task warnings section
        if task_warnings.get('failed'):
            lines.append("*❌ Failed Tasks:*")
            
            for task_name, count in sorted(
                task_warnings['failed'].items(),
                key=lambda x: x[1],
                reverse=True
            )[:5]:
                lines.append(f"• `{task_name}`: {count} failures")
            
            if len(task_warnings['failed']) > 5:
                lines.append(
                    f"  _...and {len(task_warnings['failed']) - 5} more tasks_"
                )
            
            lines.append("")
        
        if task_warnings.get('slow'):
            lines.append("*⏱️ Slow Tasks:*")
            
            for task_name in task_warnings['slow'][:5]:
                lines.append(f"• `{task_name}`")
            
            if len(task_warnings['slow']) > 5:
                lines.append(
                    f"  _...and {len(task_warnings['slow']) - 5} more tasks_"
                )
            
            lines.append("")
        
        # Add summary stats
        total_issues = (
            len(slow_requests) + len(task_warnings.get('failed', {}))
        )
        if total_issues > 0:
            lines.append(f"*Total Issues:* {total_issues}")
            lines.append(
                f"_Threshold: Slow requests >"
                f"{monitoring_config.SLOW_REQUEST_THRESHOLD_SECONDS}s, "
                f"Slow tasks >"
                f"{monitoring_config.ARQ_TASK_SLOW_THRESHOLD_SECONDS}s_"
            )
        
        # Send the summary
        full_text = "\n".join(lines)
        
        await telegram_reporter.send_message(
            text=full_text,
            level=AlertLevel.WARNING,
            disable_notification=True  # Don't buzz for batch alerts
        )
        
        logger.info(f"Batch alert sent with {total_issues} issues")
        
    except Exception as e:
        logger.error(f"Failed to send batch summary: {e}")