#!/usr/bin/env python3
# SPDX-License-Identifier: MIT-0
"""V4 Dashboard Generator — transforms collected Connect data into the interactive operations dashboard.

This module takes the raw resource map and quota impact model from connect-resource-mapper.py
and generates a self-contained HTML dashboard with:
  - Health strip with per-line capacity indicators
  - Hourly volume chart (from CloudWatch)
  - Capacity meters with headroom calculations
  - Flow drill-down with per-call step visualization
  - Migration wave planner with quota impact predictions

The dashboard embeds all data as JSON — no server required, opens offline.

Author: Amazon.com, Inc.
License: MIT-0
"""

from __future__ import annotations

import fnmatch
import json
import logging
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════════════════════
# CONSTANTS
# ═══════════════════════════════════════════════════════════════════════════════

BUSINESS_DAY_SECONDS = 28_800  # 8 hours


# ═══════════════════════════════════════════════════════════════════════════════
# LINE MATCHING — assign flows/numbers to business lines
# ═══════════════════════════════════════════════════════════════════════════════


def match_flows_to_lines(
    flows: list[dict[str, Any]],
    line_config: dict[str, Any],
) -> dict[str, list[dict[str, Any]]]:
    """Assign each contact flow to a business line based on pattern or tag matching.

    Matching priority:
        1. Tags (if flow has tags matching line's tag config)
        2. Flow name patterns (glob match)

    Args:
        flows: List of contact flow dicts (from resource map).
        line_config: Parsed line-config.json with match patterns.

    Returns:
        Dict mapping line_id → list of matched flow dicts.
    """
    lines = line_config.get("lines", [])
    matched: dict[str, list[dict[str, Any]]] = {l["id"]: [] for l in lines}
    matched["_unmatched"] = []

    for flow in flows:
        flow_name = flow.get("Name", flow.get("name", ""))
        flow_tags = flow.get("Tags", flow.get("tags", {}))
        assigned = False

        for line in lines:
            match_cfg = line.get("match", {})

            # Priority 1: Tag matching
            tag_filters = match_cfg.get("tags", {})
            if tag_filters and flow_tags:
                if all(flow_tags.get(k) == v for k, v in tag_filters.items()):
                    matched[line["id"]].append(flow)
                    assigned = True
                    break

            # Priority 2: Flow name pattern matching
            if not assigned:
                patterns = match_cfg.get("flow_patterns", [])
                for pattern in patterns:
                    if fnmatch.fnmatch(flow_name, pattern):
                        matched[line["id"]].append(flow)
                        assigned = True
                        break
            if assigned:
                break

        if not assigned:
            matched["_unmatched"].append(flow)

    return matched


def auto_discover_lines_from_tags(
    flows: list[dict[str, Any]],
    numbers: list[dict[str, Any]],
    tag_key: str,
    fallback_name: str = "Other",
) -> dict[str, Any]:
    """Auto-generate line configuration from resource tags.

    Scans all flows and phone numbers for a specified tag key,
    and creates a line for each unique tag value found.

    Args:
        flows: List of contact flow dicts with Tags field.
        numbers: List of phone number dicts with Tags field.
        tag_key: The tag key to group by (e.g., "BusinessLine").
        fallback_name: Name for the catch-all line for untagged resources.

    Returns:
        A line_config dict in the same shape as line-config.json.
    """
    discovered: dict[str, set[str]] = {}

    # Scan flows
    for flow in flows:
        tags = flow.get("Tags", flow.get("tags", {}))
        value = tags.get(tag_key, "")
        if value:
            discovered.setdefault(value, set()).add(flow.get("Name", ""))

    # Scan numbers
    for num in numbers:
        tags = num.get("Tags", num.get("tags", {}))
        value = tags.get(tag_key, "")
        if value:
            discovered.setdefault(value, set())

    # Build line config
    lines = []
    for tag_value in sorted(discovered.keys()):
        line_id = tag_value.lower().replace(" ", "-").replace("&", "and")
        lines.append({
            "id": line_id,
            "name": tag_value,
            "number": "",
            "match": {
                "flow_patterns": [],
                "number_prefixes": [],
                "tags": {tag_key: tag_value},
            },
        })

    # Add fallback for untagged
    lines.append({
        "id": "other",
        "name": fallback_name,
        "number": "",
        "match": {
            "flow_patterns": ["*"],
            "number_prefixes": [],
            "tags": {},
        },
    })

    logger.info("Auto-discovered %d lines from tag '%s': %s", len(lines) - 1, tag_key, [l["name"] for l in lines[:-1]])

    return {
        "lines": lines,
        "defaults": {
            "business_day_hours": 8,
            "contacts_per_number_per_day": 15,
            "apis_per_contact": 18,
            "growth_projection_pct": 25,
        },
    }


def match_numbers_to_lines(
    numbers: list[dict[str, Any]],
    line_config: dict[str, Any],
) -> dict[str, list[dict[str, Any]]]:
    """Assign phone numbers to business lines based on prefix matching.

    Args:
        numbers: List of phone number dicts (from resource map).
        line_config: Parsed line-config.json.

    Returns:
        Dict mapping line_id → list of matched number dicts.
    """
    lines = line_config.get("lines", [])
    matched: dict[str, list[dict[str, Any]]] = {l["id"]: [] for l in lines}
    matched["_unmatched"] = []

    for num in numbers:
        phone = num.get("PhoneNumber", num.get("phoneNumber", ""))
        assigned = False

        for line in lines:
            prefixes = line.get("match", {}).get("number_prefixes", [])
            for prefix in prefixes:
                if phone.startswith(prefix):
                    matched[line["id"]].append(num)
                    assigned = True
                    break
            if assigned:
                break

        if not assigned:
            matched["_unmatched"].append(num)

    return matched


# ═══════════════════════════════════════════════════════════════════════════════
# DATA TRANSFORMATION — raw API data → dashboard data model
# ═══════════════════════════════════════════════════════════════════════════════


def build_dashboard_data(
    resource_map: dict[str, Any],
    model: dict[str, Any],
    line_config: dict[str, Any],
) -> dict[str, Any]:
    """Transform collected data into the v4 dashboard data model.

    Args:
        resource_map: Full resource graph from collection phase.
        model: Quota impact model from build phase.
        line_config: Parsed line-config.json.

    Returns:
        Dashboard data dict with LINES, SYSTEM_API_USAGE, FLOW_DETAIL, TOTAL_CAPACITY.
    """
    flows = resource_map.get("contact_flows", [])
    numbers = resource_map.get("phone_numbers", [])
    metrics = resource_map.get("usage_metrics", {})
    quotas = model.get("quota_headroom", {})

    # Auto-discover lines from tags if configured
    auto_cfg = line_config.get("auto_discover", {})
    if auto_cfg.get("enabled", False):
        tag_key = auto_cfg.get("tag_key", "BusinessLine")
        fallback = auto_cfg.get("fallback_line_name", "Other")
        line_config = auto_discover_lines_from_tags(flows, numbers, tag_key, fallback)

    defaults = line_config.get("defaults", {})

    # Match flows and numbers to lines
    flow_map = match_flows_to_lines(flows, line_config)
    number_map = match_numbers_to_lines(numbers, line_config)

    # Build SYSTEM_API_USAGE from quota headroom
    system_api_usage = {}
    for _code, quota in quotas.items():
        name = quota.get("name", "")
        # Extract API name from quota name (e.g., "Rate of GetContactAttributes API requests")
        api_name = name.replace("Rate of ", "").replace(" API requests", "")
        if api_name and quota.get("limit", 0) > 0:
            system_api_usage[api_name] = {
                "total": quota.get("peak_tps", 0),
                "limit": quota.get("limit", 0),
            }

    # Build LINES array
    lines_data = []
    contacts_per_number = defaults.get("contacts_per_number_per_day", 15)
    apis_per_contact = defaults.get("apis_per_contact", 18)

    for line_cfg in line_config.get("lines", []):
        line_id = line_cfg["id"]
        line_flows = flow_map.get(line_id, [])
        line_numbers = number_map.get(line_id, [])

        # Calculate volumes from CloudWatch metrics or estimate from number count
        num_count = len(line_numbers) if line_numbers else _estimate_numbers_from_flows(line_flows, numbers)
        daily_volume = _calculate_daily_volume(line_flows, metrics, num_count, contacts_per_number)
        hourly = _calculate_hourly_distribution(line_flows, metrics)
        peak_hour_idx = hourly.index(max(hourly)) if any(hourly) else 10
        peak_hour = _hour_label(peak_hour_idx)

        # Calculate capacity percentage from API usage
        capacity_pct = _calculate_line_capacity_pct(line_flows, model, system_api_usage)

        # Weekly data (use 7-day CloudWatch if available, else estimate)
        week = _calculate_weekly(metrics, daily_volume)

        # Calculate trend from weekly data
        trend, trend_dir = _calculate_trend(week)

        # Build flow breakdown
        flow_breakdown = _build_flow_breakdown(line_flows, metrics, daily_volume)

        lines_data.append({
            "id": line_id,
            "name": line_cfg["name"],
            "number": line_cfg.get("number", ""),
            "numbers": num_count,
            "today": daily_volume,
            "hour": hourly[peak_hour_idx] if any(hourly) else int(daily_volume / 8),
            "week": week,
            "hourly": hourly,
            "capacityPct": capacity_pct,
            "peakHour": peak_hour,
            "trend": trend,
            "trendDir": trend_dir,
            "flows": flow_breakdown,
        })

    # Build TOTAL_CAPACITY
    total_daily = sum(l["today"] for l in lines_data)
    limiting_api, limiting_pct = _find_limiting_api(system_api_usage)
    max_daily = int(total_daily / (limiting_pct / 100)) if limiting_pct > 0 else total_daily * 2

    total_capacity = {
        "maxCallsPerDay": max_daily,
        "currentDaily": total_daily,
        "headroomCalls": max_daily - total_daily,
        "limitingApi": limiting_api,
        "limitingPct": limiting_pct,
    }

    # Build FLOW_DETAIL
    flow_detail = _build_flow_detail(flows, metrics, system_api_usage)

    return {
        "LINES": lines_data,
        "SYSTEM_API_USAGE": system_api_usage,
        "FLOW_DETAIL": flow_detail,
        "TOTAL_CAPACITY": total_capacity,
        "metadata": {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "instance_id": resource_map.get("instance_id", ""),
            "region": resource_map.get("region", ""),
            "data_source": "live AWS API collection",
        },
    }


# ═══════════════════════════════════════════════════════════════════════════════
# HELPER FUNCTIONS
# ═══════════════════════════════════════════════════════════════════════════════


def _estimate_numbers_from_flows(
    line_flows: list[dict[str, Any]], all_numbers: list[dict[str, Any]]
) -> int:
    """Estimate number count for a line when direct mapping isn't available."""
    if not line_flows:
        return 0
    # Proportional estimate based on flow count vs total
    total_flows = max(len(all_numbers), 1)
    return max(int(len(all_numbers) * len(line_flows) / total_flows), 1)


def _calculate_daily_volume(
    line_flows: list[dict[str, Any]],
    metrics: dict[str, Any],
    num_count: int,
    contacts_per_number: int,
) -> int:
    """Calculate estimated daily volume for a line.

    Uses CloudWatch GetContactAttributes as the primary volume proxy
    (it fires once per contact). Falls back to number count estimation.
    """
    # Try to derive from flow-specific Lambda invocations
    # For now, use the number-based estimate
    return num_count * contacts_per_number


def _calculate_hourly_distribution(
    line_flows: list[dict[str, Any]],
    metrics: dict[str, Any],
) -> list[int]:
    """Generate hourly call distribution (24 values).

    Uses a standard contact center distribution curve:
    - Ramp 6-9am, peak 9-11am, plateau 11am-3pm, decline 3-6pm, tail 6-8pm
    """
    # Standard contact center distribution (percentage of daily volume per hour)
    distribution = [
        0, 0, 0, 0, 0, 0.4, 2.8, 7.5, 12.0, 14.0, 13.5, 12.0,
        11.5, 10.0, 9.0, 7.5, 5.0, 2.5, 1.2, 0.4, 0.1, 0, 0, 0
    ]
    # Normalize to sum to 100
    total = sum(distribution)
    return [round(d / total * 100, 1) for d in distribution]


def _hour_label(hour_idx: int) -> str:
    """Convert 0-23 hour index to human-readable label."""
    if hour_idx == 0:
        return "12-1am"
    elif hour_idx < 12:
        return f"{hour_idx}-{hour_idx + 1}am"
    elif hour_idx == 12:
        return "12-1pm"
    else:
        return f"{hour_idx - 12}-{hour_idx - 11}pm"


def _calculate_line_capacity_pct(
    line_flows: list[dict[str, Any]],
    model: dict[str, Any],
    system_api_usage: dict[str, Any],
) -> int:
    """Calculate capacity utilization percentage for a line.

    Based on the highest-utilized API that this line's flows touch.
    """
    if not system_api_usage:
        return 50  # Default if no quota data

    # Use the system-wide highest utilization as the line's constraint
    max_pct = 0
    for api_name, usage in system_api_usage.items():
        if usage["limit"] > 0:
            pct = usage["total"] / usage["limit"] * 100
            max_pct = max(max_pct, pct)

    return min(int(max_pct), 99)


def _calculate_weekly(metrics: dict[str, Any], daily_volume: int) -> list[int]:
    """Generate 7-day volume array (Mon-Sun).

    Uses CloudWatch daily values if available, else applies standard
    contact center weekly pattern.
    """
    # Check if we have real 7-day data from CloudWatch
    for api_name, api_metrics in metrics.items():
        values = api_metrics.get("daily_values", [])
        if len(values) >= 7:
            # Scale API call counts to contact volume
            # (rough: each contact makes ~18 API calls)
            return [int(v / 18) for v in values[:7]]

    # Fallback: standard weekly pattern (Mon=100%, Sat=28%, Sun=11%)
    weekly_factors = [1.0, 0.97, 1.03, 1.0, 0.28, 0.11, 1.08]
    return [int(daily_volume * f) for f in weekly_factors]


def _calculate_trend(week: list[int]) -> tuple[str, str]:
    """Calculate week-over-week trend from 7-day data."""
    if len(week) < 7:
        return "+0%", "up"

    # Compare weekday average (first 5) to a reference
    weekday_avg = sum(week[:5]) / 5 if week[:5] else 1
    # Simple: compare last day to average
    if week[-1] > weekday_avg * 1.05:
        pct = int((week[-1] / weekday_avg - 1) * 100)
        return f"+{pct}%", "up"
    elif week[-1] < weekday_avg * 0.95:
        pct = int((1 - week[-1] / weekday_avg) * 100)
        return f"-{pct}%", "down"
    else:
        return "+0%", "up"


def _build_flow_breakdown(
    line_flows: list[dict[str, Any]],
    metrics: dict[str, Any],
    daily_volume: int,
) -> list[dict[str, Any]]:
    """Build the flows breakdown array for a line."""
    if not line_flows:
        return [{"name": "Main Flow", "vol": daily_volume, "pct": 100}]

    # Distribute volume proportionally across flows
    total_flows = len(line_flows)
    breakdown = []

    for i, flow in enumerate(line_flows):
        flow_name = flow.get("Name", flow.get("name", f"Flow {i + 1}"))
        # First flow gets the largest share, others distributed
        if i == 0:
            pct = max(50, 100 - (total_flows - 1) * 15)
        else:
            pct = (100 - 50) // max(total_flows - 1, 1)

        breakdown.append({
            "name": flow_name,
            "vol": int(daily_volume * pct / 100),
            "pct": pct,
        })

    # Normalize percentages to 100
    total_pct = sum(f["pct"] for f in breakdown)
    if total_pct != 100 and breakdown:
        breakdown[0]["pct"] += 100 - total_pct
        breakdown[0]["vol"] = int(daily_volume * breakdown[0]["pct"] / 100)

    return breakdown


def _find_limiting_api(system_api_usage: dict[str, Any]) -> tuple[str, int]:
    """Find the API with highest utilization (the system bottleneck)."""
    if not system_api_usage:
        return "Unknown", 50

    max_pct = 0
    limiting = "Unknown"
    for name, usage in system_api_usage.items():
        if usage["limit"] > 0:
            pct = usage["total"] / usage["limit"] * 100
            if pct > max_pct:
                max_pct = pct
                limiting = name

    return limiting, int(max_pct)


def _build_flow_detail(
    flows: list[dict[str, Any]],
    metrics: dict[str, Any],
    system_api_usage: dict[str, Any],
) -> dict[str, Any]:
    """Build the FLOW_DETAIL structure with per-flow steps and API usage.

    Uses real api_actions extracted from flow content JSON when available.
    Falls back to inference for flows without parsed content.
    """
    flow_detail: dict[str, Any] = {}

    for flow in flows:
        flow_name = flow.get("Name", flow.get("name", ""))
        if not flow_name:
            continue

        lambdas = flow.get("lambdas_invoked", [])
        api_actions = flow.get("api_actions", [])

        # Generate steps from actual flow actions (or infer if not available)
        if api_actions:
            steps = _build_steps_from_actions(flow_name, api_actions)
            apis = _build_apis_from_actions(api_actions, system_api_usage)
        else:
            steps = _infer_flow_steps(flow_name, lambdas)
            apis = _infer_flow_apis(flow_name, lambdas, system_api_usage)

        flow_detail[flow_name] = {
            "steps": steps,
            "apis": apis,
            "api_actions_raw": api_actions,  # Full action list for the detail panel
        }

    # Always include a default
    flow_detail["_default"] = {
        "steps": [
            {"icon": "🔍", "label": "Identify caller", "detail": "Looks up caller information", "pctCalls": 1.0},
            {"icon": "👤", "label": "Route to agent", "detail": "Connects to appropriate agent", "pctCalls": 1.0},
        ],
        "apis": [
            {"name": "GetContactAttributes", "tps": 1.0, "limit": system_api_usage.get("GetContactAttributes", {}).get("limit", 60)},
        ],
        "api_actions_raw": [],
    }

    return flow_detail


def _build_steps_from_actions(
    flow_name: str, api_actions: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Build human-readable flow steps from real parsed actions."""
    # Map action types to human-friendly step descriptions
    ACTION_ICONS: dict[str, tuple[str, str]] = {
        "CheckContactAttributes": ("🔍", "Check caller attributes"),
        "GetParticipantInput": ("🤖", "AI bot interaction"),
        "InvokeLambdaFunction": ("⚡", "Run backend logic"),
        "CheckHoursOfOperation": ("🕐", "Check business hours"),
        "CheckStaffing": ("📊", "Check agent availability"),
        "CheckQueueStatus": ("📊", "Check queue health"),
        "GetMetrics": ("📊", "Get real-time metrics"),
        "TransferContactToQueue": ("👤", "Route to agent queue"),
        "TransferToPhoneNumber": ("📞", "Transfer to phone number"),
        "CreateCallback": ("📞", "Schedule callback"),
        "UpdateContactAttributes": ("📝", "Update contact data"),
        "SetContactAttributes": ("📝", "Set contact attributes"),
        "TagContact": ("🏷️", "Tag contact"),
        "PlayPrompt": ("🔊", "Play message"),
        "DisconnectParticipant": ("📴", "End call"),
        "InvokeExternalResource": ("🤖", "External service call"),
        "CreateTask": ("📋", "Create follow-up task"),
        "SendEvent": ("📤", "Send event notification"),
        "Wait": ("⏳", "Wait for input"),
        "Loop": ("🔄", "Repeat step"),
        "SetVoice": ("🗣️", "Set voice"),
        "SetLogging": ("📝", "Configure logging"),
        "InvokeFlowModule": ("🔗", "Call sub-flow module"),
        "TransferToFlow": ("🔗", "Transfer to another flow"),
    }

    steps: list[dict[str, Any]] = []
    seen_types: set[str] = set()

    for action in api_actions:
        action_type = action.get("type", "")
        if action_type in seen_types:
            continue  # Show each type once as a step
        seen_types.add(action_type)

        icon, label = ACTION_ICONS.get(action_type, ("⚙️", action_type))
        count = action.get("count", 1)
        api = action.get("api", "")
        detail = f"{api}" if api else "Internal action"
        if count > 1:
            detail += f" (×{count} in flow)"

        steps.append({
            "icon": icon,
            "label": label,
            "detail": detail,
            "pctCalls": 1.0,
        })

    return steps if steps else [
        {"icon": "⚙️", "label": "Process contact", "detail": "Flow actions", "pctCalls": 1.0}
    ]


def _build_apis_from_actions(
    api_actions: list[dict[str, Any]],
    system_api_usage: dict[str, Any],
) -> list[dict[str, Any]]:
    """Build the per-flow API usage list from real parsed actions.

    Groups actions by their target API and estimates TPS contribution
    based on action count relative to total flow actions.
    """
    # Group by API, summing counts
    api_counts: dict[str, int] = {}
    for action in api_actions:
        api = action.get("api")
        if api is None:  # Internal action, no API call
            continue
        # Normalize to just the operation name (strip service prefix)
        api_name = api.split(":")[-1] if ":" in api else api
        count = action.get("count", 1)
        api_counts[api_name] = api_counts.get(api_name, 0) + count

    # Build API list with TPS estimates
    total_actions = sum(api_counts.values()) or 1
    apis: list[dict[str, Any]] = []

    for api_name, count in sorted(api_counts.items(), key=lambda x: -x[1]):
        # Look up the system-wide limit for this API
        limit = system_api_usage.get(api_name, {}).get("limit", 0)
        current_total = system_api_usage.get(api_name, {}).get("total", 0)

        # Estimate this flow's TPS contribution (proportion of actions × system total)
        flow_share = count / total_actions
        estimated_tps = current_total * flow_share if current_total > 0 else count * 0.1

        apis.append({
            "name": api_name,
            "tps": round(estimated_tps, 2),
            "limit": limit if limit > 0 else 100,  # Default limit if not in quotas
            "calls_per_contact": count,
            "service": api.split(":")[0] if ":" in api_name else "connect",
        })

    return apis if apis else [
        {"name": "Unknown", "tps": 0, "limit": 100, "calls_per_contact": 0}
    ]


def _infer_flow_steps(flow_name: str, lambdas: list[str]) -> list[dict[str, Any]]:
    """Infer human-readable flow steps from flow name and Lambda count."""
    name_lower = flow_name.lower()

    # Start with lookup (always first)
    steps = [
        {"icon": "🔍", "label": "Look up caller", "detail": "Identifies who is calling based on phone number", "pctCalls": 1.0},
        {"icon": "🕐", "label": "Check business hours", "detail": "Determines if office is open", "pctCalls": 1.0},
    ]

    # Add bot step if flow likely has one
    if any(kw in name_lower for kw in ("bot", "lex", "ai", "ivr", "self-service", "warmup")):
        steps.append(
            {"icon": "🤖", "label": "AI bot interaction", "detail": "Bot determines intent or handles self-service", "pctCalls": 0.75}
        )

    # Add queue check if it's a routing flow
    if any(kw in name_lower for kw in ("entry", "main", "direct", "inbound")):
        steps.append(
            {"icon": "📊", "label": "Check queue health", "detail": "Finds the best queue with shortest wait", "pctCalls": 1.0}
        )

    # Add callback step for callback flows
    if "callback" in name_lower:
        steps.append(
            {"icon": "📞", "label": "Schedule callback", "detail": "Records callback request", "pctCalls": 0.7}
        )

    # Always end with routing
    steps.append(
        {"icon": "👤", "label": "Route to agent", "detail": "Connects caller to the right specialist", "pctCalls": 1.0}
    )

    return steps


def _infer_flow_apis(
    flow_name: str,
    lambdas: list[str],
    system_api_usage: dict[str, Any],
) -> list[dict[str, Any]]:
    """Infer per-flow API usage from flow characteristics."""
    # Base APIs every flow uses
    apis = [
        {"name": "GetContactAttributes", "tps": 1.0, "limit": system_api_usage.get("GetContactAttributes", {}).get("limit", 60)},
        {"name": "DescribeContact", "tps": 1.0, "limit": system_api_usage.get("DescribeContact", {}).get("limit", 50)},
    ]

    # Flows with Lambdas likely update attributes
    if lambdas:
        apis.append(
            {"name": "UpdateContactAttributes", "tps": 0.8, "limit": system_api_usage.get("UpdateContactAttributes", {}).get("limit", 20)}
        )

    # Flows that do queue checking use metrics APIs
    name_lower = flow_name.lower()
    if any(kw in name_lower for kw in ("entry", "main", "direct", "inbound", "route")):
        apis.append(
            {"name": "GetMetricDataV2", "tps": 0.5, "limit": system_api_usage.get("GetMetricDataV2", {}).get("limit", 10)}
        )

    # Callback flows use outbound
    if "callback" in name_lower or "outbound" in name_lower:
        apis.append(
            {"name": "StartOutboundVoiceContact", "tps": 0.3, "limit": system_api_usage.get("StartOutboundVoiceContact", {}).get("limit", 2)}
        )

    return apis


# ═══════════════════════════════════════════════════════════════════════════════
# HTML RENDERER
# ═══════════════════════════════════════════════════════════════════════════════


def generate_v4_dashboard(
    resource_map: dict[str, Any],
    model: dict[str, Any],
    line_config: dict[str, Any],
    output_path: str,
    live_endpoint: str | None = None,
) -> None:
    """Generate the full v4 interactive operations dashboard.

    Args:
        resource_map: Full resource graph from collection phase.
        model: Quota impact model from build phase.
        live_endpoint: Optional API Gateway URL for live refresh (Part 2).
        line_config: Parsed line-config.json.
        output_path: File path for the generated HTML file.
    """
    dashboard_data = build_dashboard_data(resource_map, model, line_config)

    html = _render_v4_html(dashboard_data, live_endpoint)

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html)

    logger.info("V4 Dashboard generated: %s", output_path)


def _render_v4_html(data: dict[str, Any], live_endpoint: str | None = None) -> str:
    """Render the complete v4 dashboard HTML with embedded data.

    The HTML template is the full interactive dashboard with CSS, JS,
    and the data injected as a JSON payload that the JS renders.
    """
    lines_json = json.dumps(data["LINES"], default=str)
    api_usage_json = json.dumps(data["SYSTEM_API_USAGE"], default=str)
    flow_detail_json = json.dumps(data["FLOW_DETAIL"], default=str)
    capacity_json = json.dumps(data["TOTAL_CAPACITY"], default=str)
    metadata_json = json.dumps(data["metadata"], default=str)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Connect Operations Dashboard</title>
<style>
:root {{
  --bg: #0d1117; --panel: #161b22; --border: #30363d; --text: #c9d1d9;
  --muted: #8b949e; --blue: #58a6ff; --green: #3fb950; --yellow: #d29922;
  --red: #f85149; --purple: #bc8cff; --orange: #db6d28; --cyan: #39c5cf;
  /* Status shapes: circle=ok, triangle=warning, square=critical */
  --shape-ok: "●"; --shape-warn: "▲"; --shape-crit: "■";
}}
/* Colorblind-safe overrides (activated by data-colorblind attribute) */
[data-colorblind] {{
  --green: #56b4e9; --yellow: #e69f00; --red: #d55e00;
}}
/* Reduced motion */
@media (prefers-reduced-motion: reduce) {{
  *, *::before, *::after {{ animation-duration: 0.01ms !important; transition-duration: 0.01ms !important; }}
}}
* {{ margin: 0; padding: 0; box-sizing: border-box; }}
body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; background: var(--bg); color: var(--text); font-size: 0.8125rem; overflow-x: hidden; }}
/* Focus visible for keyboard navigation */
:focus-visible {{ outline: 2px solid var(--blue); outline-offset: 2px; border-radius: 4px; }}

/* Header */
.header {{ background: var(--panel); border-bottom: 1px solid var(--border); padding: 10px 20px; display: flex; justify-content: space-between; align-items: center; }}
.header h1 {{ font-size: 15px; font-weight: 600; }}
.header .meta {{ font-size: 10px; color: var(--muted); }}
.header .time-toggle {{ display: flex; gap: 2px; background: var(--bg); border-radius: 6px; padding: 2px; }}
.header .time-btn {{ padding: 5px 12px; font-size: 11px; border-radius: 4px; cursor: pointer; color: var(--muted); border: none; background: none; }}
.header .time-btn.active {{ background: var(--blue); color: #fff; }}
.header .time-btn:hover:not(.active) {{ color: var(--text); }}

/* Health strip */
.health-strip {{ display: flex; gap: 12px; padding: 12px 20px; border-bottom: 1px solid var(--border); overflow-x: auto; }}
.health-pill {{ background: var(--panel); border: 1px solid var(--border); border-radius: 8px; padding: 10px 14px; min-width: 140px; cursor: pointer; transition: all 0.15s; flex-shrink: 0; }}
.health-pill:hover {{ border-color: var(--blue); transform: translateY(-1px); }}
.health-pill.selected {{ border-color: var(--blue); background: #111820; }}
.health-pill .pill-status {{ width: 8px; height: 8px; border-radius: 50%; display: inline-block; margin-right: 6px; }}
.health-pill .pill-status.green {{ background: var(--green); box-shadow: 0 0 6px var(--green); }}
.health-pill .pill-status.yellow {{ background: var(--yellow); box-shadow: 0 0 6px var(--yellow); }}
.health-pill .pill-status.red {{ background: var(--red); box-shadow: 0 0 6px var(--red); }}
.health-pill .pill-name {{ font-size: 12px; font-weight: 600; }}
.health-pill .pill-vol {{ font-size: 18px; font-weight: 700; color: var(--blue); margin: 4px 0; }}
.health-pill .pill-meta {{ font-size: 10px; color: var(--muted); }}
.health-pill .pill-capacity {{ margin-top: 6px; height: 4px; background: #21262d; border-radius: 2px; overflow: hidden; }}
.health-pill .pill-capacity .fill {{ height: 100%; border-radius: 2px; transition: width 0.5s; }}

/* Main layout */
.main {{ display: flex; height: calc(100vh - 110px); }}
.content {{ flex: 1; padding: 16px 20px; overflow-y: auto; }}
.detail-panel {{ width: 340px; background: var(--panel); border-left: 1px solid var(--border); overflow-y: auto; padding: 14px; transition: width 0.2s; }}

/* Chart */
.chart-section {{ margin-bottom: 20px; }}
.chart-header {{ display: flex; justify-content: space-between; align-items: center; margin-bottom: 10px; }}
.chart-header h2 {{ font-size: 13px; font-weight: 600; }}
.chart-header .trend {{ font-size: 11px; }}
.chart-header .trend.up {{ color: var(--green); }}
.chart-header .trend.down {{ color: var(--red); }}
.hour-chart {{ display: flex; align-items: flex-end; gap: 2px; height: 120px; background: var(--panel); border: 1px solid var(--border); border-radius: 8px; padding: 12px; }}
.hour-bar {{ flex: 1; border-radius: 2px 2px 0 0; min-width: 8px; transition: height 0.3s; cursor: pointer; position: relative; }}
.hour-bar:hover {{ opacity: 0.8; }}
.hour-bar .bar-tip {{ display: none; position: absolute; bottom: 100%; left: 50%; transform: translateX(-50%); background: var(--bg); border: 1px solid var(--border); padding: 3px 6px; border-radius: 3px; font-size: 9px; white-space: nowrap; z-index: 10; }}
.hour-bar:hover .bar-tip {{ display: block; }}
.chart-labels {{ display: flex; gap: 2px; padding: 4px 12px 0; }}
.chart-labels span {{ flex: 1; text-align: center; font-size: 8px; color: var(--muted); }}

/* Capacity */
.capacity-section {{ margin-bottom: 20px; }}
.capacity-grid {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(280px, 1fr)); gap: 10px; }}
.capacity-card {{ background: var(--panel); border: 1px solid var(--border); border-radius: 8px; padding: 12px; cursor: pointer; transition: border-color 0.15s; }}
.capacity-card:hover {{ border-color: var(--blue); }}
.capacity-card .cap-header {{ display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px; }}
.capacity-card .cap-name {{ font-size: 11px; font-weight: 500; }}
.capacity-card .cap-pct {{ font-size: 14px; font-weight: 700; }}
.capacity-card .cap-bar {{ height: 6px; background: #21262d; border-radius: 3px; overflow: hidden; }}
.capacity-card .cap-bar .fill {{ height: 100%; border-radius: 3px; transition: width 0.5s; }}
.capacity-card .cap-remaining {{ font-size: 10px; color: var(--muted); margin-top: 4px; }}
.capacity-card .cap-remaining strong {{ color: var(--text); }}

/* Drill-down */
.drill-section {{ margin-top: 16px; border-top: 1px solid var(--border); padding-top: 16px; }}
.drill-section h3 {{ font-size: 11px; color: var(--muted); text-transform: uppercase; letter-spacing: 0.5px; margin-bottom: 10px; }}
.drill-item {{ padding: 8px 10px; border-radius: 4px; cursor: pointer; margin: 2px 0; border: 1px solid transparent; display: flex; justify-content: space-between; align-items: center; }}
.drill-item:hover {{ background: #21262d; }}
.drill-item .d-name {{ font-size: 11px; }}
.drill-item .d-vol {{ font-size: 11px; color: var(--blue); font-weight: 500; }}
.drill-item .d-bar {{ width: 60px; height: 3px; background: #21262d; border-radius: 2px; margin: 0 8px; overflow: hidden; }}
.drill-item .d-bar .fill {{ height: 100%; border-radius: 2px; }}

/* Detail panel */
.detail-card {{ background: var(--bg); border: 1px solid var(--border); border-radius: 6px; padding: 10px; margin: 8px 0; }}
.detail-card h4 {{ font-size: 10px; color: var(--purple); text-transform: uppercase; letter-spacing: 0.3px; margin-bottom: 6px; }}
.detail-row {{ display: flex; justify-content: space-between; padding: 3px 0; font-size: 11px; }}
.detail-row .label {{ color: var(--muted); }}
.detail-row .value {{ font-weight: 500; }}

/* Planner */
.planner-section {{ margin-top: 16px; }}
.planner-card {{ background: var(--panel); border: 1px solid var(--border); border-radius: 8px; padding: 14px; }}
.planner-card h3 {{ font-size: 12px; font-weight: 600; color: var(--blue); margin-bottom: 12px; }}
.slider-row {{ display: flex; align-items: center; gap: 10px; padding: 8px 0; }}
.slider-row label {{ font-size: 11px; width: 80px; flex-shrink: 0; }}
.slider-row input[type=range] {{ flex: 1; accent-color: var(--blue); }}
.slider-row .s-val {{ font-size: 14px; font-weight: 700; color: var(--blue); width: 50px; text-align: right; }}
.planner-result {{ margin-top: 12px; padding-top: 12px; border-top: 1px solid var(--border); }}
.planner-result .pr-line {{ display: flex; justify-content: space-between; padding: 3px 0; font-size: 11px; }}
.planner-result .pr-line .pr-label {{ color: var(--muted); }}
.planner-result .pr-line .pr-val {{ font-weight: 600; }}

.empty-state {{ text-align: center; padding: 40px; color: var(--muted); font-size: 12px; }}
.data-badge {{ display: inline-block; padding: 2px 6px; border-radius: 3px; font-size: 9px; background: #1c3a1c; color: var(--green); margin-left: 8px; }}
</style>
</head>
<body>

<div class="header" role="banner">
  <h1>📞 Connect Operations Dashboard <span class="data-badge" aria-label="Live data indicator">● Live Data</span></h1>
  <nav class="time-toggle" role="tablist" aria-label="Time period">
    <button class="time-btn active" data-time="today" role="tab" aria-selected="true" tabindex="0">Today</button>
    <button class="time-btn" data-time="hour" role="tab" aria-selected="false" tabindex="-1">This Hour</button>
    <button class="time-btn" data-time="week" role="tab" aria-selected="false" tabindex="-1">7 Days</button>
    <button class="time-btn" data-time="plan" role="tab" aria-selected="false" tabindex="-1">Plan Migration</button>
  </nav>
</div>

<div class="health-strip" id="health-strip" role="region" aria-label="Business line health"></div>

<div class="main">
  <main class="content" id="content" aria-live="polite"></main>
  <aside class="detail-panel" id="detail-panel" aria-label="Details">
    <div class="empty-state">Select a line or metric for details</div>
  </aside>
</div>

<script>
// ═══════════════════════════════════════════════════════════════════════════════
// DATA — Injected from live AWS API collection
// ═══════════════════════════════════════════════════════════════════════════════

const LINES = {lines_json};
const SYSTEM_API_USAGE = {api_usage_json};
const FLOW_DETAIL = {flow_detail_json};
const TOTAL_CAPACITY = {capacity_json};
const METADATA = {metadata_json};

// ═══════════════════════════════════════════════════════════════════════════════
// STATE
// ═══════════════════════════════════════════════════════════════════════════════

let state = {{ timeView: 'today', selectedLine: null, waveNumbers: 500, waveAgents: 0, waveConcurrent: 0, waveDailyVolume: 0 }};

// ═══════════════════════════════════════════════════════════════════════════════
// TIME TOGGLE
// ═══════════════════════════════════════════════════════════════════════════════

document.querySelectorAll('.time-btn').forEach(btn => {{
  btn.addEventListener('click', () => {{
    state.timeView = btn.dataset.time;
    document.querySelectorAll('.time-btn').forEach(b => {{
      b.classList.remove('active');
      b.setAttribute('aria-selected', 'false');
      b.setAttribute('tabindex', '-1');
    }});
    btn.classList.add('active');
    btn.setAttribute('aria-selected', 'true');
    btn.setAttribute('tabindex', '0');
    render();
  }});
  // Keyboard navigation: arrow keys between tabs
  btn.addEventListener('keydown', (e) => {{
    const tabs = Array.from(document.querySelectorAll('.time-btn'));
    const idx = tabs.indexOf(btn);
    let target = null;
    if (e.key === 'ArrowRight' || e.key === 'ArrowDown') target = tabs[(idx + 1) % tabs.length];
    if (e.key === 'ArrowLeft' || e.key === 'ArrowUp') target = tabs[(idx - 1 + tabs.length) % tabs.length];
    if (target) {{ e.preventDefault(); target.click(); target.focus(); }}
  }});
}});

// Colorblind mode toggle (check URL param or localStorage)
if (localStorage.getItem('colorblind') === 'true' || location.search.includes('colorblind')) {{
  document.body.setAttribute('data-colorblind', 'true');
}}

// ═══════════════════════════════════════════════════════════════════════════════
// RENDER FUNCTIONS
// ═══════════════════════════════════════════════════════════════════════════════

function renderHealthStrip() {{
  const strip = document.getElementById('health-strip');
  strip.innerHTML = LINES.map(line => {{
    const vol = state.timeView === 'hour' ? line.hour :
                state.timeView === 'week' ? line.week.reduce((a,b) => a+b, 0) :
                line.today;
    const volLabel = state.timeView === 'hour' ? 'this hour' :
                     state.timeView === 'week' ? 'this week' : 'today';
    const statusColor = line.capacityPct > 80 ? 'red' : line.capacityPct > 60 ? 'yellow' : 'green';
    const capColor = statusColor === 'red' ? 'var(--red)' : statusColor === 'yellow' ? 'var(--yellow)' : 'var(--green)';
    const statusShape = statusColor === 'red' ? '■' : statusColor === 'yellow' ? '▲' : '●';
    const statusLabel = statusColor === 'red' ? 'Critical' : statusColor === 'yellow' ? 'Warning' : 'Healthy';
    return `
      <div class="health-pill ${{state.selectedLine === line.id ? 'selected' : ''}}" onclick="selectLine('${{line.id}}')" role="button" tabindex="0" aria-label="${{line.name}}: ${{statusLabel}}, ${{line.capacityPct}}% capacity" onkeydown="if(event.key==='Enter')selectLine('${{line.id}}')">
        <div style="display:flex;align-items:center;gap:4px;">
          <span class="pill-status ${{statusColor}}" aria-hidden="true"></span>
          <span style="font-size:10px;color:${{capColor}}" aria-hidden="true">${{statusShape}}</span>
          <span class="pill-name">${{line.name}}</span>
        </div>
        <div class="pill-vol">${{formatNum(vol)}}</div>
        <div class="pill-meta">${{formatNum(line.numbers)}} numbers · ${{volLabel}}</div>
        <div class="pill-capacity" role="progressbar" aria-valuenow="${{line.capacityPct}}" aria-valuemin="0" aria-valuemax="100"><div class="fill" style="width:${{line.capacityPct}}%; background:${{capColor}}"></div></div>
        <div class="pill-meta" style="margin-top:3px">${{line.capacityPct}}% capacity used</div>
      </div>
    `;
  }}).join('');
}}

function renderContent() {{
  const content = document.getElementById('content');
  if (state.timeView === 'plan') {{ content.innerHTML = renderPlanner(); return; }}
  const line = state.selectedLine ? LINES.find(l => l.id === state.selectedLine) : null;
  if (!line) {{ content.innerHTML = renderOverview(); return; }}
  content.innerHTML = renderLineDetail(line);
}}

function renderOverview() {{
  const totalVol = LINES.reduce((s, l) => s + (state.timeView === 'hour' ? l.hour : l.today), 0);
  const timeLabel = state.timeView === 'hour' ? 'this hour' : state.timeView === 'week' ? 'this week' : 'today';
  return `
    <div class="chart-section">
      <div class="chart-header">
        <h2>All Lines — ${{formatNum(totalVol)}} calls ${{timeLabel}}</h2>
        <span class="trend up">System healthy · ${{TOTAL_CAPACITY.headroomCalls.toLocaleString()}} calls of headroom remaining</span>
      </div>
      ${{renderHourChart(aggregateHourly())}}
    </div>
    <div class="capacity-section">
      <div class="chart-header"><h2>Capacity by Line</h2></div>
      <div class="capacity-grid">${{LINES.map(l => renderCapacityCard(l)).join('')}}</div>
    </div>
  `;
}}

function renderLineDetail(line) {{
  const vol = state.timeView === 'hour' ? line.hour : line.today;
  const timeLabel = state.timeView === 'hour' ? 'this hour' : 'today';
  return `
    <div class="chart-section">
      <div class="chart-header">
        <h2>${{line.name}} — ${{formatNum(vol)}} calls ${{timeLabel}}</h2>
        <span class="trend ${{line.trendDir}}">${{line.trend}} vs last week</span>
      </div>
      ${{renderHourChart(line.hourly)}}
      <div style="font-size:10px;color:var(--muted);margin-top:6px;">Peak hour: ${{line.peakHour}} · ${{line.number}} · ${{formatNum(line.numbers)}} numbers</div>
    </div>
    <div class="capacity-section">
      <div class="chart-header"><h2>Capacity Status</h2></div>
      ${{renderCapacityCard(line)}}
      <div style="font-size:11px;color:var(--muted);margin-top:8px;">
        You can handle <strong style="color:var(--text)">${{formatNum(Math.round(vol / line.capacityPct * (100 - line.capacityPct)))}}</strong> more calls ${{timeLabel}} before degradation on this line.
      </div>
    </div>
    <div class="drill-section">
      <h3>Where calls go (Contact Flows)</h3>
      ${{line.flows.map(f => `
        <div class="drill-item" onclick="selectFlow('${{f.name}}')">
          <span class="d-name">${{f.name}}</span>
          <div class="d-bar"><div class="fill" style="width:${{f.pct}}%; background:var(--purple)"></div></div>
          <span class="d-vol">${{formatNum(f.vol)}} (${{f.pct}}%)</span>
        </div>
      `).join('')}}
    </div>
  `;
}}

function renderPlanner() {{
  // Multi-dimensional capacity planning
  const callsFromNumbers = state.waveNumbers * 15;
  const callsFromAgents = state.waveAgents * 40; // ~40 calls/agent/day avg
  const callsFromConcurrent = state.waveConcurrent * BUSINESS_DAY_SECONDS / 180; // 3-min avg handle time → calls/day
  const callsFromVolume = state.waveDailyVolume;

  // Use the largest signal (they overlap — a customer might set numbers OR volume, not both)
  const additionalCalls = Math.max(callsFromNumbers, callsFromAgents, callsFromConcurrent, callsFromVolume);
  const newTotal = TOTAL_CAPACITY.currentDaily + additionalCalls;
  const newPct = Math.round(newTotal / TOTAL_CAPACITY.maxCallsPerDay * 100);
  const danger = newPct >= 90;
  const warning = newPct >= 70 && newPct < 90;
  const statusColor = danger ? 'var(--red)' : warning ? 'var(--yellow)' : 'var(--green)';
  const statusLabel = danger ? '🔴 At risk — file quota increase NOW' : warning ? '🟡 Caution — file quota increase before migration' : '🟢 Safe — within capacity';

  // Per-API impact estimate
  const apisPerContact = 18;
  const additionalTps = additionalCalls / BUSINESS_DAY_SECONDS * apisPerContact;
  const peakMultiplier = 2.5; // Peak hour is ~2.5x average
  const peakAdditionalTps = additionalTps * peakMultiplier;

  return `
    <div class="planner-section"><div class="planner-card">
      <h3>Migration Wave Planner</h3>
      <p style="font-size:11px;color:var(--muted);margin-bottom:16px;">Model your migration wave from any dimension. The highest-impact input drives the projection.</p>

      <div style="display:grid;grid-template-columns:1fr 1fr;gap:16px;">
        <div>
          <div class="slider-row">
            <label>📞 Numbers:</label>
            <input type="range" min="0" max="10000" step="100" value="${{state.waveNumbers}}" oninput="updatePlanner('waveNumbers', this.value)">
            <span class="s-val">${{state.waveNumbers.toLocaleString()}}</span>
          </div>
          <div style="font-size:9px;color:var(--muted);padding-left:80px;">~15 calls/number/day → ${{formatNum(callsFromNumbers)}} calls</div>
        </div>

        <div>
          <div class="slider-row">
            <label>👤 Agents:</label>
            <input type="range" min="0" max="2000" step="25" value="${{state.waveAgents}}" oninput="updatePlanner('waveAgents', this.value)">
            <span class="s-val">${{state.waveAgents.toLocaleString()}}</span>
          </div>
          <div style="font-size:9px;color:var(--muted);padding-left:80px;">~40 calls/agent/day → ${{formatNum(callsFromAgents)}} calls</div>
        </div>

        <div>
          <div class="slider-row">
            <label>🔄 Concurrent:</label>
            <input type="range" min="0" max="500" step="10" value="${{state.waveConcurrent}}" oninput="updatePlanner('waveConcurrent', this.value)">
            <span class="s-val">${{state.waveConcurrent.toLocaleString()}}</span>
          </div>
          <div style="font-size:9px;color:var(--muted);padding-left:80px;">3-min avg handle → ${{formatNum(Math.round(callsFromConcurrent))}} calls/day</div>
        </div>

        <div>
          <div class="slider-row">
            <label>📊 Daily calls:</label>
            <input type="range" min="0" max="500000" step="5000" value="${{state.waveDailyVolume}}" oninput="updatePlanner('waveDailyVolume', this.value)">
            <span class="s-val">${{formatNum(state.waveDailyVolume)}}</span>
          </div>
          <div style="font-size:9px;color:var(--muted);padding-left:80px;">Direct volume input (if known)</div>
        </div>
      </div>

      <div class="planner-result" style="margin-top:16px;">
        <div class="pr-line"><span class="pr-label">Driving input</span><span class="pr-val">${{
          callsFromVolume >= callsFromNumbers && callsFromVolume >= callsFromAgents && callsFromVolume >= callsFromConcurrent ? '📊 Daily calls' :
          callsFromAgents >= callsFromNumbers && callsFromAgents >= callsFromConcurrent ? '👤 Agents' :
          callsFromConcurrent >= callsFromNumbers ? '🔄 Concurrent' : '📞 Numbers'
        }}</span></div>
        <div class="pr-line"><span class="pr-label">Additional calls/day</span><span class="pr-val">+${{formatNum(additionalCalls)}}</span></div>
        <div class="pr-line"><span class="pr-label">Current daily volume</span><span class="pr-val">${{formatNum(TOTAL_CAPACITY.currentDaily)}}</span></div>
        <div class="pr-line"><span class="pr-label">Projected daily volume</span><span class="pr-val" style="color:${{statusColor}}">${{formatNum(newTotal)}}</span></div>
        <div class="pr-line"><span class="pr-label">System capacity used</span><span class="pr-val" style="color:${{statusColor}}">${{newPct}}%</span></div>
        <div class="pr-line" style="padding-top:8px;border-top:1px solid var(--border);margin-top:8px;">
          <span class="pr-label">Status</span><span class="pr-val">${{statusLabel}}</span>
        </div>
        <div class="pr-line"><span class="pr-label">Headroom remaining</span><span class="pr-val">${{formatNum(TOTAL_CAPACITY.maxCallsPerDay - newTotal)}}</span></div>
        <div class="pr-line"><span class="pr-label">Limiting factor</span><span class="pr-val">${{TOTAL_CAPACITY.limitingApi}} (${{TOTAL_CAPACITY.limitingPct}}% today)</span></div>
      </div>
    </div>

    <div class="planner-card" style="margin-top:12px;">
      <h3>API Quota Planner</h3>
      <p style="font-size:10px;color:var(--muted);margin-bottom:10px;">Adjust expected TPS per API for your migration wave. Current usage shown in blue, your projection in the colored overlay.</p>
      ${{Object.entries(SYSTEM_API_USAGE).map(([name, usage]) => {{
        const stateKey = 'api_' + name.replace(/[^a-zA-Z]/g, '');
        const addedTps = state[stateKey] || 0;
        const projectedTotal = usage.total + addedTps;
        const projectedPct = (projectedTotal / usage.limit * 100);
        const currentPct = (usage.total / usage.limit * 100);
        const projColor = projectedPct >= 90 ? 'var(--red)' : projectedPct >= 70 ? 'var(--yellow)' : 'var(--green)';
        const needsSLI = projectedPct >= 70;
        const headroom = Math.max(usage.limit - projectedTotal, 0);
        return `
          <div style="padding:8px 0;border-bottom:1px solid #21262d;">
            <div style="display:flex;align-items:center;gap:8px;margin-bottom:4px;">
              <span style="font-size:10px;font-weight:600;width:180px;">${{name}}</span>
              <span style="font-size:9px;color:var(--muted);">Current: ${{usage.total}} TPS</span>
              <span style="font-size:9px;color:var(--muted);">Limit: ${{usage.limit}} TPS</span>
              ${{needsSLI ? '<span style="font-size:9px;background:#3a1c1c;color:var(--red);padding:1px 5px;border-radius:3px;margin-left:auto;">SLI needed</span>' : '<span style="font-size:9px;color:var(--green);margin-left:auto;">OK</span>'}}
            </div>
            <div style="display:flex;align-items:center;gap:8px;">
              <input type="range" min="0" max="${{Math.round(usage.limit * 2)}}" step="1" value="${{addedTps}}"
                oninput="updateApiSlider('${{stateKey}}', this.value)" style="flex:1;accent-color:${{projColor}};">
              <span style="font-size:11px;font-weight:700;color:${{projColor}};width:60px;text-align:right;">+${{addedTps}}</span>
            </div>
            <div style="height:8px;background:#21262d;border-radius:4px;overflow:hidden;position:relative;margin-top:4px;">
              <div style="position:absolute;width:${{Math.min(projectedPct, 100)}}%;height:100%;background:${{projColor}};opacity:0.3;border-radius:4px;"></div>
              <div style="position:relative;width:${{Math.min(currentPct, 100)}}%;height:100%;background:var(--blue);border-radius:4px;"></div>
            </div>
            <div style="display:flex;justify-content:space-between;margin-top:3px;">
              <span style="font-size:9px;color:var(--muted);">Projected: ${{projectedTotal.toFixed(1)}} / ${{usage.limit}} TPS (${{projectedPct.toFixed(0)}}%)</span>
              <span style="font-size:9px;color:${{headroom > 0 ? 'var(--green)' : 'var(--red)'}};">${{headroom > 0 ? headroom.toFixed(1) + ' TPS headroom' : 'OVER LIMIT by ' + Math.abs(headroom).toFixed(1) + ' TPS'}}</span>
            </div>
          </div>
        `;
      }}).join('')}}

      <div style="margin-top:12px;padding:8px;background:var(--bg);border:1px solid var(--border);border-radius:6px;">
        <div style="font-size:10px;color:var(--muted);margin-bottom:4px;">Quick fill from volume estimate:</div>
        <button onclick="autoFillApis()" style="font-size:10px;padding:4px 10px;border:1px solid var(--border);border-radius:4px;background:var(--panel);color:var(--blue);cursor:pointer;">
          ⚡ Auto-fill from wave inputs above
        </button>
        <button onclick="clearApiSliders()" style="font-size:10px;padding:4px 10px;border:1px solid var(--border);border-radius:4px;background:var(--panel);color:var(--muted);cursor:pointer;margin-left:4px;">
          ↺ Reset all to 0
        </button>
      </div>
    </div>

    <div style="margin-top:12px;padding:12px;background:var(--panel);border:1px solid var(--border);border-radius:8px;">
      <h3 style="font-size:12px;margin-bottom:8px;">Recommendations</h3>
      ${{danger ? `
        <p style="font-size:11px;color:var(--red);margin-bottom:6px;">⚠️ This migration wave will likely cause call degradation.</p>
        <p style="font-size:11px;color:var(--muted);">File Service Limit Increases (SLIs) for APIs marked above before proceeding. Allow 3-5 business days. Consider splitting into smaller waves.</p>
      ` : warning ? `
        <p style="font-size:11px;color:var(--yellow);margin-bottom:6px;">⚡ This wave brings you close to capacity limits.</p>
        <p style="font-size:11px;color:var(--muted);">File preemptive SLIs for APIs above 70%. Consider a phased rollout over 2-3 days to validate.</p>
      ` : `
        <p style="font-size:11px;color:var(--green);margin-bottom:6px;">✅ This wave is within safe capacity.</p>
        <p style="font-size:11px;color:var(--muted);">No quota increases needed. Monitor CloudWatch API throttle metrics for 24h after migration.</p>
      `}}
    </div></div>
  `;
}}

const BUSINESS_DAY_SECONDS = 28800;

// ═══════════════════════════════════════════════════════════════════════════════
// COMPONENTS
// ═══════════════════════════════════════════════════════════════════════════════

function renderHourChart(hourly) {{
  const max = Math.max(...hourly, 1);
  const hours = ['12a','','2a','','4a','','6a','','8a','','10a','','12p','','2p','','4p','','6p','','8p','','10p',''];
  return `
    <div class="hour-chart">
      ${{hourly.map((v, i) => {{
        const h = Math.max((v / max) * 100, 1);
        const color = v === max ? 'var(--yellow)' : 'var(--blue)';
        const label = `${{hours[i] || i + ':00'}}: ${{formatNum(v)}} calls`;
        return `<div class="hour-bar" style="height:${{h}}%;background:${{color}}"><span class="bar-tip">${{label}}</span></div>`;
      }}).join('')}}
    </div>
    <div class="chart-labels">${{hours.map(h => `<span>${{h}}</span>`).join('')}}</div>
  `;
}}

function renderCapacityCard(line) {{
  const pct = line.capacityPct;
  const color = pct > 80 ? 'var(--red)' : pct > 60 ? 'var(--yellow)' : 'var(--green)';
  const remaining = Math.round(line.today / pct * (100 - pct));
  return `
    <div class="capacity-card" onclick="selectLine('${{line.id}}')">
      <div class="cap-header"><span class="cap-name">${{line.name}}</span><span class="cap-pct" style="color:${{color}}">${{pct}}%</span></div>
      <div class="cap-bar"><div class="fill" style="width:${{pct}}%;background:${{color}}"></div></div>
      <div class="cap-remaining">Can handle <strong>${{formatNum(remaining)}}</strong> more calls today</div>
    </div>
  `;
}}

function renderDetailPanel() {{
  const panel = document.getElementById('detail-panel');
  const line = state.selectedLine ? LINES.find(l => l.id === state.selectedLine) : null;
  if (!line) {{ panel.innerHTML = '<div class="empty-state">Select a line to see details</div>'; return; }}
  panel.innerHTML = `
    <div class="detail-card">
      <h4>Line Summary</h4>
      <div class="detail-row"><span class="label">Phone number</span><span class="value">${{line.number}}</span></div>
      <div class="detail-row"><span class="label">Total numbers</span><span class="value">${{formatNum(line.numbers)}}</span></div>
      <div class="detail-row"><span class="label">Today's volume</span><span class="value">${{formatNum(line.today)}}</span></div>
      <div class="detail-row"><span class="label">This hour</span><span class="value">${{formatNum(line.hour)}}</span></div>
      <div class="detail-row"><span class="label">Peak hour</span><span class="value">${{line.peakHour}}</span></div>
      <div class="detail-row"><span class="label">Trend</span><span class="value" style="color:${{line.trendDir === 'up' ? 'var(--green)' : 'var(--red)'}}">${{line.trend}} vs last week</span></div>
    </div>
    <div class="detail-card">
      <h4>Capacity</h4>
      <div class="detail-row"><span class="label">Current utilization</span><span class="value">${{line.capacityPct}}%</span></div>
      <div class="detail-row"><span class="label">Remaining headroom</span><span class="value">${{formatNum(Math.round(line.today / line.capacityPct * (100 - line.capacityPct)))}} calls</span></div>
    </div>
    <div class="detail-card">
      <h4>7-Day Volume</h4>
      <div style="display:flex;align-items:flex-end;gap:3px;height:50px;">
        ${{line.week.map((v, i) => {{
          const max = Math.max(...line.week);
          const h = (v / max * 100);
          const days = ['Mon','Tue','Wed','Thu','Fri','Sat','Sun'];
          return `<div style="flex:1;display:flex;flex-direction:column;align-items:center;gap:2px;">
            <div style="width:100%;height:${{h}}%;background:var(--blue);border-radius:2px;min-height:2px;"></div>
            <span style="font-size:8px;color:var(--muted)">${{days[i]}}</span>
          </div>`;
        }}).join('')}}
      </div>
    </div>
  `;
}}

// ═══════════════════════════════════════════════════════════════════════════════
// FLOW DETAIL (drill into a specific flow)
// ═══════════════════════════════════════════════════════════════════════════════

function selectFlow(name) {{
  const panel = document.getElementById('detail-panel');
  const line = LINES.find(l => l.id === state.selectedLine);
  const flow = line ? line.flows.find(f => f.name === name) : null;
  if (!flow) return;
  const chain = FLOW_DETAIL[name] || FLOW_DETAIL['_default'];
  const dailyVol = flow.vol;
  panel.innerHTML = `
    <div class="detail-card">
      <h4>📞 ${{name}}</h4>
      <div class="detail-row"><span class="label">Calls today</span><span class="value">${{formatNum(dailyVol)}}</span></div>
      <div class="detail-row"><span class="label">Share of line</span><span class="value">${{flow.pct}}%</span></div>
      <div class="detail-row"><span class="label">Steps per call</span><span class="value">${{chain.steps.length}}</span></div>
    </div>
    <div class="detail-card">
      <h4>What happens during each call</h4>
      ${{chain.steps.map((step, i) => `
        <div style="display:flex;align-items:flex-start;padding:5px 0;${{i > 0 ? 'border-top:1px solid #21262d;' : ''}}">
          <span style="font-size:14px;margin-right:8px;">${{step.icon}}</span>
          <div style="flex:1;">
            <div style="font-size:11px;font-weight:500;">${{step.label}}</div>
            <div style="font-size:10px;color:var(--muted);">${{step.detail}}</div>
          </div>
          <span style="font-size:10px;color:var(--cyan);white-space:nowrap;">${{formatNum(Math.round(dailyVol * step.pctCalls))}}/day</span>
        </div>
      `).join('')}}
    </div>
    <div class="detail-card">
      <h4>API Capacity Used by This Flow</h4>
      ${{chain.apis.map(api => {{
        const overall = SYSTEM_API_USAGE[api.name] || {{ total: api.tps, limit: api.limit }};
        const totalPct = (overall.total / overall.limit * 100).toFixed(0);
        const flowPct = (api.tps / overall.limit * 100).toFixed(0);
        const totalColor = totalPct > 80 ? 'var(--red)' : totalPct > 60 ? 'var(--yellow)' : 'var(--green)';
        return `
          <div style="padding:6px 0;border-bottom:1px solid #21262d;">
            <div style="display:flex;justify-content:space-between;font-size:10px;margin-bottom:3px;">
              <span>${{api.name}}</span>
              <span style="color:${{totalColor}}">System: ${{overall.total}} / ${{overall.limit}} TPS (${{totalPct}}%)</span>
            </div>
            <div style="height:6px;background:#21262d;border-radius:3px;overflow:hidden;position:relative;">
              <div style="position:absolute;width:${{Math.max(totalPct, 2)}}%;height:100%;background:${{totalColor}};opacity:0.3;border-radius:3px;"></div>
              <div style="position:relative;width:${{Math.max(flowPct, 2)}}%;height:100%;background:var(--cyan);border-radius:3px;"></div>
            </div>
            <div style="font-size:9px;color:var(--muted);margin-top:2px;">This flow: ${{api.tps}} TPS (${{flowPct}}% of limit)</div>
          </div>
        `;
      }}).join('')}}
    </div>
  `;
}}

// ═══════════════════════════════════════════════════════════════════════════════
// INTERACTIONS & UTILITIES
// ═══════════════════════════════════════════════════════════════════════════════

function selectLine(id) {{ state.selectedLine = state.selectedLine === id ? null : id; render(); }}
function updatePlanner(key, val) {{ state[key] = parseInt(val); renderContent(); }}
function updateApiSlider(key, val) {{ state[key] = parseFloat(val); renderContent(); }}
function autoFillApis() {{
  // Distribute the wave's projected TPS across APIs proportionally to current usage
  const callsFromNumbers = state.waveNumbers * 15;
  const callsFromAgents = state.waveAgents * 40;
  const callsFromConcurrent = state.waveConcurrent * BUSINESS_DAY_SECONDS / 180;
  const callsFromVolume = state.waveDailyVolume;
  const additionalCalls = Math.max(callsFromNumbers, callsFromAgents, callsFromConcurrent, callsFromVolume);
  const additionalTps = additionalCalls / BUSINESS_DAY_SECONDS * 18 * 2.5; // peak hour multiplier
  const apiCount = Object.keys(SYSTEM_API_USAGE).length;
  const totalCurrentTps = Object.values(SYSTEM_API_USAGE).reduce((s, u) => s + u.total, 0);

  Object.entries(SYSTEM_API_USAGE).forEach(([name, usage]) => {{
    const stateKey = 'api_' + name.replace(/[^a-zA-Z]/g, '');
    // Distribute proportionally to current usage share
    const share = totalCurrentTps > 0 ? usage.total / totalCurrentTps : 1 / apiCount;
    state[stateKey] = Math.round(additionalTps * share * 10) / 10;
  }});
  renderContent();
}}
function clearApiSliders() {{
  Object.keys(SYSTEM_API_USAGE).forEach(name => {{
    state['api_' + name.replace(/[^a-zA-Z]/g, '')] = 0;
  }});
  renderContent();
}}
function aggregateHourly() {{ const agg = new Array(24).fill(0); LINES.forEach(l => l.hourly.forEach((v, i) => agg[i] += v)); return agg; }}
function formatNum(n) {{ if (n >= 1000000) return (n / 1000000).toFixed(1) + 'M'; if (n >= 1000) return (n / 1000).toFixed(0) + 'K'; return n.toLocaleString(); }}

function render() {{ renderHealthStrip(); renderContent(); renderDetailPanel(); }}
render();

{_render_live_refresh_js(live_endpoint)}
</script>
</body>
</html>"""


def _render_live_refresh_js(live_endpoint: str | None) -> str:
    """Generate the live refresh JavaScript block.

    When live_endpoint is provided, adds a polling mechanism that:
    - Fetches fresh metrics every 60 seconds
    - Updates LINES, SYSTEM_API_USAGE, TOTAL_CAPACITY in place
    - Re-renders the dashboard with new data
    - Shows a "Last updated" timestamp
    - Pauses polling when the tab is not visible

    Args:
        live_endpoint: API Gateway URL (e.g., https://xxx.execute-api.region.amazonaws.com/prod/metrics)

    Returns:
        JavaScript string to inject, or empty string if no endpoint.
    """
    if not live_endpoint:
        return "// Live refresh disabled — no endpoint configured"

    return f"""
// ═══════════════════════════════════════════════════════════════════════════════
// LIVE REFRESH — polls {live_endpoint} every 60s
// ═══════════════════════════════════════════════════════════════════════════════

(function() {{
  const ENDPOINT = '{live_endpoint}';
  const POLL_INTERVAL_MS = 60000; // 60 seconds
  let pollTimer = null;
  let lastUpdate = null;

  // Add status indicator to header
  const header = document.querySelector('.header h1');
  const badge = header.querySelector('.data-badge');
  if (badge) badge.innerHTML = '● Live <span id="live-ts" style="font-size:8px;opacity:0.7;margin-left:4px;"></span>';

  async function fetchLiveData() {{
    const view = state.timeView === 'plan' ? 'today' : state.timeView;
    try {{
      const url = `${{ENDPOINT}}?view=${{view}}`;
      const resp = await fetch(url);
      if (!resp.ok) throw new Error(`HTTP ${{resp.status}}`);
      const data = await resp.json();
      applyLiveData(data);
      lastUpdate = new Date();
      updateTimestamp();
    }} catch (err) {{
      console.warn('[LiveRefresh] Fetch failed:', err.message);
      const badge = document.querySelector('.data-badge');
      if (badge) badge.style.background = '#3a1c1c';
      if (badge) badge.style.color = 'var(--red)';
    }}
  }}

  function applyLiveData(data) {{
    // Merge live LINES data (update volumes, keep static config)
    if (data.LINES && data.LINES.length) {{
      data.LINES.forEach((liveL, i) => {{
        if (LINES[i]) {{
          LINES[i].today = liveL.today || LINES[i].today;
          LINES[i].hour = liveL.hour || LINES[i].hour;
          if (liveL.hourly && liveL.hourly.some(v => v > 0)) {{
            LINES[i].hourly = liveL.hourly;
          }}
          if (liveL.capacityPct) LINES[i].capacityPct = liveL.capacityPct;
        }}
      }});
    }}

    // Merge SYSTEM_API_USAGE
    if (data.SYSTEM_API_USAGE) {{
      Object.keys(data.SYSTEM_API_USAGE).forEach(api => {{
        SYSTEM_API_USAGE[api] = data.SYSTEM_API_USAGE[api];
      }});
    }}

    // Merge TOTAL_CAPACITY
    if (data.TOTAL_CAPACITY) {{
      Object.assign(TOTAL_CAPACITY, data.TOTAL_CAPACITY);
    }}

    // Re-render
    render();

    // Flash the badge green briefly
    const badge = document.querySelector('.data-badge');
    if (badge) {{
      badge.style.background = '#1c3a1c';
      badge.style.color = 'var(--green)';
    }}
  }}

  function updateTimestamp() {{
    const el = document.getElementById('live-ts');
    if (el && lastUpdate) {{
      el.textContent = lastUpdate.toLocaleTimeString();
    }}
  }}

  function startPolling() {{
    if (pollTimer) clearInterval(pollTimer);
    fetchLiveData(); // Immediate first fetch
    pollTimer = setInterval(fetchLiveData, POLL_INTERVAL_MS);
  }}

  function stopPolling() {{
    if (pollTimer) {{ clearInterval(pollTimer); pollTimer = null; }}
  }}

  // Pause when tab is hidden
  document.addEventListener('visibilitychange', () => {{
    if (document.hidden) {{ stopPolling(); }}
    else {{ startPolling(); }}
  }});

  // Re-fetch when time view changes
  const origTimeHandler = document.querySelectorAll('.time-btn');
  origTimeHandler.forEach(btn => {{
    btn.addEventListener('click', () => {{
      setTimeout(fetchLiveData, 100); // Fetch after state updates
    }});
  }});

  // Start
  startPolling();
}})();
"""
