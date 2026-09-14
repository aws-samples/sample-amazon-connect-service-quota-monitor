"""Regression tests for the audit-remediation pass.

Each test here pins a specific bug the multi-role audit found, so the fix cannot
silently regress. Grouped by the file under test. All deterministic and offline
(botocore Stubber / fakes, no network, no real AWS).
"""

import importlib.util
import sys
from datetime import datetime, timezone
from pathlib import Path

from botocore.exceptions import ClientError

sys.path.insert(0, str(Path(__file__).parent.parent))

import lambda_function  # noqa: E402  (path insert must precede import)


def _monitor():
    """A ConnectQuotaMonitor without its AWS-touching __init__."""
    return lambda_function.ConnectQuotaMonitor.__new__(lambda_function.ConnectQuotaMonitor)


def _storage():
    """A FlexibleStorageEngine without its AWS-touching __init__ (owns TTL and
    the consolidated-report builder)."""
    return lambda_function.FlexibleStorageEngine.__new__(lambda_function.FlexibleStorageEngine)


def _load_live_refresh():
    path = Path(__file__).parent.parent / "live-refresh" / "lambda_function.py"
    spec = importlib.util.spec_from_file_location("live_refresh_lambda", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# lambda_function.py — list_phone_numbers_v2 parameter fix
# ---------------------------------------------------------------------------
class TestPhoneNumbersV2Params:
    """list_phone_numbers_v2 takes InstanceId OR TargetArn, never both."""

    def _params(self, api):
        m = _monitor()
        m.region = "us-east-1"
        m._get_account_id = lambda: "123456789012"
        return m._build_api_parameters(
            "00000000-0000-0000-0000-000000000001",
            {"service": "connect", "api": api, "scope": "INSTANCE"},
        )

    def test_phone_numbers_v2_uses_target_arn_only(self):
        params = self._params("list_phone_numbers_v2")
        assert "InstanceId" not in params, "must not send InstanceId alongside TargetArn"
        assert params["TargetArn"].endswith(":instance/00000000-0000-0000-0000-000000000001")

    def test_other_connect_api_still_gets_instance_id(self):
        params = self._params("list_queues")
        assert params["InstanceId"] == "00000000-0000-0000-0000-000000000001"


# ---------------------------------------------------------------------------
# lambda_function.py — zero/unavailable limit must report unavailable, not 0%
# ---------------------------------------------------------------------------
class TestZeroLimitReportsUnavailable:
    def _config(self):
        return {
            "method": "service_quotas", "quota_name": "Concurrent campaign calls",
            "category": "CONTACT_HANDLING", "scope": "INSTANCE",
            "service": "connectcampaigns", "default_limit": 0,
        }

    def test_usage_with_zero_limit_returns_none(self):
        m = _monitor()
        m._monitor_via_service_quotas = lambda *a, **k: (5, 0)  # usage 5, limit 0
        result = m._process_quota_config("iid", self._config(), "L-E908C3A1")
        assert result is None, "usage>0 with a 0/unknown limit must be unavailable, not 0%"

    def test_zero_usage_zero_limit_is_healthy(self):
        m = _monitor()
        m._monitor_via_service_quotas = lambda *a, **k: (0, 0)
        result = m._process_quota_config("iid", self._config(), "L-E908C3A1")
        assert result is not None and result["utilization_percentage"] == 0


# ---------------------------------------------------------------------------
# lambda_function.py — throttle vs denied on the instance access probe
# ---------------------------------------------------------------------------
class TestCheckInstanceAccess:
    def _monitor_with_list_users(self, behavior):
        m = _monitor()

        class _Client:
            def list_users(self, **kwargs):
                return behavior()

        m.get_service_client = lambda name: _Client()
        return m

    def test_ok_when_list_users_succeeds(self):
        m = self._monitor_with_list_users(lambda: {"UserSummaryList": []})
        assert m.check_instance_access("iid") == "ok"

    def test_denied_on_access_denied(self):
        def raise_denied():
            raise ClientError({"Error": {"Code": "AccessDeniedException", "Message": "no"}}, "ListUsers")
        m = self._monitor_with_list_users(raise_denied)
        assert m.check_instance_access("iid") == "denied"

    def test_throttle_is_unknown_not_denied(self):
        def raise_throttle():
            raise ClientError({"Error": {"Code": "TooManyRequestsException", "Message": "slow"}}, "ListUsers")
        m = self._monitor_with_list_users(raise_throttle)
        # A throttle must NOT be treated as a permission gap -- that would drop a
        # possibly-breaching instance from the run.
        assert m.check_instance_access("iid") == "unknown"

    def test_validate_instance_permissions_true_only_on_ok(self):
        m = self._monitor_with_list_users(lambda: {"UserSummaryList": []})
        assert m.validate_instance_permissions("iid") is True


# ---------------------------------------------------------------------------
# lambda_function.py — DynamoDB TTL is actually written
# ---------------------------------------------------------------------------
class TestDynamoDbTtl:
    def test_apply_ttl_adds_future_epoch(self, monkeypatch):
        monkeypatch.setenv("DYNAMODB_TTL_DAYS", "90")
        s = _storage()
        item = s._apply_ttl({"id": {"S": "x"}})
        assert "ttl" in item
        ttl = int(item["ttl"]["N"])
        assert ttl > int(datetime.now(timezone.utc).timestamp())

    def test_ttl_disabled_when_zero(self, monkeypatch):
        monkeypatch.setenv("DYNAMODB_TTL_DAYS", "0")
        s = _storage()
        assert "ttl" not in s._apply_ttl({"id": {"S": "x"}})


# ---------------------------------------------------------------------------
# lambda_function.py — SNS pending-only subscriptions surfaced as invalid
# ---------------------------------------------------------------------------
class TestSnsSubscriptionConfirmation:
    def _engine(self, subscriptions):
        eng = lambda_function.AlertConsolidationEngine.__new__(lambda_function.AlertConsolidationEngine)
        eng.topic_arn = "arn:aws:sns:us-east-1:123456789012:topic"

        class _Sns:
            def get_topic_attributes(self, **k):
                return {"Attributes": {}}

            def list_subscriptions_by_topic(self, **k):
                return {"Subscriptions": subscriptions}

        eng.sns_client = _Sns()
        return eng

    def test_pending_only_is_invalid(self):
        eng = self._engine([{"SubscriptionArn": "PendingConfirmation"}])
        ok, msg = eng.validate_sns_configuration()
        assert ok is False and "confirmed" in msg.lower()

    def test_confirmed_subscription_is_valid(self):
        eng = self._engine([{"SubscriptionArn": "arn:aws:sns:...:sub-1"}])
        ok, msg = eng.validate_sns_configuration()
        assert ok is True and "1 confirmed" in msg

    def test_mixed_reports_pending_count(self):
        eng = self._engine([
            {"SubscriptionArn": "arn:aws:sns:...:sub-1"},
            {"SubscriptionArn": "PendingConfirmation"},
        ])
        ok, msg = eng.validate_sns_configuration()
        assert ok is True and "pending" in msg.lower()


# ---------------------------------------------------------------------------
# lambda_function.py — consolidated report records the effective threshold
# ---------------------------------------------------------------------------
class TestReportThreshold:
    def test_report_includes_threshold(self, monkeypatch):
        monkeypatch.setenv("THRESHOLD_PERCENTAGE", "95")
        s = _storage()
        report = s._prepare_consolidated_report({"instances_monitored": 1}, {})
        assert report["threshold_percentage"] == 95


# ---------------------------------------------------------------------------
# lambda_function.py — alert threshold boundary (>=) is exercised, not assumed
# ---------------------------------------------------------------------------
class TestAlertThresholdBoundary:
    def _engine(self, threshold):
        eng = lambda_function.AlertConsolidationEngine.__new__(lambda_function.AlertConsolidationEngine)
        eng.threshold_percentage = threshold
        return eng

    def test_exactly_at_threshold_is_a_violation(self):
        eng = self._engine(80)
        data = {"results": [{"quota_name": "q", "utilization_percentage": 80.0}]}
        assert len(eng._extract_instance_violations(data)) == 1

    def test_just_below_threshold_is_not_a_violation(self):
        eng = self._engine(80)
        data = {"results": [{"quota_name": "q", "utilization_percentage": 79.99}]}
        assert eng._extract_instance_violations(data) == []

    def test_account_violation_boundary(self):
        eng = self._engine(90)
        results = {"account_results": [
            {"quota_name": "a", "utilization_percentage": 90.0},
            {"quota_name": "b", "utilization_percentage": 89.0},
        ]}
        viol = eng._extract_account_violations(results)
        assert [v["quota_name"] for v in viol] == ["a"]


# ---------------------------------------------------------------------------
# lambda_function.py — main() dispatch: config_update rejects an empty config
# ---------------------------------------------------------------------------
class TestMainConfigUpdate:
    def test_config_update_without_config_returns_400(self, monkeypatch):
        # main() builds a ConnectQuotaMonitor before dispatching; stub it so the
        # test exercises the dispatch/validation branch without touching AWS.
        monkeypatch.setattr(
            lambda_function, "ConnectQuotaMonitor",
            lambda *a, **k: object(),
        )
        result = lambda_function.main({"invocation_type": "config_update"}, None)
        code = result.get("statusCode") if isinstance(result, dict) else None
        assert code == 400, f"empty config_update should be a 400, got {result}"


# ---------------------------------------------------------------------------
# live-refresh/lambda_function.py — exact quota-name match, no false criticals
# ---------------------------------------------------------------------------
class _FakePaginator:
    def __init__(self, pages):
        self._pages = pages

    def paginate(self, **kwargs):
        return iter(self._pages)


class _FakeServiceQuotas:
    def __init__(self, quotas):
        self._quotas = quotas

    def get_paginator(self, name):
        return _FakePaginator([{"Quotas": self._quotas}])


class TestLiveRefreshQuotaLimits:
    def test_exact_match_does_not_clobber_prefixed_api(self):
        mod = _load_live_refresh()
        sq = _FakeServiceQuotas([
            {"QuotaName": "Rate of StopContact API requests", "Value": 2.0},
            {"QuotaName": "Rate of StopContactStreaming API requests", "Value": 5.0},
        ])
        limits = mod._get_quota_limits(sq)
        # StopContact must keep its own limit, not StopContactStreaming's.
        assert limits.get("StopContact") == 2.0

    def test_null_value_limit_is_omitted(self):
        mod = _load_live_refresh()
        sq = _FakeServiceQuotas([
            {"QuotaName": "Rate of StopContact API requests", "Value": None},
        ])
        limits = mod._get_quota_limits(sq)
        assert "StopContact" not in limits  # absent, not a fake default


class TestLiveRefreshSnapshotUnknownLimit:
    def test_unknown_limit_is_not_critical(self, monkeypatch):
        mod = _load_live_refresh()
        monkeypatch.setattr(mod, "_get_concurrent_metrics", lambda cw, iid: {})
        # Usage present, but no limit available for it.
        monkeypatch.setattr(mod, "_get_api_usage", lambda cw, now: {mod.HIGH_TRAFFIC_APIS[0]: 4.0})
        monkeypatch.setattr(mod, "_get_quota_limits", lambda sq: {})
        monkeypatch.setattr(mod.boto3, "client", lambda svc: object())
        snap = mod._collect_snapshot("iid")
        statuses = {q["api"]: q["status"] for q in snap["quotas"]}
        # The API with usage-but-no-limit must be 'unknown', never 'critical'.
        assert statuses[mod.HIGH_TRAFFIC_APIS[0]] == "unknown"
        assert snap["summary"]["critical"] == 0
        assert snap["summary"]["unknown"] >= 1


# ---------------------------------------------------------------------------
# quota_report_to_html.py — render every instance; don't truncate fractional TPS
# ---------------------------------------------------------------------------
class TestQuotaReportMultiInstance:
    def _data(self):
        return {
            "timestamp": "2026-09-13T04:00:00+00:00",
            "threshold_percentage": 80,
            "monitoring_results": {
                "total_quotas_checked": 3, "violations_found": 1, "account_quotas_checked": 0,
                "account_results": [],
                "instance_results": {
                    "iid-AAAA": {"instance_alias": "InstanceA", "results": [
                        {"quota_name": "Users per instance", "category": "CORE_CONNECT",
                         "current_usage": 95, "quota_limit": 100, "utilization_percentage": 95.0},
                    ]},
                    "iid-BBBB": {"instance_alias": "InstanceB", "results": [
                        {"quota_name": "Queues per instance", "category": "ROUTING_QUEUES",
                         "current_usage": 1, "quota_limit": 100, "utilization_percentage": 1.0},
                    ]},
                },
            },
        }

    def test_all_instances_rendered(self):
        import quota_report_to_html as q
        html_out = q.generate_html(self._data())
        # Both instances and both their quotas must appear (previously only the
        # last instance's tables were rendered).
        assert "InstanceA" in html_out and "iid-AAAA" in html_out
        assert "InstanceB" in html_out and "iid-BBBB" in html_out
        assert "Users per instance" in html_out
        assert "Queues per instance" in html_out

    def test_fractional_usage_not_truncated_to_zero(self):
        import quota_report_to_html as q
        data = self._data()
        data["monitoring_results"]["instance_results"]["iid-BBBB"]["results"] = [
            {"quota_name": "SearchContacts", "category": "API_RATE_LIMITS",
             "current_usage": 0.45, "quota_limit": 0.5, "utilization_percentage": 90.0},
        ]
        # API rate limits render in the account section; move it there instead.
        data["monitoring_results"]["account_results"] = [
            {"quota_name": "Rate of SearchContacts API requests", "category": "API_RATE_LIMITS",
             "service": "connect", "current_usage": 0.45, "quota_limit": 0.5, "utilization_percentage": 90.0},
        ]
        html_out = q.generate_html(data)
        assert "0.45" in html_out, "fractional TPS usage must not be truncated to 0"

    def test_fmt_num_whole_and_fraction(self):
        import quota_report_to_html as q
        assert q.fmt_num(1000) == "1,000"
        assert q.fmt_num(0.45) == "0.45"
        assert q.fmt_num(5.0) == "5"
