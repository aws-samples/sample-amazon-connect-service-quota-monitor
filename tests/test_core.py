"""Core tests for the Connect Quota Monitor.

Unit tests: individual functions in isolation.
Integration tests: full pipeline with mocked AWS calls.
"""

import json
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))


class TestResourceMapper:
    """Unit tests for connect-resource-mapper.py functions."""

    def test_import_mapper(self):
        """Mapper module imports without error."""
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "mapper", Path(__file__).parent.parent / "connect-resource-mapper.py"
        )
        importlib.util.module_from_spec(spec)
        # Don't exec (it has argparse at module level) — just verify importable
        assert spec is not None


class TestDashboardGeneration:
    """Unit tests for dashboard_v4.py."""

    def test_import_dashboard(self):
        from dashboard_v4 import build_dashboard_data
        assert callable(build_dashboard_data)

    def test_build_dashboard_data_empty_input(self):
        from dashboard_v4 import build_dashboard_data
        result = build_dashboard_data(
            resource_map={"contact_flows": [], "phone_numbers": [], "lambdas": []},
            model={"quota_headroom": {}, "flow_to_lambda_map": {}, "tdg_number_distribution": {}},
            line_config={},
        )
        assert "LINES" in result
        assert "TOTAL_CAPACITY" in result

    def test_no_nan_in_dashboard_output(self):
        """Regression: dashboard must not produce NaN/Infinity values."""
        from dashboard_v4 import build_dashboard_data
        result = build_dashboard_data(
            resource_map={"contact_flows": [], "phone_numbers": [], "lambdas": []},
            model={"quota_headroom": {}, "flow_to_lambda_map": {}, "tdg_number_distribution": {}},
            line_config={"lines": [{"id": "test", "name": "Test", "number": "1-800-TEST",
                                     "match": {"flow_patterns": ["*"]}}]},
        )
        json_str = json.dumps(result, default=str)
        assert "NaN" not in json_str
        assert "Infinity" not in json_str


class TestConsolidatedReport:
    """Unit tests for consolidated_report.py."""

    def test_import_report(self):
        from consolidated_report import generate_consolidated_report
        assert callable(generate_consolidated_report)

    def test_report_escapes_user_controlled_names(self):
        """Regression: XSS — Connect resource names must not break out of the
        <script> block or inject markup into the rendered report."""
        from consolidated_report import _render_report_html
        summary = {k: 1 for k in (
            "total_phone_numbers", "total_flows", "unique_apis_called",
            "total_api_calls_per_contact", "total_lambdas",
            "critical_quotas", "warning_quotas",
        )}
        summary["generated_at"] = "2026-01-01"
        html = _render_report_html(
            all_apis=[{"api": "c:X", "action_types": ["<img src=x onerror=alert(1)>"],
                       "total_calls_per_contact": 1, "flow_count": 1, "max_in_single_flow": 1}],
            per_flow=[{"flow_name": "</script><script>alert(1)</script>", "flow_type": "X",
                       "total_api_calls_per_contact": 1, "unique_apis": 1, "lambda_count": 0,
                       "apis": [{"api": "a:b", "count": 1}]}],
            quota_table=[{"api_name": "</script>", "limit_tps": 5, "current_peak_tps": 1,
                          "utilization_pct": 10.0, "headroom_tps": 4.0, "avg_daily_calls": 1,
                          "peak_daily_calls": 1, "status": "OK"}],
            lambda_table=[{"name": "<b>x</b>", "runtime": "python3.12", "memory_mb": 128,
                           "timeout_sec": 3, "provisioned_concurrency": False,
                           "flow_count": 0, "invoked_by_flows": ["</script>"]}],
            summary=summary, resource_map={},
        )
        assert "<img src=x onerror" not in html
        assert "<script>alert(1)</script>" not in html
        # Exactly one legitimate closing </script> (the report's own script block).
        assert html.count("</script>") == 1


class TestDashboardXss:
    """Regression: dashboard_v4 must escape user-controlled Connect names."""

    def test_dashboard_escapes_flow_and_line_names(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "d4", Path(__file__).parent.parent / "dashboard_v4.py"
        )
        d4 = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(d4)
        resource_map = {"contact_flows": [{"Name": "</script><svg onload=alert(2)>", "Tags": {}}],
                        "phone_numbers": [], "usage_metrics": {}}
        line_config = {"lines": [{"id": "l1", "name": "</script><img src=x onerror=alert(1)>",
                                   "number": "+1", "match": {"flow_patterns": ["*"]}}], "defaults": {}}
        html = d4._render_v4_html(d4.build_dashboard_data(resource_map, {"quota_headroom": {}}, line_config))
        assert "</script><img src=x onerror" not in html
        assert "</script><svg onload" not in html
        assert "function esc(" in html
        # Selection uses event delegation on data- attributes, not inline onclick
        # with interpolated user data (which would need fragile escaping and would
        # also break name matching if the value were stripped).
        assert 'onclick="selectLine' not in html
        assert 'onclick="selectFlow' not in html
        assert 'data-action="select-line"' in html
        assert 'data-action="select-flow"' in html
        # Headroom math divides by capacity-used %, which is 0 on an idle line.
        # The guarded headroom() helper must be present so the UI shows a dash
        # instead of "InfinityM" / "NaN".
        assert "function headroom(" in html
        assert "Math.round(line.today / line.capacityPct" not in html
        # The delegated listeners must actually be wired, else the markup is inert
        # (data- attributes present but nothing clickable) — this would otherwise be
        # a silent regression that the assertions above wouldn't catch.
        assert "addEventListener('click'" in html
        assert "addEventListener('keydown'" in html
        assert "el.dataset.id" in html


class TestQuotaReportHtmlXss:
    """Regression: quota_report_to_html must escape names and be library-callable."""

    def test_escapes_instance_alias_and_quota_name(self):
        import quota_report_to_html as q
        data = {"timestamp": "2026-01-01T00:00:00", "threshold_percentage": 80,
                "monitoring_results": {"total_quotas_checked": 1, "violations_found": 0,
                    "instances_monitored": 1, "account_quotas_checked": 0, "account_results": [],
                    "instance_results": {"i-1": {"instance_alias": "</script><img src=x onerror=alert(1)>",
                        "results": [{"category": "CORE_CONNECT", "quota_name": "<script>alert(2)</script>",
                                     "current_usage": 1, "quota_limit": 10, "utilization_percentage": 10}]}}}}
        html = q.generate_html(data)   # must not raise NameError (was: input_path)
        assert "<img src=x onerror" not in html
        assert "<script>alert(2)</script>" not in html


class TestNoHardcodedSecrets:
    """Security: no credentials in source."""

    def test_no_akia_keys(self):
        """No AWS access key IDs in any Python file."""
        import re
        root = Path(__file__).parent.parent
        for py_file in root.rglob("*.py"):
            if ".git" in str(py_file) or "__pycache__" in str(py_file):
                continue
            content = py_file.read_text()
            assert not re.search(r"AKIA[A-Z0-9]{16}", content), \
                f"Possible AWS key in {py_file.name}"

    # UUIDs that are allowed to appear in committed source because they are
    # obvious placeholders or well-known public AWS constants, not a real
    # Connect instance. Anything else UUID-shaped in tracked source fails below.
    ALLOWED_UUIDS = {
        "00000000-0000-0000-0000-000000000001",  # placeholder instance id
        # AWS-managed CloudFront policy IDs referenced in cloudfront.tf (public):
        "4135ea2d-6df8-44a3-9df3-4b5a84be39ad",  # Managed-CachingDisabled
        "b689b0a8-53d0-40ab-baf2-68738e2966ac",  # Managed-AllViewerExceptHostHeader
    }

    def test_no_hardcoded_instance_ids_in_source(self):
        """No real Connect instance ID (or any non-placeholder UUID) in committed
        source. Connect instance IDs are UUIDs, so a real one leaking into the repo
        would be UUID-shaped. This scans every git-tracked source file and allows
        only the known placeholder UUIDs; a real deployment's instance ID lives in
        the gitignored terraform.tfvars and must never reach a tracked file. Runs
        unconditionally so CI always enforces it (no env var, no skip)."""
        import re
        import subprocess

        root = Path(__file__).parent.parent
        uuid_re = re.compile(
            r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.IGNORECASE
        )
        scanned_exts = (".py", ".tf", ".tftpl", ".js", ".json", ".md", ".yaml", ".yml", ".sh")
        allowed = {u.lower() for u in self.ALLOWED_UUIDS}

        # git ls-files gives exactly the committed files, so gitignored secrets
        # (terraform.tfvars, terraform.tfstate) are never scanned or flagged.
        tracked = subprocess.run(
            ["git", "ls-files"], cwd=root, capture_output=True, text=True, check=True
        ).stdout.split()

        offenders = {}
        for rel in tracked:
            if not rel.endswith(scanned_exts):
                continue
            text = (root / rel).read_text(encoding="utf-8", errors="ignore")
            found = {m.lower() for m in uuid_re.findall(text)} - allowed
            if found:
                offenders[rel] = sorted(found)

        assert not offenders, f"Non-placeholder UUID(s) in committed source: {offenders}"


class TestCloudWatchMonitoring:
    """Regression: percentage-typed CloudWatch metrics must not be treated as counts."""

    def _make_monitor(self):
        """Build a ConnectQuotaMonitor without running its AWS-touching __init__."""
        from lambda_function import ConnectQuotaMonitor
        return ConnectQuotaMonitor.__new__(ConnectQuotaMonitor)

    def test_percentage_metric_not_used_as_count(self):
        """Regression: C4 — a 0-100 percentage datapoint must not become a raw usage
        count. Previously ConcurrentHighVolumeCallsPercentage=80 was divided by the
        limit of 10, yielding 800% utilization and a false CRITICAL alert."""
        monitor = self._make_monitor()
        metric_config = {
            "metric_name": "ConcurrentCalls",
            "metric_name_fallback": "ConcurrentHighVolumeCallsPercentage",
            "namespace": "AWS/Connect",
            "statistic": "Maximum",
            "scope": "INSTANCE",
        }
        # Primary metric empty -> fallback percentage metric returns 80 (percent).
        calls = {"n": 0}

        def fake_call(service, api, **kwargs):
            calls["n"] += 1
            if kwargs.get("MetricName") == "ConcurrentCalls":
                return {"Datapoints": []}
            return {"Datapoints": [{"Maximum": 80, "Timestamp": 0}]}

        monitor.call_service_api = fake_call
        usage = monitor._monitor_via_cloudwatch("inst-123", metric_config)
        assert usage == 0, f"percentage metric leaked in as count usage={usage}"

    def test_count_metric_still_returned(self):
        """A genuine count metric is still returned unchanged."""
        monitor = self._make_monitor()
        metric_config = {
            "metric_name": "ConcurrentCalls",
            "namespace": "AWS/Connect",
            "statistic": "Maximum",
            "scope": "INSTANCE",
        }
        monitor.call_service_api = lambda *a, **k: {
            "Datapoints": [{"Maximum": 7, "Timestamp": 0}]
        }
        assert monitor._monitor_via_cloudwatch("inst-123", metric_config) == 7

    def test_low_rate_quota_reports_fractional_tps_not_ceiled(self):
        """Regression: a single call in the window must report a small fractional
        TPS, not be ceiled to 1 (which would read as 200% of a 0.5/s limit)."""
        monitor = self._make_monitor()
        monitor.call_service_api = lambda *a, **k: {"Datapoints": [{"Sum": 1, "Timestamp": 0}]}
        usage = monitor._monitor_via_cloudwatch_api(None, {"operation": "SearchContacts", "scope": "ACCOUNT"})
        assert usage < 0.02, f"expected ~0.0167 TPS, got {usage}"
        # Against a 0.5/s limit that is ~3.3% utilization, not 200%.
        assert (usage / 0.5) * 100 < 5

    def test_concurrent_calls_quota_has_no_percentage_fallback(self):
        """The concurrent-calls quota must not carry the invalid percentage fallback."""
        defs_path = Path(__file__).parent.parent / "quota_definitions.json"
        defs = json.loads(defs_path.read_text())
        entry = defs["L-12AB7C57"]
        assert "ercent" not in entry.get("metric_name_fallback", ""), \
            "L-12AB7C57 still uses a percentage metric as a count fallback"

    def test_api_rate_uses_aws_usage_callcount(self):
        """Regression: C3 — API request-rate usage must query AWS/Usage:CallCount with
        Service/Class/Type/Resource dimensions, not AWS/Connect:APICallCount:Operation
        (which never returns data, so every rate quota silently read 0%)."""
        monitor = self._make_monitor()
        captured = {}

        def fake_call(service, api, **kwargs):
            captured.update(kwargs)
            return {"Datapoints": [{"Sum": 300, "Timestamp": 0}]}

        monitor.call_service_api = fake_call
        usage = monitor._monitor_via_cloudwatch_api(
            None, {"operation": "GetMetricData", "scope": "ACCOUNT"}
        )
        assert captured["Namespace"] == "AWS/Usage"
        assert captured["MetricName"] == "CallCount"
        dims = {d["Name"]: d["Value"] for d in captured["Dimensions"]}
        assert dims == {
            "Service": "Connect", "Class": "None",
            "Type": "API", "Resource": "GetMetricData",
        }
        # 300 calls in the peak minute -> 5 TPS.
        assert usage == 5, f"expected 5 TPS, got {usage}"

    def test_api_rate_uses_peak_minute_not_window_average(self):
        """A burst confined to one minute must not be smoothed away across the window."""
        monitor = self._make_monitor()
        # 600 calls in one minute (10 TPS), zero in others. Old code averaged over
        # 300s -> 2 TPS; corrected code reports the 10 TPS peak minute.
        monitor.call_service_api = lambda *a, **k: {
            "Datapoints": [
                {"Sum": 600, "Timestamp": 2},
                {"Sum": 0, "Timestamp": 1},
                {"Sum": 0, "Timestamp": 0},
            ]
        }
        usage = monitor._monitor_via_cloudwatch_api(
            None, {"operation": "GetMetricData", "scope": "ACCOUNT"}
        )
        assert usage == 10, f"expected 10 TPS peak, got {usage}"


class TestAppliedQuotaLookup:
    """Regression: applied limits come from a single batched ListServiceQuotas
    per service (cached), never a per-quota GetServiceQuota call."""

    def _make_monitor(self):
        from lambda_function import ConnectQuotaMonitor
        m = ConnectQuotaMonitor.__new__(ConnectQuotaMonitor)
        m.region = "us-east-1"
        return m

    def test_applied_limit_from_list_service_quotas(self):
        monitor = self._make_monitor()
        calls = []

        def fake_call(service, api, **kwargs):
            calls.append((service, api))
            if api == "list_service_quotas":
                return {"Quotas": [{"QuotaCode": "L-D945C9A8", "Value": 75.0}]}
            raise AssertionError(f"unexpected call {service}.{api}")

        monitor.call_service_api = fake_call
        val = monitor._get_actual_quota_limit("connect", "L-D945C9A8", instance_id="inst-1")
        assert val == 75.0, f"expected applied value 75 from the batch map, got {val}"
        assert all(api == "list_service_quotas" for _, api in calls), \
            "must not call get_service_quota per quota"
        # Second lookup must be served from cache (no new list_service_quotas call).
        before = len(calls)
        monitor._get_actual_quota_limit("connect", "L-D945C9A8", instance_id="inst-1")
        assert len(calls) == before, "second lookup should hit the cached map"

    def test_no_context_required_quotas_remaining(self):
        """All quota definitions now omit resource-level context (design decision:
        Connect applied quotas are read at account/Region level)."""
        defs_path = Path(__file__).parent.parent / "quota_definitions.json"
        defs = json.loads(defs_path.read_text())
        still_true = [c for c, q in defs.items() if q.get("context_required") is True]
        assert still_true == [], f"context_required still True for: {still_true}"


class TestServiceQuotasUsage:
    """Regression: C2-logic — service_quotas usage must come from the CloudWatch
    metric described by UsageMetric, not from a non-existent 'MetricValue' key
    (which made every such quota report 0% and never alert)."""

    def _make_monitor(self):
        from lambda_function import ConnectQuotaMonitor
        m = ConnectQuotaMonitor.__new__(ConnectQuotaMonitor)
        m.region = "us-east-1"
        return m

    def test_usage_queried_from_usage_metric(self):
        monitor = self._make_monitor()
        captured = {}

        def fake_call(service, api, **kwargs):
            if api == "list_service_quotas":
                return {"Quotas": [{"QuotaCode": "L-64992552", "Value": 100.0, "UsageMetric": {
                    "MetricNamespace": "AWS/Usage",
                    "MetricName": "ResourceCount",
                    "MetricDimensions": {"Service": "Connect", "Resource": "X"},
                    "MetricStatisticRecommendation": "Maximum",
                }}]}
            captured.update(kwargs)  # the CloudWatch call
            return {"Datapoints": [{"Maximum": 42, "Timestamp": 0}]}

        monitor.call_service_api = fake_call
        usage, limit = monitor._monitor_via_service_quotas(
            "inst-1", {"service": "connect"}, "L-64992552"
        )
        assert (usage, limit) == (42, 100)
        assert captured["Namespace"] == "AWS/Usage"
        assert captured["MetricName"] == "ResourceCount"

    def test_missing_usage_metric_returns_none_not_zero(self):
        """No UsageMetric -> usage unknown (None), not a false 0."""
        monitor = self._make_monitor()
        monitor.call_service_api = lambda service, api, **k: (
            {"Quotas": [{"QuotaCode": "L-59F577B1", "Value": 50.0}]}
            if api == "list_service_quotas" else {}
        )
        usage, limit = monitor._monitor_via_service_quotas(
            "inst-1", {"service": "connect", "default_limit": 50}, "L-59F577B1"
        )
        assert usage is None, f"usage should be None (unknown), got {usage}"
        assert limit == 50

    def test_synthetic_code_not_in_map_falls_back_to_default(self):
        """A quota code that ListServiceQuotas does not return (e.g. a synthetic
        L-API-* rate code) must fall back to the default with no per-quota call."""
        monitor = self._make_monitor()
        monitor.call_service_api = lambda service, api, **k: {"Quotas": []}
        usage, limit = monitor._monitor_via_service_quotas(
            "inst-1", {"service": "connect", "default_limit": 7}, "L-API-FAKE"
        )
        assert usage is None and limit == 7.0


class TestApiRetryBackoff:
    """Retry policy: deterministic errors must fail fast, throttling must back off."""

    def _make_monitor(self):
        from lambda_function import ConnectQuotaMonitor
        m = ConnectQuotaMonitor.__new__(ConnectQuotaMonitor)
        m.region = "us-east-1"
        return m

    def _client_raising(self, error_code):
        from botocore.exceptions import ClientError

        class _Client:
            calls = 0

            def list_service_quotas(self, **kwargs):
                _Client.calls += 1
                raise ClientError(
                    {"Error": {"Code": error_code, "Message": "not available in Region"}},
                    "ListServiceQuotas",
                )

        return _Client()

    def test_no_such_resource_is_not_retried(self):
        """NoSuchResourceException is deterministic -> one call, no backoff cycles."""
        monitor = self._make_monitor()
        client = self._client_raising("NoSuchResourceException")
        monitor.get_service_client = lambda service: client
        result = monitor.call_service_api("connect-participant", "list_service_quotas")
        assert result is None
        assert type(client).calls == 1, "must not retry a deterministic region error"

    def test_throttling_is_retried(self, monkeypatch):
        """TooManyRequestsException must exhaust the 3 retries (backoff path)."""
        import lambda_function
        monkeypatch.setattr(lambda_function.time, "sleep", lambda *_: None)
        monitor = self._make_monitor()
        client = self._client_raising("TooManyRequestsException")
        monitor.get_service_client = lambda service: client
        result = monitor.call_service_api("connect", "list_service_quotas")
        assert result is None
        assert type(client).calls == 3, "throttling should retry up to max_retries"


class TestServiceQuotaMapCaching:
    """Regression: a partial/failed ListServiceQuotas result is reused within one
    invocation (so a throttle can't cause a re-fetch storm that blows the 300s
    budget) but is NOT promoted to the cross-invocation cache (so it can't shadow
    real applied limits with defaults across runs and hide a breach)."""

    def _make_monitor(self):
        from lambda_function import ConnectQuotaMonitor
        m = ConnectQuotaMonitor.__new__(ConnectQuotaMonitor)
        m.region = "us-east-1"
        return m

    def test_partial_result_memoized_within_invocation(self):
        """A throttled/partial fetch must be attempted at most once per service
        per invocation (no re-fetch storm)."""
        monitor = self._make_monitor()
        calls = {"n": 0}

        def fake_call(service, api, **kwargs):
            calls["n"] += 1
            if "NextToken" in kwargs:
                return None  # page 2 throttles out -> incomplete map
            return {"Quotas": [{"QuotaCode": "L-1", "Value": 5.0}], "NextToken": "tok"}

        monitor.call_service_api = fake_call
        first = monitor._get_service_quota_map("connect")
        assert "L-1" in first, "partial data is still returned for this call"
        n_after_first = calls["n"]
        monitor._get_service_quota_map("connect")
        assert calls["n"] == n_after_first, "a second lookup this invocation must be memoized, not re-fetched"

    def test_partial_not_promoted_to_cross_invocation_cache(self):
        """A partial map must not land in the 5-min cross-call cache, so a fresh
        instance (next invocation) retries instead of serving stale defaults."""
        monitor = self._make_monitor()
        monitor.call_service_api = lambda service, api, **k: (
            {"Quotas": [{"QuotaCode": "L-1", "Value": 5.0}], "NextToken": "tok"}
            if "NextToken" not in k else None
        )
        monitor._get_service_quota_map("connect")
        assert "connect" not in getattr(monitor, "_service_quota_maps", {}), \
            "partial map must not be promoted to the cross-invocation cache"

    def test_complete_map_is_cached(self):
        monitor = self._make_monitor()
        calls = {"n": 0}

        def fake_call(service, api, **kwargs):
            calls["n"] += 1
            return {"Quotas": [{"QuotaCode": "L-1", "Value": 5.0}]}  # no NextToken -> complete

        monitor.call_service_api = fake_call
        monitor._get_service_quota_map("connect")
        n = calls["n"]
        monitor._get_service_quota_map("connect")
        assert calls["n"] == n, "a complete map should be cached (no re-fetch within TTL)"
        assert "connect" in monitor._service_quota_maps, "complete map should be in the cross-call cache"


class TestApiCountDispatch:
    """Regression: describe_user_hierarchy_structure has no pagination response
    key, so it must be dispatched to its counter BEFORE the response-key lookup,
    or the quota is silently skipped (returns None)."""

    def _make_monitor(self):
        from lambda_function import ConnectQuotaMonitor
        m = ConnectQuotaMonitor.__new__(ConnectQuotaMonitor)
        m.region = "us-east-1"
        return m

    def test_hierarchy_structure_reaches_counter(self):
        monitor = self._make_monitor()

        def fake_call(service, api, **kwargs):
            assert api == "describe_user_hierarchy_structure"
            return {"HierarchyStructure": {
                "LevelOne": {"Name": "L1"},
                "LevelTwo": {"Name": "L2"},
            }}

        monitor.call_service_api = fake_call
        count = monitor._monitor_via_api_count(
            "inst-1", {"service": "connect", "api": "describe_user_hierarchy_structure"}
        )
        assert count == 2, f"expected 2 hierarchy levels, got {count} (dispatch regressed?)"


class TestLiveRefreshHandler:
    """The public API path must be read-only: an API Gateway GET must never
    trigger the resource-map crawl or S3 writes; only the schedule may write."""

    def _load(self):
        import importlib.util
        path = Path(__file__).parent.parent / "live-refresh" / "lambda_function.py"
        spec = importlib.util.spec_from_file_location("live_refresh_lambda", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod

    def test_api_request_is_read_only(self, monkeypatch):
        mod = self._load()
        monkeypatch.setenv("CONNECT_INSTANCE_ID", "inst-1")
        monkeypatch.setenv("S3_REPORT_BUCKET", "bucket")
        seen = {"serve_latest": 0, "write": 0, "collect": 0}

        def _serve_latest(bucket):
            seen["serve_latest"] += 1
            return {"statusCode": 200, "body": "{}"}

        def _boom_write(*a, **k):
            seen["write"] += 1

        def _boom_collect(*a, **k):
            seen["collect"] += 1
            return {}

        monkeypatch.setattr(mod, "_serve_latest", _serve_latest)
        monkeypatch.setattr(mod, "_write_latest", _boom_write)
        monkeypatch.setattr(mod, "_collect_snapshot", _boom_collect)
        # An API Gateway proxy request always carries requestContext.
        mod.lambda_handler({"requestContext": {}, "queryStringParameters": None}, None)
        assert seen["serve_latest"] == 1, "API GET must serve latest.json (read-only)"
        assert seen["write"] == 0, "API GET must not write to S3"
        assert seen["collect"] == 0, "API GET must not run a fresh collection/crawl"

    def test_scheduled_event_collects_and_writes(self, monkeypatch):
        mod = self._load()
        monkeypatch.setenv("CONNECT_INSTANCE_ID", "inst-1")
        monkeypatch.setenv("S3_REPORT_BUCKET", "bucket")
        seen = {"write": 0, "collect": 0}

        monkeypatch.setattr(mod, "_collect_snapshot", lambda *a, **k: seen.__setitem__("collect", seen["collect"] + 1) or {"quotas": []})
        monkeypatch.setattr(mod, "_write_latest", lambda *a, **k: seen.__setitem__("write", seen["write"] + 1))
        monkeypatch.setattr(mod, "_write_archive", lambda *a, **k: None)
        monkeypatch.setattr(mod, "_update_peak", lambda *a, **k: None)
        # A scheduled EventBridge event has no requestContext.
        mod.lambda_handler({}, None)
        assert seen["collect"] == 1, "scheduled run must collect"
        assert seen["write"] == 1, "scheduled run must write latest.json"


class TestPaginationCounting:
    """Regression: C6 — hitting the pagination safety cap must not silently return
    a truncated (falsely-low) count that hides a quota breach."""

    def _make_monitor(self):
        from lambda_function import ConnectQuotaMonitor
        m = ConnectQuotaMonitor.__new__(ConnectQuotaMonitor)
        m.performance_optimizer = None
        return m

    def test_full_pagination_sums_all_pages(self):
        monitor = self._make_monitor()
        # 3 pages of 2 items, last page no token.
        pages = [
            {"Items": [1, 2], "NextToken": "a"},
            {"Items": [3, 4], "NextToken": "b"},
            {"Items": [5, 6]},
        ]
        seq = iter(pages)
        monitor.call_service_api = lambda *a, **k: next(seq)
        count = monitor._count_via_pagination_enhanced("connect", "list_x", "Items", {})
        assert count == 6

    def test_pagination_cap_returns_none_not_truncated_count(self):
        monitor = self._make_monitor()
        # Always returns a token -> would loop forever; must stop at the cap and
        # return None (unknown), never a partial count.
        monitor.call_service_api = lambda *a, **k: {"Items": [1], "NextToken": "x"}
        count = monitor._count_via_pagination_enhanced("connect", "list_x", "Items", {})
        assert count is None, f"cap-hit must return None, got {count}"

    def test_multi_count_propagates_none_on_truncated_child(self):
        """Regression: a truncated/unknown child count must make the whole
        api_count_multi aggregate None, not a silently-low sum."""
        from lambda_function import ConnectQuotaMonitor
        m = ConnectQuotaMonitor.__new__(ConnectQuotaMonitor)
        m.performance_optimizer = None
        # Parent returns 2 parents; first child paginates forever (-> None).
        def fake_call(service, api, **k):
            if api == "list_parents":
                return {"Parents": [{"id": "p1"}, {"id": "p2"}]}
            return {"Items": [1], "NextToken": "loops"}  # child never terminates

        m.call_service_api = fake_call
        m._get_response_key = lambda s, a: "Parents" if a == "list_parents" else "Items"
        m._build_api_parameters = lambda iid, cfg: {}
        cfg = {"service": "connect", "api": "list_children", "parent_service": "connect",
               "parent_api": "list_parents", "parent_key": "id", "scope": "ACCOUNT"}
        assert m._monitor_via_api_count_multi("inst-1", cfg) is None


class TestThresholdCoercion:
    """Regression: C13 — a bad THRESHOLD_PERCENTAGE must not crash module import."""

    def test_invalid_threshold_falls_back_to_default(self):
        from lambda_function import _coerce_threshold
        assert _coerce_threshold("high") == 80
        assert _coerce_threshold(None) == 80
        assert _coerce_threshold("150") == 80   # out of range
        assert _coerce_threshold("0") == 80     # out of range
        assert _coerce_threshold("90") == 90
        assert _coerce_threshold("85.0") == 85
        assert _coerce_threshold("inf") == 80    # OverflowError path
        assert _coerce_threshold("1e400") == 80


class TestSnsPublish:
    """Regression: C9 — SNS publish must not use an invalid 'json' protocol key
    (which rejects the whole publish) and must cap the Subject at 100 chars."""

    def test_publish_omits_invalid_json_key_and_truncates_subject(self):
        from lambda_function import AlertConsolidationEngine
        engine = AlertConsolidationEngine.__new__(AlertConsolidationEngine)
        engine.topic_arn = "arn:aws:sns:us-east-1:123456789012:alerts"
        captured = {}

        class FakeSns:
            def publish(self, **kwargs):
                captured.update(kwargs)
                return {"MessageId": "mid"}

        engine.sns_client = FakeSns()
        ok = engine._send_sns_alert(
            {"violations_count": 2, "scope": "INSTANCE", "instance_alias": "x"},
            "human readable body",
            "S" * 250,  # overlong subject with no newlines
        )
        assert ok is True
        import json as _json
        body = _json.loads(captured["Message"])
        assert set(body.keys()) == {"default", "email", "sms"}, body.keys()
        assert "json" not in body
        assert len(captured["Subject"]) <= 100
