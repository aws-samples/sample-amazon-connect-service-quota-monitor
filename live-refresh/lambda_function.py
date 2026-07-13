#!/usr/bin/env python3
# SPDX-License-Identifier: MIT-0
"""Connect Quota Dashboard — V1 Lambda.

Runs on a schedule (default: every hour). On each run:
1. Collects current quota utilization from CloudWatch + ServiceQuotas
2. Writes the result to S3 as latest.json (same URL, always fresh)
3. Writes a timestamped copy to the archive folder
4. Updates daily peak if this run exceeds previous peak

API Gateway serves the same Lambda for on-demand queries:
    GET /quota                → latest snapshot (same as latest.json)
    GET /quota?history=1h     → last 1 hour of archive entries
    GET /quota?history=1d     → last 24 hours (hourly entries)
    GET /quota?history=7d     → last 7 days (daily peaks)
    GET /quota?history=trend  → 30-day trend (daily peaks)

S3 structure:
    s3://BUCKET/
    ├── latest.json                          ← always current (overwritten every run)
    ├── archive/
    │   └── YYYY-MM-DD/
    │       ├── HH-MM.json                   ← each run
    │       └── ...
    └── peaks/
        ├── YYYY-MM-DD.json                  ← daily peak summary
        └── ...

Author: Amazon.com, Inc.
License: MIT-0
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any

import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger()
logger.setLevel(logging.INFO)

CONNECT_SERVICE_CODE = "connect"

HIGH_TRAFFIC_APIS = (
    "GetContactAttributes",
    "UpdateContactAttributes",
    "DescribeContact",
    "GetMetricDataV2",
    "GetCurrentMetricData",
    "StartOutboundVoiceContact",
    "StopContact",
    "TransferContact",
    "TagContact",
    "UntagContact",
    "SearchContacts",
)


def lambda_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """Entry point. Handles both scheduled events and API Gateway requests."""
    instance_id = os.environ.get("CONNECT_INSTANCE_ID", "")
    bucket = os.environ.get("S3_BUCKET", "")

    if not instance_id:
        return _response(400, {"error": "CONNECT_INSTANCE_ID not configured"})

    # Determine if this is an API Gateway request or a scheduled event
    params = event.get("queryStringParameters") or {}
    history = params.get("history", "")

    # If history requested, serve from archive (no new collection)
    if history:
        return _serve_history(bucket, history)

    # Otherwise: collect fresh data, write to S3, return latest
    snapshot = _collect_snapshot(instance_id)

    if bucket:
        _write_latest(bucket, snapshot)
        _write_archive(bucket, snapshot)
        _update_peak(bucket, snapshot)

    return _response(200, snapshot)


# ═══════════════════════════════════════════════════════════════════════════════
# COLLECTION
# ═══════════════════════════════════════════════════════════════════════════════


def _collect_snapshot(instance_id: str) -> dict[str, Any]:
    """Collect current quota state from CloudWatch and ServiceQuotas."""
    cw = boto3.client("cloudwatch")
    sq = boto3.client("service-quotas")
    now = datetime.now(timezone.utc)

    # Get concurrent metrics (with MetricGroup dimension fix)
    concurrent = _get_concurrent_metrics(cw, instance_id)

    # Get API TPS from CloudWatch Usage metrics (last hour)
    api_usage = _get_api_usage(cw, now)

    # Get quota limits
    quota_limits = _get_quota_limits(sq)

    # Build snapshot
    quotas = []
    for api_name in HIGH_TRAFFIC_APIS:
        usage = api_usage.get(api_name, 0)
        limit = quota_limits.get(api_name, 2)  # Default TPS if not found
        utilization = round(usage / limit * 100, 1) if limit > 0 else 0
        headroom = round(limit - usage, 2)

        quotas.append({
            "api": api_name,
            "current_tps": round(usage, 2),
            "limit_tps": limit,
            "utilization_pct": utilization,
            "headroom_tps": headroom,
            "status": "critical" if utilization > 85 else "warning" if utilization > 70 else "ok",
        })

    # Sort by utilization descending (highest risk first)
    quotas.sort(key=lambda q: q["utilization_pct"], reverse=True)

    return {
        "timestamp": now.isoformat(),
        "instance_id": instance_id,
        "concurrent": concurrent,
        "quotas": quotas,
        "summary": {
            "total_checked": len(quotas),
            "critical": sum(1 for q in quotas if q["status"] == "critical"),
            "warning": sum(1 for q in quotas if q["status"] == "warning"),
            "ok": sum(1 for q in quotas if q["status"] == "ok"),
            "highest_utilization_pct": quotas[0]["utilization_pct"] if quotas else 0,
            "highest_utilization_api": quotas[0]["api"] if quotas else "none",
        },
    }


def _get_concurrent_metrics(cw: Any, instance_id: str) -> dict[str, int]:
    """Get ConcurrentCalls, ConcurrentChats, ConcurrentTasks with MetricGroup dimension."""
    now = datetime.now(timezone.utc)
    start = now - timedelta(minutes=5)
    results = {}

    for metric_name, group in [
        ("ConcurrentCalls", "VoiceCalls"),
        ("ConcurrentActiveChats", "Chats"),
        ("ConcurrentActiveTasks", "Tasks"),
    ]:
        try:
            resp = cw.get_metric_data(
                MetricDataQueries=[{
                    "Id": "m1",
                    "MetricStat": {
                        "Metric": {
                            "Namespace": "AWS/Connect",
                            "MetricName": metric_name,
                            "Dimensions": [
                                {"Name": "InstanceId", "Value": instance_id},
                                {"Name": "MetricGroup", "Value": group},
                            ],
                        },
                        "Period": 300,
                        "Stat": "Maximum",
                    },
                }],
                StartTime=start,
                EndTime=now,
            )
            values = resp.get("MetricDataResults", [{}])[0].get("Values", [])
            results[metric_name] = int(max(values)) if values else 0
        except ClientError:
            results[metric_name] = 0

    return results


def _get_api_usage(cw: Any, now: datetime) -> dict[str, float]:
    """Get peak TPS per API from CloudWatch Usage metrics (last hour)."""
    start = now - timedelta(hours=1)
    queries = []

    for i, api_name in enumerate(HIGH_TRAFFIC_APIS):
        queries.append({
            "Id": f"a{i}",
            "MetricStat": {
                "Metric": {
                    "Namespace": "AWS/Usage",
                    "MetricName": "CallCount",
                    "Dimensions": [
                        {"Name": "Service", "Value": "Connect"},
                        {"Name": "Type", "Value": "API"},
                        {"Name": "Resource", "Value": api_name},
                        {"Name": "Class", "Value": "None"},
                    ],
                },
                "Period": 60,
                "Stat": "Sum",
            },
        })

    try:
        resp = cw.get_metric_data(
            MetricDataQueries=queries,
            StartTime=start,
            EndTime=now,
        )
    except ClientError as e:
        logger.warning("CloudWatch Usage query failed: %s", e)
        return {}

    results = {}
    for metric_result in resp.get("MetricDataResults", []):
        idx = int(metric_result["Id"].replace("a", ""))
        values = metric_result.get("Values", [])
        # Peak TPS = max calls in any 60s window / 60
        peak_calls = max(values) if values else 0
        results[HIGH_TRAFFIC_APIS[idx]] = peak_calls / 60.0

    return results


def _get_quota_limits(sq: Any) -> dict[str, float]:
    """Get applied quota limits from ServiceQuotas."""
    limits = {}
    try:
        paginator = sq.get_paginator("list_service_quotas")
        for page in paginator.paginate(ServiceCode=CONNECT_SERVICE_CODE):
            for quota in page.get("Quotas", []):
                name = quota.get("QuotaName", "")
                # Match "Rate of X API requests" pattern
                for api_name in HIGH_TRAFFIC_APIS:
                    if api_name in name:
                        limits[api_name] = quota.get("Value", 2)
                        break
    except ClientError as e:
        logger.warning("ServiceQuotas query failed: %s", e)
    return limits


# ═══════════════════════════════════════════════════════════════════════════════
# S3 WRITES
# ═══════════════════════════════════════════════════════════════════════════════


def _write_latest(bucket: str, snapshot: dict[str, Any]) -> None:
    """Run the full resource mapper and upload the API report HTML to S3.

    This produces the same connect-api-report.html that the CLI generates,
    with all tabs (All APIs, Per Flow, Quotas, Lambdas), sortable tables,
    and search. The snapshot data is merged into the model for current TPS.
    """
    import sys
    import tempfile
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

    s3 = boto3.client("s3")
    instance_id = os.environ.get("CONNECT_INSTANCE_ID", "")
    region = os.environ.get("AWS_REGION", "us-east-1")

    # Write JSON snapshot (for API consumers and archive)
    try:
        s3.put_object(
            Bucket=bucket,
            Key="latest.json",
            Body=json.dumps(snapshot, default=str),
            ContentType="application/json",
            CacheControl="no-cache, max-age=0",
        )
    except ClientError as e:
        logger.warning("Failed to write latest.json: %s", e)

    # Generate the full API report HTML
    try:
        import importlib
        mapper = importlib.import_module("connect-resource-mapper")
        from consolidated_report import generate_consolidated_report_string

        resource_map, model = mapper.collect_all(instance_id, region)
        html = generate_consolidated_report_string(resource_map, model)

        s3.put_object(
            Bucket=bucket,
            Key="index.html",
            Body=html.encode("utf-8"),
            ContentType="text/html; charset=utf-8",
            CacheControl="no-cache, max-age=0",
        )
        logger.info("API report HTML written to s3://%s/index.html", bucket)
    except ImportError:
        # Fallback: generate lightweight HTML if mapper modules not bundled
        logger.warning("Full mapper not bundled in Lambda. Generating lightweight dashboard.")
        html = _generate_dashboard_html(snapshot)
        s3.put_object(
            Bucket=bucket,
            Key="index.html",
            Body=html.encode("utf-8"),
            ContentType="text/html; charset=utf-8",
            CacheControl="no-cache, max-age=0",
        )
    except Exception as e:
        logger.warning("Failed to generate API report: %s. Falling back to lightweight.", e)
        html = _generate_dashboard_html(snapshot)
        s3.put_object(
            Bucket=bucket,
            Key="index.html",
            Body=html.encode("utf-8"),
            ContentType="text/html; charset=utf-8",
            CacheControl="no-cache, max-age=0",
        )


def _write_archive(bucket: str, snapshot: dict[str, Any]) -> None:
    """Write timestamped copy to archive folder."""
    s3 = boto3.client("s3")
    now = datetime.now(timezone.utc)
    key = f"archive/{now.strftime('%Y-%m-%d')}/{now.strftime('%H-%M')}.json"
    try:
        s3.put_object(
            Bucket=bucket,
            Key=key,
            Body=json.dumps(snapshot, default=str),
            ContentType="application/json",
        )
        logger.info("Archive written: %s", key)
    except ClientError as e:
        logger.warning("Failed to write archive: %s", e)


def _update_peak(bucket: str, snapshot: dict[str, Any]) -> None:
    """Update daily peak file if this snapshot exceeds current peak."""
    s3 = boto3.client("s3")
    now = datetime.now(timezone.utc)
    date_str = now.strftime("%Y-%m-%d")
    peak_key = f"peaks/{date_str}.json"

    current_calls = snapshot.get("concurrent", {}).get("ConcurrentCalls", 0)
    current_max_util = snapshot.get("summary", {}).get("highest_utilization_pct", 0)

    # Read existing peak
    existing_peak = None
    try:
        resp = s3.get_object(Bucket=bucket, Key=peak_key)
        existing_peak = json.loads(resp["Body"].read().decode("utf-8"))
    except ClientError:
        pass

    # Compare
    should_update = existing_peak is None
    if existing_peak:
        prev_calls = existing_peak.get("peak_concurrent_calls", 0)
        prev_util = existing_peak.get("peak_utilization_pct", 0)
        if current_calls > prev_calls or current_max_util > prev_util:
            should_update = True

    if should_update:
        peak_data = {
            "date": date_str,
            "peak_hour": now.strftime("%H:%M UTC"),
            "peak_concurrent_calls": current_calls,
            "peak_concurrent_chats": snapshot.get("concurrent", {}).get("ConcurrentActiveChats", 0),
            "peak_concurrent_tasks": snapshot.get("concurrent", {}).get("ConcurrentActiveTasks", 0),
            "peak_utilization_pct": current_max_util,
            "peak_utilization_api": snapshot.get("summary", {}).get("highest_utilization_api", ""),
            "quotas_at_peak": snapshot.get("quotas", [])[:5],  # Top 5 at peak
            "updated_at": now.isoformat(),
        }
        try:
            s3.put_object(
                Bucket=bucket,
                Key=peak_key,
                Body=json.dumps(peak_data, indent=2, default=str),
                ContentType="application/json",
            )
            logger.info("Peak updated: %s", peak_key)
        except ClientError as e:
            logger.warning("Failed to write peak: %s", e)


# ═══════════════════════════════════════════════════════════════════════════════
# HISTORY SERVING
# ═══════════════════════════════════════════════════════════════════════════════


def _serve_history(bucket: str, history: str) -> dict[str, Any]:
    """Serve historical data from archive/peaks folders.

    Args:
        bucket: S3 bucket name.
        history: One of '1h', '1d', '7d', 'trend'.

    Returns:
        API Gateway response with historical entries.
    """
    if not bucket:
        return _response(400, {"error": "S3_BUCKET not configured for history"})

    s3 = boto3.client("s3")
    now = datetime.now(timezone.utc)

    if history == "1h":
        # Last 1 hour: list archive entries from current date/hour
        entries = _list_archive_entries(s3, bucket, now - timedelta(hours=1), now)
        return _response(200, {"history": "1h", "entries": entries})

    elif history == "1d":
        # Last 24 hours: list archive entries
        entries = _list_archive_entries(s3, bucket, now - timedelta(hours=24), now)
        return _response(200, {"history": "1d", "entries": entries})

    elif history in ("7d", "trend"):
        # 7 days or 30 days: use daily peak files
        days = 30 if history == "trend" else 7
        peaks = _list_peak_entries(s3, bucket, days)
        return _response(200, {"history": history, "entries": peaks})

    else:
        return _response(400, {"error": f"Invalid history value: {history}. Use 1h, 1d, 7d, or trend."})


def _list_archive_entries(s3: Any, bucket: str, start: datetime, end: datetime) -> list[dict]:
    """List and read archive entries between start and end."""
    entries = []
    current = start

    while current <= end:
        date_str = current.strftime("%Y-%m-%d")
        prefix = f"archive/{date_str}/"

        try:
            resp = s3.list_objects_v2(Bucket=bucket, Prefix=prefix)
            for obj in resp.get("Contents", []):
                key = obj["Key"]
                # Parse time from filename (HH-MM.json)
                filename = key.split("/")[-1].replace(".json", "")
                try:
                    hour, minute = int(filename.split("-")[0]), int(filename.split("-")[1])
                    entry_time = current.replace(hour=hour, minute=minute, second=0)
                    if start <= entry_time <= end:
                        # Read the entry
                        data = s3.get_object(Bucket=bucket, Key=key)
                        entry = json.loads(data["Body"].read().decode("utf-8"))
                        entries.append(entry)
                except (ValueError, IndexError):
                    continue
        except ClientError:
            pass

        current += timedelta(days=1)

    # Sort by timestamp
    entries.sort(key=lambda e: e.get("timestamp", ""))
    return entries


def _list_peak_entries(s3: Any, bucket: str, days: int) -> list[dict]:
    """Read daily peak files for the last N days."""
    peaks = []
    now = datetime.now(timezone.utc)

    for i in range(days):
        date = now - timedelta(days=i)
        peak_key = f"peaks/{date.strftime('%Y-%m-%d')}.json"
        try:
            resp = s3.get_object(Bucket=bucket, Key=peak_key)
            peak = json.loads(resp["Body"].read().decode("utf-8"))
            peaks.append(peak)
        except ClientError:
            continue

    # Reverse so oldest is first (chronological order)
    peaks.reverse()
    return peaks


# ═══════════════════════════════════════════════════════════════════════════════
# UTILITIES
# ═══════════════════════════════════════════════════════════════════════════════


def _response(status_code: int, body: dict[str, Any]) -> dict[str, Any]:
    """Build API Gateway proxy response with CORS headers."""
    return {
        "statusCode": status_code,
        "headers": {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Methods": "GET, OPTIONS",
            "Access-Control-Allow-Headers": "Content-Type",
            "Cache-Control": "no-cache, max-age=0" if status_code == 200 else "no-store",
        },
        "body": json.dumps(body, default=str),
    }


# ═══════════════════════════════════════════════════════════════════════════════
# HTML DASHBOARD GENERATION
# ═══════════════════════════════════════════════════════════════════════════════


def _generate_dashboard_html(snapshot: dict[str, Any]) -> str:
    """Generate a self-contained HTML dashboard from the current snapshot.

    The HTML includes:
    - Current quota utilization table with color-coded bars
    - Concurrent calls/chats/tasks summary
    - Auto-refresh: fetches latest data from the API endpoint on page load
    - History view: fetches ?history=1d and ?history=7d for trend charts
    """
    quotas = snapshot.get("quotas", [])
    concurrent = snapshot.get("concurrent", {})
    summary = snapshot.get("summary", {})
    timestamp = snapshot.get("timestamp", "")
    instance_id = snapshot.get("instance_id", "")

    # Build quota rows
    quota_rows = ""
    for q in quotas:
        pct = q["utilization_pct"]
        color = "#d91515" if pct > 85 else "#d97706" if pct > 70 else "#037f0c"
        shape = "&#9632;" if pct > 85 else "&#9650;" if pct > 70 else "&#9679;"
        width = min(pct, 100)
        quota_rows += f"""<tr>
<td style="font-weight:600">{q['api']}</td>
<td style="text-align:right">{q['current_tps']}</td>
<td style="text-align:right">{q['limit_tps']}</td>
<td><div style="background:#e9ebed;border-radius:4px;height:8px;width:120px;display:inline-block;vertical-align:middle"><div style="background:{color};height:100%;border-radius:4px;width:{width}%"></div></div></td>
<td style="color:{color};font-weight:600;text-align:right"><span aria-hidden="true">{shape}</span> {pct}%</td>
<td style="text-align:right">{q['headroom_tps']}</td>
<td>{q['status']}</td>
</tr>"""

    api_endpoint = os.environ.get("API_ENDPOINT", "")
    history_js = ""
    if api_endpoint:
        history_js = f"""
<script>
async function loadHistory() {{
  try {{
    const resp = await fetch('{api_endpoint}?history=7d');
    if (!resp.ok) return;
    const data = await resp.json();
    const entries = data.entries || [];
    if (entries.length === 0) {{ document.getElementById('trend-section').innerHTML = '<p style="color:#687078">No historical data yet. Check back after 24 hours.</p>'; return; }}
    let html = '<table style="width:100%;border-collapse:collapse;font-size:13px"><thead><tr><th style="text-align:left;padding:8px">Date</th><th style="text-align:right;padding:8px">Peak Hour</th><th style="text-align:right;padding:8px">Peak Calls</th><th style="text-align:right;padding:8px">Peak Utilization</th></tr></thead><tbody>';
    entries.forEach(e => {{
      html += `<tr><td style="padding:6px 8px">${{e.date}}</td><td style="text-align:right;padding:6px 8px">${{e.peak_hour}}</td><td style="text-align:right;padding:6px 8px">${{e.peak_concurrent_calls}}</td><td style="text-align:right;padding:6px 8px;font-weight:600">${{e.peak_utilization_pct}}%</td></tr>`;
    }});
    html += '</tbody></table>';
    document.getElementById('trend-section').innerHTML = html;
  }} catch(e) {{ document.getElementById('trend-section').innerHTML = '<p style="color:#687078">Could not load history. API endpoint may not be configured.</p>'; }}
}}
loadHistory();
</script>"""

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>Connect Quota Dashboard</title>
<style>
body {{ font-family: 'Amazon Ember',Arial,sans-serif; background:#f2f3f3; color:#161D26; margin:0; padding:24px; line-height:1.5; }}
.container {{ max-width:1100px; margin:0 auto; }}
h1 {{ font-size:22px; font-weight:700; margin-bottom:4px; }}
.subtitle {{ color:#687078; font-size:13px; margin-bottom:24px; }}
.kpi-row {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(160px,1fr)); gap:12px; margin-bottom:24px; }}
.kpi {{ background:#fff; border-radius:8px; padding:16px; border:1px solid #e9ebed; }}
.kpi-label {{ font-size:11px; color:#687078; text-transform:uppercase; letter-spacing:.5px; }}
.kpi-value {{ font-size:26px; font-weight:700; margin-top:4px; }}
.kpi-value.green {{ color:#037f0c; }} .kpi-value.orange {{ color:#d97706; }} .kpi-value.red {{ color:#d91515; }}
.section {{ background:#fff; border-radius:8px; border:1px solid #e9ebed; margin-bottom:24px; overflow:hidden; }}
.section-header {{ padding:14px 18px; border-bottom:1px solid #e9ebed; font-size:15px; font-weight:700; }}
table {{ width:100%; border-collapse:collapse; font-size:12px; }}
th {{ text-align:left; padding:8px 12px; background:#f2f3f3; color:#687078; font-weight:600; font-size:11px; text-transform:uppercase; border-bottom:1px solid #e9ebed; }}
td {{ padding:6px 12px; border-bottom:1px solid #f2f3f3; }}
tr:hover td {{ background:#f8f9fa; }}
.footer {{ text-align:center; color:#687078; font-size:11px; padding:16px; }}
.refresh-note {{ background:#e8f4fd; border:1px solid #0972d3; border-radius:6px; padding:10px 14px; font-size:12px; margin-bottom:20px; }}
</style>
</head>
<body>
<div class="container">
<h1>Connect Quota Dashboard</h1>
<div class="subtitle">Instance: {instance_id} | Last updated: {timestamp}</div>

<div class="refresh-note">This page refreshes automatically every time the Lambda runs. Bookmark this URL.</div>

<div class="kpi-row">
<div class="kpi"><div class="kpi-label">Concurrent Calls</div><div class="kpi-value">{concurrent.get('ConcurrentCalls', 0)}</div></div>
<div class="kpi"><div class="kpi-label">Concurrent Chats</div><div class="kpi-value">{concurrent.get('ConcurrentActiveChats', 0)}</div></div>
<div class="kpi"><div class="kpi-label">Concurrent Tasks</div><div class="kpi-value">{concurrent.get('ConcurrentActiveTasks', 0)}</div></div>
<div class="kpi"><div class="kpi-label">Quotas Checked</div><div class="kpi-value">{summary.get('total_checked', 0)}</div></div>
<div class="kpi"><div class="kpi-label">Critical</div><div class="kpi-value red">{summary.get('critical', 0)}</div></div>
<div class="kpi"><div class="kpi-label">Warning</div><div class="kpi-value orange">{summary.get('warning', 0)}</div></div>
<div class="kpi"><div class="kpi-label">Highest Utilization</div><div class="kpi-value {'red' if summary.get('highest_utilization_pct', 0) > 85 else 'orange' if summary.get('highest_utilization_pct', 0) > 70 else 'green'}">{summary.get('highest_utilization_pct', 0)}%</div></div>
</div>

<div class="section">
<div class="section-header">API Quota Utilization (sorted by risk)</div>
<table>
<thead><tr><th>API</th><th style="text-align:right">Current TPS</th><th style="text-align:right">Limit</th><th>Utilization</th><th style="text-align:right">%</th><th style="text-align:right">Headroom</th><th>Status</th></tr></thead>
<tbody>{quota_rows}</tbody>
</table>
</div>

<div class="section">
<div class="section-header">7-Day Peak Trend</div>
<div style="padding:14px 18px" id="trend-section">
<p style="color:#687078">Loading historical data...</p>
</div>
</div>

<div class="footer">Generated by Connect Quota Monitor | Data refreshes every run</div>
</div>
{history_js}
</body>
</html>"""
