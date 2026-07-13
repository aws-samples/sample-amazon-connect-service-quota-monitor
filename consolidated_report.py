#!/usr/bin/env python3
# SPDX-License-Identifier: MIT-0
"""Consolidated API Report — all APIs, call counts, and quota usage in one page.

Generates a single-page HTML report showing:
  - Every API called across all contact flows
  - Per-flow API breakdown with call counts per contact
  - System-wide aggregate TPS and quota utilization
  - Lambda functions and which APIs they trigger
  - Sortable tables for easy reference

Author: Amazon.com, Inc.
License: MIT-0
"""

from __future__ import annotations

import json
import logging
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)


def generate_consolidated_report(
    resource_map: dict[str, Any],
    model: dict[str, Any],
    output_path: str,
) -> None:
    """Generate the consolidated API report HTML.

    Args:
        resource_map: Full resource graph from collection phase.
        model: Quota impact model from build phase.
        output_path: File path for the generated HTML file.
    """
    flows = resource_map.get("contact_flows", [])
    lambdas = resource_map.get("lambda_functions", [])
    numbers = resource_map.get("phone_numbers", [])
    metrics = resource_map.get("usage_metrics", {})
    quotas = model.get("quota_headroom", {})

    # Build consolidated data
    all_apis = _aggregate_apis_across_flows(flows)
    per_flow = _build_per_flow_table(flows)
    quota_table = _build_quota_table(quotas, metrics)
    lambda_table = _build_lambda_table(lambdas, flows)
    summary = _build_summary(flows, lambdas, numbers, all_apis, quotas)

    html = _render_report_html(all_apis, per_flow, quota_table, lambda_table, summary, resource_map)

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html)

    logger.info("Consolidated report generated: %s", output_path)


def generate_consolidated_report_string(
    resource_map: dict[str, Any],
    model: dict[str, Any],
) -> str:
    """Generate the consolidated API report and return as HTML string.

    Same output as generate_consolidated_report but returns the HTML
    instead of writing to a file. Used by the Lambda for S3 upload.
    """
    flows = resource_map.get("contact_flows", [])
    lambdas = resource_map.get("lambda_functions", [])
    numbers = resource_map.get("phone_numbers", [])
    metrics = resource_map.get("usage_metrics", {})
    quotas = model.get("quota_headroom", {})

    all_apis = _aggregate_apis_across_flows(flows)
    per_flow = _build_per_flow_table(flows)
    quota_table = _build_quota_table(quotas, metrics)
    lambda_table = _build_lambda_table(lambdas, flows)
    summary = _build_summary(flows, lambdas, numbers, all_apis, quotas)

    return _render_report_html(all_apis, per_flow, quota_table, lambda_table, summary, resource_map)


def _aggregate_apis_across_flows(flows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Aggregate all API actions across all flows into a single sorted list."""
    api_totals: dict[str, dict[str, Any]] = {}

    for flow in flows:
        flow_name = flow.get("Name", flow.get("name", "Unknown"))
        api_actions = flow.get("api_actions", [])

        for action in api_actions:
            api = action.get("api")
            if api is None:
                continue

            action_type = action.get("type", "")
            count = action.get("count", 1)

            if api not in api_totals:
                api_totals[api] = {
                    "api": api,
                    "action_types": set(),
                    "total_calls_per_contact": 0,
                    "flows_using": set(),
                    "max_in_single_flow": 0,
                }

            api_totals[api]["action_types"].add(action_type)
            api_totals[api]["total_calls_per_contact"] += count
            api_totals[api]["flows_using"].add(flow_name)
            api_totals[api]["max_in_single_flow"] = max(
                api_totals[api]["max_in_single_flow"], count
            )

    # Convert sets to lists for serialization and sort by total calls
    result = []
    for api, data in sorted(api_totals.items(), key=lambda x: -x[1]["total_calls_per_contact"]):
        result.append({
            "api": api,
            "action_types": sorted(data["action_types"]),
            "total_calls_per_contact": data["total_calls_per_contact"],
            "flows_using": sorted(data["flows_using"]),
            "flow_count": len(data["flows_using"]),
            "max_in_single_flow": data["max_in_single_flow"],
        })

    return result


def _build_per_flow_table(flows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Build per-flow API breakdown."""
    result = []

    for flow in flows:
        flow_name = flow.get("Name", flow.get("name", "Unknown"))
        flow_type = flow.get("Type", flow.get("type", ""))
        api_actions = flow.get("api_actions", [])
        lambdas = flow.get("lambdas_invoked", [])

        if not api_actions:
            continue

        apis_in_flow = []
        total_api_calls = 0

        for action in api_actions:
            api = action.get("api")
            if api is None:
                continue
            count = action.get("count", 1)
            total_api_calls += count
            apis_in_flow.append({
                "action_type": action.get("type", ""),
                "api": api,
                "count": count,
            })

        result.append({
            "flow_name": flow_name,
            "flow_type": flow_type,
            "total_api_calls_per_contact": total_api_calls,
            "unique_apis": len(set(a["api"] for a in apis_in_flow)),
            "lambda_count": len(lambdas),
            "apis": sorted(apis_in_flow, key=lambda x: -x["count"]),
        })

    return sorted(result, key=lambda x: -x["total_api_calls_per_contact"])


def _build_quota_table(quotas: dict[str, Any], metrics: dict[str, Any]) -> list[dict[str, Any]]:
    """Build quota utilization table."""
    result = []

    for code, quota in quotas.items():
        name = quota.get("name", "")
        api_name = name.replace("Rate of ", "").replace(" API requests", "")
        limit = quota.get("limit", 0)
        peak_tps = quota.get("peak_tps", 0)
        utilization = quota.get("utilization_pct", 0)
        headroom = quota.get("headroom_tps", 0)

        # Get daily volume from metrics
        api_metrics = metrics.get(api_name, {})
        avg_daily = api_metrics.get("avg_daily", 0)
        peak_daily = api_metrics.get("peak_daily", 0)

        result.append({
            "api_name": api_name,
            "quota_code": code,
            "limit_tps": limit,
            "current_peak_tps": peak_tps,
            "utilization_pct": utilization,
            "headroom_tps": headroom,
            "avg_daily_calls": int(avg_daily),
            "peak_daily_calls": int(peak_daily),
            "status": "🔴 Critical" if utilization > 85 else "🟡 Warning" if utilization > 70 else "🟢 OK",
        })

    return sorted(result, key=lambda x: -x["utilization_pct"])


def _build_lambda_table(lambdas: list[dict[str, Any]], flows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Build Lambda function table with flow associations."""
    # Map Lambda ARN → which flows invoke it
    lambda_to_flows: dict[str, list[str]] = defaultdict(list)
    for flow in flows:
        flow_name = flow.get("Name", flow.get("name", "Unknown"))
        for arn in flow.get("lambdas_invoked", []):
            lambda_to_flows[arn].append(flow_name)

    result = []
    for lam in lambdas:
        if not isinstance(lam, dict):
            continue
        arn = lam.get("FunctionArn", "")
        result.append({
            "name": lam.get("FunctionName", "Unknown"),
            "arn": arn,
            "runtime": lam.get("Runtime", "unknown"),
            "memory_mb": lam.get("MemorySize", 0),
            "timeout_sec": lam.get("Timeout", 0),
            "provisioned_concurrency": len(lam.get("ProvisionedConcurrency", [])) > 0,
            "invoked_by_flows": lambda_to_flows.get(arn, []),
            "flow_count": len(lambda_to_flows.get(arn, [])),
        })

    return sorted(result, key=lambda x: -x["flow_count"])


def _build_summary(
    flows: list[dict[str, Any]],
    lambdas: list[dict[str, Any]],
    numbers: list[dict[str, Any]],
    all_apis: list[dict[str, Any]],
    quotas: dict[str, Any],
) -> dict[str, Any]:
    """Build summary statistics."""
    total_api_calls = sum(a["total_calls_per_contact"] for a in all_apis)
    flows_with_actions = sum(1 for f in flows if f.get("api_actions"))
    critical_quotas = sum(1 for q in quotas.values() if q.get("utilization_pct", 0) > 85)
    warning_quotas = sum(1 for q in quotas.values() if 70 < q.get("utilization_pct", 0) <= 85)

    return {
        "total_phone_numbers": len(numbers),
        "total_flows": len(flows),
        "flows_with_api_data": flows_with_actions,
        "total_lambdas": len(lambdas),
        "unique_apis_called": len(all_apis),
        "total_api_calls_per_contact": total_api_calls,
        "critical_quotas": critical_quotas,
        "warning_quotas": warning_quotas,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


def _render_report_html(
    all_apis: list[dict[str, Any]],
    per_flow: list[dict[str, Any]],
    quota_table: list[dict[str, Any]],
    lambda_table: list[dict[str, Any]],
    summary: dict[str, Any],
    resource_map: dict[str, Any],
) -> str:
    """Render the full consolidated report HTML."""

    all_apis_json = json.dumps(all_apis, default=str)
    per_flow_json = json.dumps(per_flow, default=str)
    quota_json = json.dumps(quota_table, default=str)
    lambda_json = json.dumps(lambda_table, default=str)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Connect API Consolidated Report</title>
<style>
:root {{
  --bg: #0d1117; --panel: #161b22; --border: #30363d; --text: #c9d1d9;
  --muted: #8b949e; --blue: #58a6ff; --green: #3fb950; --yellow: #d29922;
  --red: #f85149; --purple: #bc8cff; --cyan: #39c5cf;
}}
* {{ margin: 0; padding: 0; box-sizing: border-box; }}
body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; background: var(--bg); color: var(--text); font-size: 13px; padding: 24px; line-height: 1.5; }}
h1 {{ font-size: 18px; color: var(--blue); margin-bottom: 4px; }}
h2 {{ font-size: 14px; color: var(--purple); margin: 24px 0 12px; padding-bottom: 6px; border-bottom: 1px solid var(--border); }}
.meta {{ font-size: 11px; color: var(--muted); margin-bottom: 24px; }}
.summary-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 10px; margin-bottom: 24px; }}
.stat {{ background: var(--panel); border: 1px solid var(--border); border-radius: 6px; padding: 12px; text-align: center; }}
.stat .num {{ font-size: 24px; font-weight: 700; color: var(--blue); }}
.stat .lbl {{ font-size: 10px; color: var(--muted); margin-top: 2px; }}
.stat.critical .num {{ color: var(--red); }}
.stat.warning .num {{ color: var(--yellow); }}

table {{ width: 100%; border-collapse: collapse; font-size: 12px; margin-bottom: 16px; }}
th {{ background: var(--panel); color: var(--blue); padding: 8px 10px; text-align: left; font-weight: 600; border-bottom: 2px solid var(--border); cursor: pointer; user-select: none; }}
th:hover {{ color: var(--cyan); }}
td {{ padding: 6px 10px; border-bottom: 1px solid var(--border); }}
tr:hover td {{ background: #1c2128; }}
.bar {{ height: 6px; background: #21262d; border-radius: 3px; overflow: hidden; min-width: 60px; }}
.bar .fill {{ height: 100%; border-radius: 3px; }}
.tag {{ display: inline-block; padding: 1px 6px; border-radius: 3px; font-size: 10px; margin: 1px 2px; background: #21262d; }}
.tag.critical {{ background: #3a1c1c; color: var(--red); }}
.tag.warning {{ background: #3a2f1c; color: var(--yellow); }}
.tag.ok {{ background: #1c3a1c; color: var(--green); }}

.tabs {{ display: flex; gap: 2px; margin-bottom: 16px; background: var(--panel); border-radius: 6px; padding: 3px; width: fit-content; }}
.tab {{ padding: 6px 14px; border-radius: 4px; cursor: pointer; font-size: 11px; color: var(--muted); border: none; background: none; }}
.tab.active {{ background: var(--blue); color: #fff; }}
.tab:hover:not(.active) {{ color: var(--text); }}
.tab-content {{ display: none; }}
.tab-content.active {{ display: block; }}

.search {{ margin-bottom: 12px; }}
.search input {{ background: var(--panel); border: 1px solid var(--border); border-radius: 4px; padding: 6px 10px; color: var(--text); font-size: 12px; width: 300px; }}
.search input:focus {{ outline: none; border-color: var(--blue); }}

@media print {{
  body {{ background: white; color: black; }}
  .tabs, .search {{ display: none; }}
  .tab-content {{ display: block !important; page-break-inside: avoid; }}
  th {{ background: #f0f0f0; color: black; }}
  td {{ border-color: #ddd; }}
}}
</style>
</head>
<body>

<h1>📋 Connect API Consolidated Report</h1>
<div class="meta">
  Instance: {resource_map.get("instance_id", "")} &nbsp;|&nbsp;
  Region: {resource_map.get("region", "")} &nbsp;|&nbsp;
  Generated: {summary["generated_at"]}
</div>

<div class="summary-grid">
  <div class="stat"><div class="num">{summary["total_phone_numbers"]:,}</div><div class="lbl">Phone Numbers</div></div>
  <div class="stat"><div class="num">{summary["total_flows"]}</div><div class="lbl">Contact Flows</div></div>
  <div class="stat"><div class="num">{summary["unique_apis_called"]}</div><div class="lbl">Unique APIs Called</div></div>
  <div class="stat"><div class="num">{summary["total_api_calls_per_contact"]}</div><div class="lbl">API Calls / Contact</div></div>
  <div class="stat"><div class="num">{summary["total_lambdas"]}</div><div class="lbl">Lambda Functions</div></div>
  <div class="stat {'critical' if summary['critical_quotas'] > 0 else ''}"><div class="num">{summary["critical_quotas"]}</div><div class="lbl">Quotas &gt; 85%</div></div>
  <div class="stat {'warning' if summary['warning_quotas'] > 0 else ''}"><div class="num">{summary["warning_quotas"]}</div><div class="lbl">Quotas 70-85%</div></div>
</div>

<div class="tabs">
  <button class="tab active" onclick="showTab('apis')">All APIs</button>
  <button class="tab" onclick="showTab('flows')">Per Flow</button>
  <button class="tab" onclick="showTab('quotas')">Quotas</button>
  <button class="tab" onclick="showTab('lambdas')">Lambdas</button>
</div>

<div class="search"><input type="text" placeholder="Filter..." oninput="filterTable(this.value)"></div>

<!-- ALL APIs TAB -->
<div class="tab-content active" id="tab-apis">
<h2>All APIs Called Across Flows ({len(all_apis)} unique)</h2>
<table id="table-apis">
<thead><tr>
  <th onclick="sortTable('table-apis', 0)">API</th>
  <th onclick="sortTable('table-apis', 1)">Action Types</th>
  <th onclick="sortTable('table-apis', 2)">Calls/Contact</th>
  <th onclick="sortTable('table-apis', 3)">Flows Using</th>
  <th onclick="sortTable('table-apis', 4)">Max in 1 Flow</th>
</tr></thead>
<tbody id="tbody-apis"></tbody>
</table>
</div>

<!-- PER FLOW TAB -->
<div class="tab-content" id="tab-flows">
<h2>API Breakdown Per Contact Flow</h2>
<table id="table-flows">
<thead><tr>
  <th onclick="sortTable('table-flows', 0)">Flow Name</th>
  <th onclick="sortTable('table-flows', 1)">Type</th>
  <th onclick="sortTable('table-flows', 2)">APIs/Contact</th>
  <th onclick="sortTable('table-flows', 3)">Unique APIs</th>
  <th onclick="sortTable('table-flows', 4)">Lambdas</th>
  <th>API Details</th>
</tr></thead>
<tbody id="tbody-flows"></tbody>
</table>
</div>

<!-- QUOTAS TAB -->
<div class="tab-content" id="tab-quotas">
<h2>Service Quota Utilization</h2>
<table id="table-quotas">
<thead><tr>
  <th onclick="sortTable('table-quotas', 0)">API</th>
  <th onclick="sortTable('table-quotas', 1)">Limit (TPS)</th>
  <th onclick="sortTable('table-quotas', 2)">Peak TPS</th>
  <th onclick="sortTable('table-quotas', 3)">Utilization</th>
  <th>Headroom</th>
  <th onclick="sortTable('table-quotas', 5)">Avg Daily</th>
  <th onclick="sortTable('table-quotas', 6)">Peak Daily</th>
  <th>Status</th>
</tr></thead>
<tbody id="tbody-quotas"></tbody>
</table>
</div>

<!-- LAMBDAS TAB -->
<div class="tab-content" id="tab-lambdas">
<h2>Lambda Functions</h2>
<table id="table-lambdas">
<thead><tr>
  <th onclick="sortTable('table-lambdas', 0)">Function Name</th>
  <th onclick="sortTable('table-lambdas', 1)">Runtime</th>
  <th onclick="sortTable('table-lambdas', 2)">Memory</th>
  <th onclick="sortTable('table-lambdas', 3)">Timeout</th>
  <th onclick="sortTable('table-lambdas', 4)">PC</th>
  <th onclick="sortTable('table-lambdas', 5)">Flows</th>
  <th>Invoked By</th>
</tr></thead>
<tbody id="tbody-lambdas"></tbody>
</table>
</div>

<script>
const ALL_APIS = {all_apis_json};
const PER_FLOW = {per_flow_json};
const QUOTAS = {quota_json};
const LAMBDAS = {lambda_json};

function renderApis() {{
  document.getElementById('tbody-apis').innerHTML = ALL_APIS.map(a => `
    <tr>
      <td style="font-weight:600;">${{a.api}}</td>
      <td>${{a.action_types.map(t => `<span class="tag">${{t}}</span>`).join('')}}</td>
      <td style="text-align:right;font-weight:600;">${{a.total_calls_per_contact}}</td>
      <td style="text-align:right;">${{a.flow_count}}</td>
      <td style="text-align:right;">${{a.max_in_single_flow}}</td>
    </tr>
  `).join('');
}}

function renderFlows() {{
  document.getElementById('tbody-flows').innerHTML = PER_FLOW.map(f => `
    <tr>
      <td style="font-weight:600;">${{f.flow_name}}</td>
      <td style="font-size:10px;color:var(--muted);">${{f.flow_type}}</td>
      <td style="text-align:right;font-weight:600;">${{f.total_api_calls_per_contact}}</td>
      <td style="text-align:right;">${{f.unique_apis}}</td>
      <td style="text-align:right;">${{f.lambda_count}}</td>
      <td style="font-size:10px;">${{f.apis.map(a => `${{a.api.split(':').pop()}} ×${{a.count}}`).join(', ')}}</td>
    </tr>
  `).join('');
}}

function renderQuotas() {{
  document.getElementById('tbody-quotas').innerHTML = QUOTAS.map(q => {{
    const color = q.utilization_pct > 85 ? 'var(--red)' : q.utilization_pct > 70 ? 'var(--yellow)' : 'var(--green)';
    const statusClass = q.utilization_pct > 85 ? 'critical' : q.utilization_pct > 70 ? 'warning' : 'ok';
    return `
      <tr>
        <td style="font-weight:600;">${{q.api_name}}</td>
        <td style="text-align:right;">${{q.limit_tps}}</td>
        <td style="text-align:right;">${{q.current_peak_tps}}</td>
        <td>
          <div style="display:flex;align-items:center;gap:6px;">
            <div class="bar" style="flex:1;"><div class="fill" style="width:${{Math.min(q.utilization_pct, 100)}}%;background:${{color}};"></div></div>
            <span style="font-size:11px;color:${{color}};font-weight:600;width:40px;text-align:right;">${{q.utilization_pct.toFixed(1)}}%</span>
          </div>
        </td>
        <td style="text-align:right;">${{q.headroom_tps.toFixed(1)}} TPS</td>
        <td style="text-align:right;">${{q.avg_daily_calls.toLocaleString()}}</td>
        <td style="text-align:right;">${{q.peak_daily_calls.toLocaleString()}}</td>
        <td><span class="tag ${{statusClass}}">${{q.status}}</span></td>
      </tr>
    `;
  }}).join('');
}}

function renderLambdas() {{
  document.getElementById('tbody-lambdas').innerHTML = LAMBDAS.map(l => `
    <tr>
      <td style="font-weight:600;">${{l.name}}</td>
      <td>${{l.runtime}}</td>
      <td style="text-align:right;">${{l.memory_mb}} MB</td>
      <td style="text-align:right;">${{l.timeout_sec}}s</td>
      <td>${{l.provisioned_concurrency ? '<span class="tag ok">Yes</span>' : '<span class="tag">No</span>'}}</td>
      <td style="text-align:right;">${{l.flow_count}}</td>
      <td style="font-size:10px;">${{l.invoked_by_flows.join(', ') || '—'}}</td>
    </tr>
  `).join('');
}}

// Tab switching
function showTab(name) {{
  document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
  document.querySelectorAll('.tab-content').forEach(t => t.classList.remove('active'));
  event.target.classList.add('active');
  document.getElementById('tab-' + name).classList.add('active');
}}

// Table sorting
function sortTable(tableId, colIdx) {{
  const table = document.getElementById(tableId);
  const tbody = table.querySelector('tbody');
  const rows = Array.from(tbody.querySelectorAll('tr'));
  const dir = table.dataset.sortDir === 'asc' ? 'desc' : 'asc';
  table.dataset.sortDir = dir;

  rows.sort((a, b) => {{
    let va = a.cells[colIdx].textContent.trim();
    let vb = b.cells[colIdx].textContent.trim();
    const na = parseFloat(va.replace(/[^0-9.-]/g, ''));
    const nb = parseFloat(vb.replace(/[^0-9.-]/g, ''));
    if (!isNaN(na) && !isNaN(nb)) {{ va = na; vb = nb; }}
    if (va < vb) return dir === 'asc' ? -1 : 1;
    if (va > vb) return dir === 'asc' ? 1 : -1;
    return 0;
  }});

  rows.forEach(r => tbody.appendChild(r));
}}

// Filter
function filterTable(query) {{
  const q = query.toLowerCase();
  document.querySelectorAll('.tab-content.active tbody tr').forEach(row => {{
    row.style.display = row.textContent.toLowerCase().includes(q) ? '' : 'none';
  }});
}}

// Initial render
renderApis();
renderFlows();
renderQuotas();
renderLambdas();
</script>
</body>
</html>"""
