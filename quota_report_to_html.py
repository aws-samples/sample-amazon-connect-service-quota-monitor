#!/usr/bin/env python3
"""Convert Amazon Connect quota report JSON to an HTML dashboard."""

import json
import sys
from datetime import datetime
from pathlib import Path


def util_class(pct):
    if pct >= 80:
        return "high"
    if pct >= 50:
        return "mid"
    return "low"


def badge(violations):
    if violations > 0:
        return f'<span class="badge badge-red">{violations} violation{"s" if violations != 1 else ""}</span>'
    return '<span class="badge badge-green">All clear</span>'


def bar_row(name, usage, limit, pct, extra_cols=""):
    cls = util_class(pct)
    width = min(pct, 100)
    return (
        f"<tr>{extra_cols}<td>{name}</td>"
        f'<td class="mono">{usage:,} / {limit:,}</td>'
        f'<td class="bar-cell"><div class="bar-bg"><div class="bar-fill {cls}" style="width:{width}%"></div></div></td>'
        f'<td class="right"><span class="util {cls}">{pct:.1f}%</span></td></tr>\n'
    )


def api_row(name, service, limit, usage, pct):
    cls = util_class(pct)
    limit_str = f"{limit:g}"
    return (
        f"<tr><td>{name}</td>"
        f'<td class="mono">{service}</td>'
        f'<td class="right mono">{limit_str}</td>'
        f'<td class="right mono">{usage}</td>'
        f'<td class="right"><span class="util {cls}">{pct:.1f}%</span></td></tr>\n'
    )


def group_by_category(results):
    groups = {}
    for r in results:
        cat = r.get("category", "OTHER")
        groups.setdefault(cat, []).append(r)
    for items in groups.values():
        items.sort(key=lambda x: x.get("utilization_percentage", 0), reverse=True)
    return groups


CATEGORY_LABELS = {
    "CORE_CONNECT": "Core Connect",
    "CONTACT_HANDLING": "Contact Handling",
    "ROUTING_QUEUES": "Routing and Queues",
    "INTEGRATIONS": "Integrations",
    "FORECASTING_CAPACITY": "Forecasting and Capacity",
    "API_RATE_LIMITS": "API Rate Limits",
}

CSS = """
/* Amazon Internal Design Guidelines - Support Ops Compliant */
/* Color palette from official Amazon style guide */
:root{
  --squid:#161D26;
  --g100:#f2f3f3;
  --g200:#e9ebed;
  --g600:#687078;
  --blue:#0972d3;
  --green:#037f0c;
  --orange:#d97706;
  --red:#d91515;
}
*{margin:0;padding:0;box-sizing:border-box}
body{
  font-family:'Amazon Ember',Arial,sans-serif;
  background:var(--g100);
  color:var(--squid);
  line-height:1.5;
  -webkit-font-smoothing:antialiased;
}
.container{max-width:1200px;margin:0 auto;padding:24px}

/* Responsive breakpoints for Amazon laptops (HP 640 G2: 1366x768, Standard: 1920x1080) */
/* Accounts for Tree Style Tabs (168px), bookmarks row (113px), taskbar (40px) */
@media (max-width:600px){
  .container{padding:16px}
  .kpi-row{grid-template-columns:1fr}
  h1{font-size:20px}
  .bar-cell{width:120px}
  table{font-size:12px}
  th,td{padding:8px 12px}
}
@media (min-width:601px) and (max-width:1024px){
  .container{padding:20px}
  .kpi-row{grid-template-columns:repeat(2,1fr)}
  .bar-cell{width:150px}
}
@media (min-width:1025px){
  .kpi-row{grid-template-columns:repeat(5,1fr)}
}

h1{font-size:24px;font-weight:700;margin-bottom:4px}
.subtitle{color:var(--g600);font-size:14px;margin-bottom:24px}
.kpi-row{display:grid;gap:16px;margin-bottom:24px}
.kpi{
  background:#fff;
  border-radius:8px;
  padding:20px;
  border:1px solid var(--g200);
  box-shadow:0 1px 3px rgba(22,29,38,0.05);
}
.kpi-label{font-size:12px;color:var(--g600);text-transform:uppercase;letter-spacing:.5px;font-weight:600}
.kpi-value{font-size:28px;font-weight:700;margin-top:4px}
.kpi-value.green{color:var(--green)}
.kpi-value.orange{color:var(--orange)}
.kpi-value.red{color:var(--red)}
.section{
  background:#fff;
  border-radius:8px;
  border:1px solid var(--g200);
  margin-bottom:24px;
  overflow:hidden;
  box-shadow:0 1px 3px rgba(22,29,38,0.05);
}
.section-header{
  padding:16px 20px;
  border-bottom:1px solid var(--g200);
  font-size:16px;
  font-weight:700;
  display:flex;
  justify-content:space-between;
  align-items:center;
  flex-wrap:wrap;
  gap:8px;
}
.badge{
  font-size:11px;
  font-weight:600;
  padding:2px 8px;
  border-radius:4px;
  white-space:nowrap;
}
.badge-green{background:#d5f0d5;color:var(--green)}
.badge-orange{background:#fef3cd;color:var(--orange)}
.badge-red{background:#fdd;color:var(--red)}
table{width:100%;border-collapse:collapse;font-size:13px;overflow-x:auto;display:block}
@media (min-width:768px){table{display:table}}
thead{display:none}
@media (min-width:768px){thead{display:table-header-group}}
th{
  text-align:left;
  padding:10px 16px;
  background:var(--g100);
  color:var(--g600);
  font-weight:600;
  font-size:11px;
  text-transform:uppercase;
  letter-spacing:.5px;
  border-bottom:1px solid var(--g200);
  white-space:nowrap;
}
td{padding:10px 16px;border-bottom:1px solid var(--g200)}
tr:last-child td{border-bottom:none}
tr:hover{background:#f8f9fa}
.bar-cell{width:200px;min-width:120px}
.bar-bg{
  background:var(--g200);
  border-radius:4px;
  height:8px;
  position:relative;
  overflow:hidden;
}
.bar-fill{height:100%;border-radius:4px;transition:width 0.3s ease}
.bar-fill.low{background:var(--green)}
.bar-fill.mid{background:var(--orange)}
.bar-fill.high{background:var(--red)}
.util{
  font-weight:600;
  font-size:12px;
  min-width:48px;
  display:inline-block;
  text-align:right;
}
.util.low{color:var(--green)}
.util.mid{color:var(--orange)}
.util.high{color:var(--red)}
.right{text-align:right}
.mono{font-family:'Amazon Ember Mono',Consolas,monospace;font-size:12px}
.footer{
  text-align:center;
  color:var(--g600);
  font-size:12px;
  padding:16px;
  margin-top:24px;
}
.scope-tag{
  font-size:10px;
  padding:1px 6px;
  border-radius:3px;
  background:var(--g200);
  color:var(--g600);
  font-weight:600;
}
"""


def generate_html(data):
    mon = data["monitoring_results"]
    total_checked = mon.get("total_quotas_checked", 0)
    violations = mon.get("violations_found", 0)
    instance_count = mon.get("instances_monitored", 0)
    account_checked = mon.get("account_quotas_checked", 0)
    ts = data.get("timestamp", "")
    threshold = data.get("threshold_percentage", 80)

    # Collect all results for max utilization
    all_results = list(mon.get("account_results", []))
    instance_alias = ""
    instance_id = ""
    instance_results = []
    for iid, idata in mon.get("instance_results", {}).items():
        instance_alias = idata.get("instance_alias", iid)
        instance_id = iid
        instance_results = idata.get("results", [])
        all_results.extend(instance_results)

    max_util = max((r.get("utilization_percentage", 0) for r in all_results), default=0)
    max_cls = util_class(max_util)

    # Format timestamp
    try:
        dt = datetime.fromisoformat(ts)
        ts_display = dt.strftime("%B %d, %Y %H:%M UTC")
    except Exception:
        ts_display = ts

    # Group instance results by category
    inst_groups = group_by_category(instance_results)

    # Separate account results: non-API vs API
    acct_non_api = [r for r in mon.get("account_results", []) if r.get("category") != "API_RATE_LIMITS"]
    acct_api = [r for r in mon.get("account_results", []) if r.get("category") == "API_RATE_LIMITS"]
    acct_api.sort(key=lambda x: x.get("quota_limit", 0), reverse=True)

    acct_violations = sum(1 for r in acct_non_api if r.get("utilization_percentage", 0) >= threshold)

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>Amazon Connect Quota Report — {instance_alias}</title>
<style>{CSS}</style>
</head>
<body>
<div class="container">
<h1>Amazon Connect Quota Report</h1>
<div class="subtitle">Instance: {instance_alias} ({instance_id}) — Generated: {ts_display}</div>

<div class="kpi-row">
  <div class="kpi"><div class="kpi-label">Total quotas checked</div><div class="kpi-value">{total_checked}</div></div>
  <div class="kpi"><div class="kpi-label">Violations (≥{threshold}%)</div><div class="kpi-value {"green" if violations == 0 else "red"}">{violations}</div></div>
  <div class="kpi"><div class="kpi-label">Instance quotas</div><div class="kpi-value">{total_checked - account_checked}</div></div>
  <div class="kpi"><div class="kpi-label">Account quotas</div><div class="kpi-value">{account_checked}</div></div>
  <div class="kpi"><div class="kpi-label">Highest utilization</div><div class="kpi-value {max_cls}">{max_util:.0f}%</div></div>
</div>
"""

    # Account-level non-API quotas
    if acct_non_api:
        html += f"""<div class="section">
<div class="section-header">Account-level quotas {badge(acct_violations) if acct_violations else '<span class="badge badge-green">0 violations</span>'}</div>
<table><thead><tr><th>Quota</th><th>Scope</th><th>Usage / Limit</th><th class="bar-cell">Utilization</th><th class="right">%</th></tr></thead><tbody>\n"""
        for r in sorted(acct_non_api, key=lambda x: x.get("utilization_percentage", 0), reverse=True):
            pct = r.get("utilization_percentage", 0)
            usage = int(r.get("current_usage", 0))
            limit = int(r.get("quota_limit", 0))
            scope_col = '<td><span class="scope-tag">ACCOUNT</span></td>'
            html += bar_row(r["quota_name"], usage, limit, pct, extra_cols=scope_col)
        html += "</tbody></table></div>\n"

    # Instance-level sections by category
    category_order = ["CORE_CONNECT", "CONTACT_HANDLING", "ROUTING_QUEUES", "INTEGRATIONS", "FORECASTING_CAPACITY"]
    for cat in category_order:
        items = inst_groups.get(cat, [])
        if not items:
            continue
        label = CATEGORY_LABELS.get(cat, cat)
        cat_violations = sum(1 for r in items if r.get("utilization_percentage", 0) >= threshold)
        html += f"""<div class="section">
<div class="section-header">{label} {badge(cat_violations)}</div>
<table><thead><tr><th>Quota</th><th>Usage / Limit</th><th class="bar-cell">Utilization</th><th class="right">%</th></tr></thead><tbody>\n"""
        for r in items:
            pct = r.get("utilization_percentage", 0)
            usage = int(r.get("current_usage", 0))
            limit = int(r.get("quota_limit", 0))
            html += bar_row(r["quota_name"], usage, limit, pct)
        html += "</tbody></table></div>\n"

    # Remaining instance categories not in the predefined order
    for cat, items in inst_groups.items():
        if cat in category_order or cat == "API_RATE_LIMITS":
            continue
        label = CATEGORY_LABELS.get(cat, cat.replace("_", " ").title())
        cat_violations = sum(1 for r in items if r.get("utilization_percentage", 0) >= threshold)
        html += f"""<div class="section">
<div class="section-header">{label} {badge(cat_violations)}</div>
<table><thead><tr><th>Quota</th><th>Usage / Limit</th><th class="bar-cell">Utilization</th><th class="right">%</th></tr></thead><tbody>\n"""
        for r in items:
            pct = r.get("utilization_percentage", 0)
            usage = int(r.get("current_usage", 0))
            limit = int(r.get("quota_limit", 0))
            html += bar_row(r["quota_name"], usage, limit, pct)
        html += "</tbody></table></div>\n"

    # API Rate Limits
    if acct_api:
        api_violations = sum(1 for r in acct_api if r.get("utilization_percentage", 0) >= threshold)
        api_badge = badge(api_violations) if api_violations else f'<span class="badge badge-green">All at 0% utilization</span>' if all(r.get("utilization_percentage", 0) == 0 for r in acct_api) else badge(api_violations)
        html += f"""<div class="section">
<div class="section-header">API Rate Limits (Account-level, {len(acct_api)} quotas) {api_badge}</div>
<table><thead><tr><th>API</th><th>Service</th><th class="right">Limit (TPS)</th><th class="right">Usage</th><th class="right">%</th></tr></thead><tbody>\n"""
        for r in acct_api:
            pct = r.get("utilization_percentage", 0)
            usage = int(r.get("current_usage", 0))
            limit = r.get("quota_limit", 0)
            name = r["quota_name"].replace("Rate of ", "").replace(" API requests", "")
            html += api_row(name, r.get("service", "connect"), limit, usage, pct)
        html += "</tbody></table></div>\n"

    html += f"""<div class="footer">Amazon Connect Quota Report — Generated from {Path(input_path).name}</div>
</div></body></html>"""
    return html


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python3 quota_report_to_html.py <input.json> [output.html]")
        sys.exit(1)

    input_path = sys.argv[1]
    output_path = sys.argv[2] if len(sys.argv) > 2 else input_path.replace(".json", ".html")

    with open(input_path) as f:
        data = json.load(f)

    html = generate_html(data)

    with open(output_path, "w") as f:
        f.write(html)

    print(f"Report generated: {output_path}")
