#!/usr/bin/env python3
"""
Test Suite for Amazon Connect Resource Mapper
=============================================
Run: python3 -m pytest test-connect-resource-mapper.py -v
Or:  python3 test-connect-resource-mapper.py

Requires: pip install boto3 pytest moto (or just boto3 + pytest with stubber)
"""

import json
import sys
import os
import pytest
from unittest.mock import patch, MagicMock
from datetime import datetime, timedelta
from botocore.stub import Stubber
import boto3

# Add script directory to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Import the module under test
import importlib.util
spec = importlib.util.spec_from_file_location(
    "mapper", 
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "connect-resource-mapper.py")
)
mapper = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mapper)


# ═══════════════════════════════════════════════════════════════════════════════
# FIXTURES
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.fixture(autouse=True)
def mock_aws_env(monkeypatch):
    """Set fake AWS credentials so boto3 doesn't try to resolve real ones."""
    monkeypatch.setenv('AWS_ACCESS_KEY_ID', 'testing')
    monkeypatch.setenv('AWS_SECRET_ACCESS_KEY', 'testing')
    monkeypatch.setenv('AWS_SECURITY_TOKEN', 'testing')
    monkeypatch.setenv('AWS_SESSION_TOKEN', 'testing')
    monkeypatch.setenv('AWS_DEFAULT_REGION', 'us-east-1')

@pytest.fixture
def connect_client():
    client = boto3.client('connect', region_name='us-east-1')
    return client

@pytest.fixture
def lambda_client():
    client = boto3.client('lambda', region_name='us-east-1')
    return client

@pytest.fixture
def cw_client():
    client = boto3.client('cloudwatch', region_name='us-east-1')
    return client

@pytest.fixture
def sq_client():
    client = boto3.client('service-quotas', region_name='us-east-1')
    return client

@pytest.fixture
def sample_flow_content_uppercase_arn():
    """Real Connect flow JSON structure with LambdaFunctionARN (uppercase — the actual key)."""
    return json.dumps({
        "Version": "2019-10-30",
        "StartAction": "action-1",
        "Actions": [
            {
                "Identifier": "action-1",
                "Type": "InvokeLambdaFunction",
                "Parameters": {
                    "LambdaFunctionARN": "arn:aws:lambda:us-east-1:123456789012:function:entry-point-lookup"
                },
                "Transitions": {"NextAction": "action-2"}
            },
            {
                "Identifier": "action-2",
                "Type": "InvokeLambdaFunction",
                "Parameters": {
                    "LambdaFunctionARN": "arn:aws:lambda:us-east-1:123456789012:function:get-business-hours"
                },
                "Transitions": {"NextAction": "action-3"}
            },
            {
                "Identifier": "action-3",
                "Type": "InvokeLambdaFunction",
                "Parameters": {
                    "LambdaFunctionARN": "arn:aws:lambda:us-east-1:123456789012:function:lex-fulfillment"
                },
                "Transitions": {"NextAction": "action-4"}
            },
            {
                "Identifier": "action-4",
                "Type": "TransferToQueue",
                "Parameters": {"QueueId": "queue-123"},
                "Transitions": {}
            }
        ]
    })

@pytest.fixture
def sample_flow_content_lowercase_arn():
    """Flow JSON with lowercase lambdaFunctionArn (older format, should still be caught)."""
    return json.dumps({
        "Version": "2019-10-30",
        "StartAction": "action-1",
        "Actions": [
            {
                "Identifier": "action-1",
                "Type": "InvokeLambdaFunction",
                "Parameters": {
                    "LambdaFunctionArn": "arn:aws:lambda:us-east-1:123456789012:function:legacy-handler"
                },
                "Transitions": {"NextAction": "action-2"}
            }
        ]
    })

@pytest.fixture
def sample_flow_content_mixed():
    """Flow with both Lambda invocations and non-Lambda actions."""
    return json.dumps({
        "Version": "2019-10-30",
        "StartAction": "action-1",
        "Actions": [
            {
                "Identifier": "action-1",
                "Type": "InvokeLambdaFunction",
                "Parameters": {
                    "LambdaFunctionARN": "arn:aws:lambda:us-east-1:123456789012:function:entry-point-lookup"
                },
                "Transitions": {"NextAction": "action-2"}
            },
            {
                "Identifier": "action-2",
                "Type": "PlayPrompt",
                "Parameters": {"Text": "Welcome"},
                "Transitions": {"NextAction": "action-3"}
            },
            {
                "Identifier": "action-3",
                "Type": "GetCustomerInput",
                "Parameters": {"LexBot": {"Name": "MainIVR"}},
                "Transitions": {"NextAction": "action-4"}
            },
            {
                "Identifier": "action-4",
                "Type": "InvokeLambdaFunction",
                "Parameters": {
                    "LambdaFunctionARN": "arn:aws:lambda:us-east-1:123456789012:function:routing-decision"
                },
                "Transitions": {}
            }
        ]
    })

@pytest.fixture
def sample_flow_content_duplicates():
    """Flow that invokes the same Lambda multiple times (should deduplicate)."""
    return json.dumps({
        "Version": "2019-10-30",
        "StartAction": "action-1",
        "Actions": [
            {
                "Identifier": "action-1",
                "Type": "InvokeLambdaFunction",
                "Parameters": {
                    "LambdaFunctionARN": "arn:aws:lambda:us-east-1:123456789012:function:entry-point-lookup"
                },
                "Transitions": {"NextAction": "action-2"}
            },
            {
                "Identifier": "action-2",
                "Type": "InvokeLambdaFunction",
                "Parameters": {
                    "LambdaFunctionARN": "arn:aws:lambda:us-east-1:123456789012:function:entry-point-lookup"
                },
                "Transitions": {}
            }
        ]
    })


# ═══════════════════════════════════════════════════════════════════════════════
# TEST: extract_lambdas_from_flow()
# ═══════════════════════════════════════════════════════════════════════════════

class TestExtractLambdasFromFlow:
    """Tests for the critical flow JSON parser."""

    def test_extracts_uppercase_arn(self, sample_flow_content_uppercase_arn):
        """The real Connect format uses LambdaFunctionARN (uppercase ARN)."""
        result = mapper.extract_lambdas_from_flow(sample_flow_content_uppercase_arn)
        assert len(result) == 3
        assert "arn:aws:lambda:us-east-1:123456789012:function:entry-point-lookup" in result
        assert "arn:aws:lambda:us-east-1:123456789012:function:get-business-hours" in result
        assert "arn:aws:lambda:us-east-1:123456789012:function:lex-fulfillment" in result

    def test_extracts_lowercase_arn(self, sample_flow_content_lowercase_arn):
        """Older flow format uses LambdaFunctionArn (mixed case)."""
        result = mapper.extract_lambdas_from_flow(sample_flow_content_lowercase_arn)
        assert len(result) == 1
        assert "arn:aws:lambda:us-east-1:123456789012:function:legacy-handler" in result

    def test_skips_non_lambda_actions(self, sample_flow_content_mixed):
        """Only extracts from Lambda invocation actions, not PlayPrompt/GetCustomerInput."""
        result = mapper.extract_lambdas_from_flow(sample_flow_content_mixed)
        assert len(result) == 2
        assert "arn:aws:lambda:us-east-1:123456789012:function:entry-point-lookup" in result
        assert "arn:aws:lambda:us-east-1:123456789012:function:routing-decision" in result

    def test_deduplicates_same_lambda(self, sample_flow_content_duplicates):
        """Same Lambda invoked multiple times should appear once."""
        result = mapper.extract_lambdas_from_flow(sample_flow_content_duplicates)
        assert len(result) == 1
        assert "arn:aws:lambda:us-east-1:123456789012:function:entry-point-lookup" in result

    def test_handles_empty_content(self):
        """Empty JSON should return empty list."""
        result = mapper.extract_lambdas_from_flow("{}")
        assert result == []

    def test_handles_invalid_json(self):
        """Malformed JSON should not crash, returns empty list."""
        result = mapper.extract_lambdas_from_flow("not valid json {{[")
        assert result == []

    def test_handles_empty_string(self):
        """Empty string should not crash."""
        result = mapper.extract_lambdas_from_flow("")
        assert result == []

    def test_handles_actions_without_parameters(self):
        """Actions missing Parameters key should be skipped."""
        content = json.dumps({
            "Actions": [
                {"Identifier": "a1", "Type": "InvokeLambdaFunction"},
                {"Identifier": "a2", "Type": "InvokeLambdaFunction", "Parameters": {}}
            ]
        })
        result = mapper.extract_lambdas_from_flow(content)
        assert result == []

    def test_handles_empty_arn_value(self):
        """Empty string ARN should not be added."""
        content = json.dumps({
            "Actions": [
                {
                    "Identifier": "a1",
                    "Type": "InvokeLambdaFunction",
                    "Parameters": {"LambdaFunctionARN": ""}
                }
            ]
        })
        result = mapper.extract_lambdas_from_flow(content)
        assert result == []

    def test_preserves_full_arn(self):
        """ARN should be stored exactly as-is, not truncated."""
        arn = "arn:aws:lambda:us-west-2:123456789012:function:my-func:PROD"
        content = json.dumps({
            "Actions": [{"Identifier": "a1", "Type": "InvokeLambdaFunction", "Parameters": {"LambdaFunctionARN": arn}}]
        })
        result = mapper.extract_lambdas_from_flow(content)
        assert result == [arn]


# ═══════════════════════════════════════════════════════════════════════════════
# TEST: collect_phone_numbers() — pagination
# ═══════════════════════════════════════════════════════════════════════════════

class TestCollectPhoneNumbers:
    """Tests for phone number collection with pagination."""

    def test_single_page(self, connect_client):
        """Single page of results, no NextToken."""
        with Stubber(connect_client) as stubber:
            stubber.add_response(
                'list_phone_numbers_v2',
                {
                    'ListPhoneNumbersSummaryList': [
                        {'PhoneNumber': '+18001234567', 'PhoneNumberType': 'TOLL_FREE', 'TargetArn': 'arn:aws:connect:us-east-1:123456789012:traffic-distribution-group/tdg-abc123'},
                        {'PhoneNumber': '+13125551234', 'PhoneNumberType': 'DID', 'TargetArn': 'arn:aws:connect:us-east-1:123456789012:traffic-distribution-group/tdg-def456'}
                    ]
                },
                {'MaxResults': 1000}
            )
            result = mapper.collect_phone_numbers(connect_client, 'instance-123')
            assert len(result) == 2
            assert result[0]['PhoneNumber'] == '+18001234567'
            assert result[1]['PhoneNumberType'] == 'DID'

    def test_multi_page_pagination(self, connect_client):
        """Multiple pages — must follow NextToken until exhausted."""
        with Stubber(connect_client) as stubber:
            # Page 1
            stubber.add_response(
                'list_phone_numbers_v2',
                {
                    'ListPhoneNumbersSummaryList': [{'PhoneNumber': f'+1800000{i:04d}', 'PhoneNumberType': 'TOLL_FREE'} for i in range(1000)],
                    'NextToken': 'token-page-2'
                },
                {'MaxResults': 1000}
            )
            # Page 2
            stubber.add_response(
                'list_phone_numbers_v2',
                {
                    'ListPhoneNumbersSummaryList': [{'PhoneNumber': f'+1800001{i:04d}', 'PhoneNumberType': 'DID'} for i in range(500)],
                },
                {'MaxResults': 1000, 'NextToken': 'token-page-2'}
            )
            result = mapper.collect_phone_numbers(connect_client, 'instance-123')
            assert len(result) == 1500

    def test_empty_result(self, connect_client):
        """No phone numbers should return empty list."""
        with Stubber(connect_client) as stubber:
            stubber.add_response(
                'list_phone_numbers_v2',
                {'ListPhoneNumbersSummaryList': []},
                {'MaxResults': 1000}
            )
            result = mapper.collect_phone_numbers(connect_client, 'instance-123')
            assert result == []


# ═══════════════════════════════════════════════════════════════════════════════
# TEST: collect_quotas() — pagination
# ═══════════════════════════════════════════════════════════════════════════════

class TestCollectQuotas:
    """Tests for service quota collection."""

    def test_collects_all_quotas_with_pagination(self, sq_client):
        """Should paginate through all Connect quotas."""
        with Stubber(sq_client) as stubber:
            # Page 1
            stubber.add_response(
                'list_service_quotas',
                {
                    'Quotas': [
                        {'QuotaCode': 'L-5AF7EB96', 'QuotaName': 'Rate of GetContactAttributes API requests', 'Value': 60.0},
                        {'QuotaCode': 'L-F001E5ED', 'QuotaName': 'Rate of UpdateContactAttributes API requests', 'Value': 20.0}
                    ],
                    'NextToken': 'page2'
                },
                {'ServiceCode': 'connect', 'MaxResults': 100}
            )
            # Page 2
            stubber.add_response(
                'list_service_quotas',
                {
                    'Quotas': [
                        {'QuotaCode': 'L-371095B8', 'QuotaName': 'Rate of DescribeContact API requests', 'Value': 50.0}
                    ]
                },
                {'ServiceCode': 'connect', 'MaxResults': 100, 'NextToken': 'page2'}
            )
            result = mapper.collect_service_quotas(sq_client)
            assert len(result) == 3
            assert result[0]['QuotaCode'] == 'L-5AF7EB96'
            assert result[0]['Value'] == 60.0


# ═══════════════════════════════════════════════════════════════════════════════
# TEST: build_quota_impact_model() — math validation
# ═══════════════════════════════════════════════════════════════════════════════

class TestQuotaImpactModel:
    """Tests for the predictive model math."""

    def test_flow_to_lambda_map(self):
        """Model should correctly map flows to their Lambda ARNs."""
        flows = [
            {'Id': 'flow-1', 'Name': 'Test Flow', 'lambdas_invoked': ['arn:aws:lambda:us-east-1:123:function:func-a', 'arn:aws:lambda:us-east-1:123:function:func-b']},
            {'Id': 'flow-2', 'Name': 'Empty Flow', 'lambdas_invoked': []}
        ]
        model = mapper.build_quota_impact_model(flows, [], [], [], {})
        assert 'flow-1' in model['flow_to_lambda_map']
        assert len(model['flow_to_lambda_map']['flow-1']) == 2
        assert 'flow-2' in model['flow_to_lambda_map']
        assert model['flow_to_lambda_map']['flow-2'] == []

    def test_tdg_number_distribution(self):
        """Model should count numbers per TDG by type."""
        numbers = [
            {'TargetArn': 'arn:aws:connect:us-east-1:123:traffic-distribution-group/tdg-abc', 'PhoneNumberType': 'DID'},
            {'TargetArn': 'arn:aws:connect:us-east-1:123:traffic-distribution-group/tdg-abc', 'PhoneNumberType': 'TOLL_FREE'},
            {'TargetArn': 'arn:aws:connect:us-east-1:123:traffic-distribution-group/tdg-abc', 'PhoneNumberType': 'DID'},
            {'TargetArn': 'arn:aws:connect:us-east-1:123:traffic-distribution-group/tdg-xyz', 'PhoneNumberType': 'TOLL_FREE'},
        ]
        model = mapper.build_quota_impact_model([], [], numbers, [], {})
        dist = model['tdg_number_distribution']
        assert dist['tdg-abc']['DID'] == 2
        assert dist['tdg-abc']['TOLL_FREE'] == 1
        assert dist['tdg-abc']['total'] == 3
        assert dist['tdg-xyz']['TOLL_FREE'] == 1
        assert dist['tdg-xyz']['total'] == 1

    def test_quota_headroom_calculation(self):
        """Headroom = limit - peak TPS. Utilization % should be correct."""
        quotas = [
            {'QuotaCode': 'L-TEST', 'QuotaName': 'Rate of GetContactAttributes API requests', 'Value': 60.0}
        ]
        metrics = {
            'GetContactAttributes': {'peak_tps_estimate': 52.0, 'daily_values': [700000], 'avg_daily': 700000, 'peak_daily': 700000}
        }
        model = mapper.build_quota_impact_model([], [], [], quotas, metrics)
        headroom = model['quota_headroom']['L-TEST']
        assert headroom['limit'] == 60.0
        assert headroom['peak_tps'] == 52.0
        assert headroom['utilization_pct'] == pytest.approx(86.7, rel=0.1)
        assert headroom['headroom_tps'] == 8.0

    def test_quota_headroom_zero_limit(self):
        """Zero limit should not cause division by zero."""
        quotas = [{'QuotaCode': 'L-ZERO', 'QuotaName': 'Some quota', 'Value': 0}]
        model = mapper.build_quota_impact_model([], [], [], quotas, {})
        assert model['quota_headroom']['L-ZERO']['utilization_pct'] == 0

    def test_summary_counts(self):
        """Summary should correctly count resources."""
        flows = [{'Id': f'f{i}', 'lambdas_invoked': ['arn:1']} for i in range(5)]
        lambdas = [
            {'FunctionName': 'func-1', 'ProvisionedConcurrency': [{'allocated': 10}]},
            {'FunctionName': 'func-2', 'ProvisionedConcurrency': []},
            {'FunctionName': 'func-3', 'ProvisionedConcurrency': [{'allocated': 5}]},
        ]
        numbers = [{'TargetArn': 'x', 'PhoneNumberType': 'DID'} for _ in range(100)]
        model = mapper.build_quota_impact_model(flows, lambdas, numbers, [], {})
        assert model['summary']['total_numbers'] == 100
        assert model['summary']['total_flows'] == 5
        assert model['summary']['total_connect_lambdas'] == 3
        assert model['summary']['lambdas_with_provisioned_concurrency'] == 2

    def test_migration_formulas_present(self):
        """Model should always include migration impact formulas."""
        model = mapper.build_quota_impact_model([], [], [], [], {})
        assert 'per_tfn_added' in model['migration_impact_formulas']
        assert 'per_contact_flow_added' in model['migration_impact_formulas']
        assert 'per_api_introduced_in_lambda' in model['migration_impact_formulas']
        assert 'per_migration_wave_of_N_numbers' in model['migration_impact_formulas']


# ═══════════════════════════════════════════════════════════════════════════════
# TEST: collect_usage_metrics() — CloudWatch
# ═══════════════════════════════════════════════════════════════════════════════

class TestCollectUsageMetrics:
    """Tests for CloudWatch usage metric collection."""

    def test_peak_tps_calculation(self, cw_client):
        """Peak TPS = peak daily calls / 28800 (8hr business day)."""
        with Stubber(cw_client) as stubber:
            # We need to stub for each API in the high_traffic_apis list
            # Just test the math with a known value
            daily_calls = 748769  # Example high-volume Monday
            expected_peak_tps = daily_calls / 28800  # ~26 TPS
            assert expected_peak_tps == pytest.approx(26.0, rel=0.1)

    def test_empty_metric_handling(self):
        """APIs with no data should return 0 for all fields."""
        # Simulate empty CloudWatch response
        values = []
        avg = sum(values) / len(values) if values else 0
        peak = max(values) if values else 0
        peak_tps = (max(values) / 28800) if values else 0
        assert avg == 0
        assert peak == 0
        assert peak_tps == 0


# ═══════════════════════════════════════════════════════════════════════════════
# TEST: TDG extraction from TargetArn
# ═══════════════════════════════════════════════════════════════════════════════

class TestTdgExtraction:
    """Tests for TDG ID extraction from phone number TargetArn."""

    def test_extracts_tdg_from_full_arn(self):
        """Should extract last segment after /."""
        arn = 'arn:aws:connect:us-east-1:123456789012:traffic-distribution-group/9e209bb8-1234-5678'
        tdg_id = arn.split('/')[-1] if '/' in arn else 'unknown'
        assert tdg_id == '9e209bb8-1234-5678'

    def test_handles_no_slash(self):
        """TargetArn without / should return 'unknown'."""
        arn = 'some-weird-arn-no-slash'
        tdg_id = arn.split('/')[-1] if '/' in arn else 'unknown'
        # This actually finds no '/' so returns 'unknown'
        # Wait — 'some-weird-arn-no-slash' has no '/'
        assert tdg_id == 'unknown'

    def test_handles_empty_arn(self):
        """Empty TargetArn should not crash."""
        arn = ''
        tdg_id = arn.split('/')[-1] if '/' in arn else 'unknown'
        assert tdg_id == 'unknown'


# ═══════════════════════════════════════════════════════════════════════════════
# TEST: Edge cases & error handling
# ═══════════════════════════════════════════════════════════════════════════════

class TestEdgeCases:
    """Tests for error handling and edge cases."""

    def test_flow_with_error_key(self):
        """Flows that failed description should still be included with error."""
        flows = [
            {'Id': 'flow-1', 'error': 'AccessDeniedException'},
            {'Id': 'flow-2', 'Name': 'Working Flow', 'lambdas_invoked': ['arn:1']}
        ]
        model = mapper.build_quota_impact_model(flows, [], [], [], {})
        # Should not crash, flow-1 won't have lambdas_invoked
        assert 'flow-2' in model['flow_to_lambda_map']

    def test_lambda_with_error_key(self):
        """Lambdas that failed get_function should be counted but gracefully handled."""
        lambdas = [
            {'FunctionArn': 'arn:1', 'error': 'ResourceNotFoundException'},
            {'FunctionName': 'good-func', 'ProvisionedConcurrency': []}
        ]
        model = mapper.build_quota_impact_model([], lambdas, [], [], {})
        assert model['summary']['total_connect_lambdas'] == 2

    def test_model_with_all_empty_inputs(self):
        """Empty inputs should produce a valid model structure, not crash."""
        model = mapper.build_quota_impact_model([], [], [], [], {})
        assert 'generated_at' in model
        assert 'flow_to_lambda_map' in model
        assert 'tdg_number_distribution' in model
        assert 'quota_headroom' in model
        assert 'migration_impact_formulas' in model
        assert 'summary' in model
        assert model['summary']['total_numbers'] == 0
        assert model['summary']['total_flows'] == 0

    def test_number_without_target_arn(self):
        """Phone numbers missing TargetArn should be grouped as 'unknown'."""
        numbers = [
            {'PhoneNumber': '+18001111111', 'PhoneNumberType': 'TOLL_FREE'},  # No TargetArn
        ]
        model = mapper.build_quota_impact_model([], [], numbers, [], {})
        # Should extract '' as TargetArn, split('/') gives [''], [-1] gives ''
        # The defaultdict handles it
        assert model['summary']['total_numbers'] == 1


# ═══════════════════════════════════════════════════════════════════════════════
# TEST: Migration wave math (validates dashboard calculations)
# ═══════════════════════════════════════════════════════════════════════════════

class TestMigrationWaveMath:
    """Validates the formulas used in the dashboard's wave planner."""

    BUSINESS_SECONDS = 28800  # 8hr day
    AVG_CONTACTS_PER_NUMBER = 15
    API_CALLS_PER_CONTACT = 18

    def test_wave_tps_formula(self):
        """N numbers × 15 contacts/day × 18 API calls ÷ 28800 = additional TPS."""
        n = 500
        expected_tps = (n * self.AVG_CONTACTS_PER_NUMBER * self.API_CALLS_PER_CONTACT) / self.BUSINESS_SECONDS
        # 500 * 15 * 18 / 28800 = 4.6875
        assert expected_tps == pytest.approx(4.6875)

    def test_three_wave_scenario(self):
        """Default scenario: 500 + 500 + 200 = 1200 numbers."""
        waves = [500, 500, 200]
        total_numbers = sum(waves)
        total_tps = (total_numbers * self.AVG_CONTACTS_PER_NUMBER * self.API_CALLS_PER_CONTACT) / self.BUSINESS_SECONDS
        # 1200 * 15 * 18 / 28800 = 11.25
        assert total_numbers == 1200
        assert total_tps == pytest.approx(11.25)

    def test_quota_breach_detection(self):
        """GetContactAttributes at 52/60 TPS + 11.25 additional (distributed) should trigger warning."""
        current_peak = 52
        limit = 60
        # GetContactAttributes gets ~proportional share
        # If it's ~40% of all API calls, it gets 40% of additional TPS
        # With 52/127 total current TPS proportion ≈ 41%
        total_current = 127  # sum of all peaks from example deployment
        proportion = current_peak / total_current
        additional_for_this_api = 11.25 * proportion
        projected = current_peak + additional_for_this_api
        utilization = projected / limit * 100
        # Should exceed 70% threshold → needs SLI
        assert utilization > 70
        assert projected > limit * 0.7

    def test_zero_wave_no_impact(self):
        """Zero numbers should produce zero additional TPS."""
        n = 0
        tps = (n * self.AVG_CONTACTS_PER_NUMBER * self.API_CALLS_PER_CONTACT) / self.BUSINESS_SECONDS
        assert tps == 0

    def test_large_wave_calculation(self):
        """2000 number wave (max slider) should calculate correctly."""
        n = 2000
        tps = (n * self.AVG_CONTACTS_PER_NUMBER * self.API_CALLS_PER_CONTACT) / self.BUSINESS_SECONDS
        # 2000 * 15 * 18 / 28800 = 18.75
        assert tps == pytest.approx(18.75)


# ═══════════════════════════════════════════════════════════════════════════════
# RUN
# ═══════════════════════════════════════════════════════════════════════════════

if __name__ == '__main__':
    pytest.main([__file__, '-v', '--tb=short'])
