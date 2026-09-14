#!/usr/bin/env python3
"""
Tests for CloudWatch MetricGroup dimension fix.

Validates that _monitor_via_cloudwatch correctly includes the MetricGroup
dimension for concurrent metrics (calls, chats, tasks) and handles
fallback scenarios.

Bug: The original code queried ConcurrentCalls with only the InstanceId
dimension. On production instances, Connect publishes these metrics with
an additional MetricGroup dimension (VoiceCalls, Chats, Tasks). Without
that dimension, CloudWatch returns empty datapoints and the monitor
reports 0 for all concurrent metrics.

Fix: Add metric_group to quota definitions and include MetricGroup
dimension in the CloudWatch query. Falls back to querying without
MetricGroup for backward compatibility with smaller instances.
"""

import unittest
import sys
import os
from unittest.mock import Mock, patch
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import lambda_function


class TestCloudWatchMetricGroupDimension(unittest.TestCase):
    """Test that concurrent metrics include MetricGroup dimension."""

    def test_metric_config_has_metric_group(self):
        """Verify metric definitions include metric_group field."""
        metrics = lambda_function.ENHANCED_CONNECT_QUOTA_METRICS

        # ConcurrentCalls must have metric_group=VoiceCalls
        calls_config = metrics['L-12AB7C57']
        self.assertEqual(calls_config['metric_group'], 'VoiceCalls')
        self.assertEqual(calls_config['metric_name'], 'ConcurrentCalls')

        # ConcurrentActiveChats must have metric_group=Chats
        chats_config = metrics['L-D4BA6F6E']
        self.assertEqual(chats_config['metric_group'], 'Chats')
        self.assertEqual(chats_config['metric_name'], 'ConcurrentActiveChats')

        # ConcurrentActiveTasks must have metric_group=Tasks
        tasks_config = metrics['L-60553137']
        self.assertEqual(tasks_config['metric_group'], 'Tasks')
        self.assertEqual(tasks_config['metric_name'], 'ConcurrentActiveTasks')

    def test_concurrent_calls_has_no_percentage_fallback(self):
        """ConcurrentCalls must NOT fall back to a percentage metric: a percentage
        is not a count, and dividing it by the count limit produced false 800%
        CRITICAL alerts. The invalid fallback was removed."""
        metrics = lambda_function.ENHANCED_CONNECT_QUOTA_METRICS
        calls_config = metrics['L-12AB7C57']
        self.assertNotIn('percent', calls_config.get('metric_name_fallback', '').lower())


class TestMonitorViaCloudWatch(unittest.TestCase):
    """Test _monitor_via_cloudwatch includes MetricGroup in dimensions."""

    def setUp(self):
        """Set up test fixtures with mocked monitor."""
        self.instance_id = 'test-instance-123'

    @patch('lambda_function.boto3.Session')
    def test_includes_metric_group_dimension(self, mock_session):
        """When metric_group is set, MetricGroup dimension must be included."""
        mock_session.return_value.get_credentials.return_value = Mock()
        mock_session.return_value.region_name = 'us-east-1'

        with patch.object(lambda_function.ConnectQuotaMonitor, '__init__', lambda x: None):
            monitor = lambda_function.ConnectQuotaMonitor()
            monitor.cloudwatch_client = Mock()
            monitor.client_manager = Mock()

            # Mock successful response with MetricGroup dimension
            monitor.call_service_api = Mock(return_value={
                'Datapoints': [
                    {'Timestamp': datetime.now(timezone.utc), 'Maximum': 150.0}
                ]
            })

            metric_config = {
                'metric_name': 'ConcurrentCalls',
                'namespace': 'AWS/Connect',
                'statistic': 'Maximum',
                'scope': 'INSTANCE',
                'metric_group': 'VoiceCalls'
            }

            result = monitor._monitor_via_cloudwatch(self.instance_id, metric_config)

            # Verify the call included MetricGroup dimension
            call_args = monitor.call_service_api.call_args
            dimensions = call_args.kwargs.get('Dimensions', call_args[1].get('Dimensions', []))

            dimension_names = [d['Name'] for d in dimensions]
            self.assertIn('InstanceId', dimension_names)
            self.assertIn('MetricGroup', dimension_names)

            # Verify the values
            dim_dict = {d['Name']: d['Value'] for d in dimensions}
            self.assertEqual(dim_dict['InstanceId'], self.instance_id)
            self.assertEqual(dim_dict['MetricGroup'], 'VoiceCalls')

            # Verify result
            self.assertEqual(result, 150)

    @patch('lambda_function.boto3.Session')
    def test_no_metric_group_for_other_quotas(self, mock_session):
        """When metric_group is not set, only InstanceId dimension is used."""
        mock_session.return_value.get_credentials.return_value = Mock()
        mock_session.return_value.region_name = 'us-east-1'

        with patch.object(lambda_function.ConnectQuotaMonitor, '__init__', lambda x: None):
            monitor = lambda_function.ConnectQuotaMonitor()
            monitor.call_service_api = Mock(return_value={
                'Datapoints': [
                    {'Timestamp': datetime.now(timezone.utc), 'Maximum': 50.0}
                ]
            })

            # Config without metric_group (like campaign calls)
            metric_config = {
                'metric_name': 'SomeOtherMetric',
                'namespace': 'AWS/Connect',
                'statistic': 'Maximum',
                'scope': 'INSTANCE'
            }

            monitor._monitor_via_cloudwatch(self.instance_id, metric_config)

            # Verify only InstanceId dimension
            call_args = monitor.call_service_api.call_args
            dimensions = call_args.kwargs.get('Dimensions', call_args[1].get('Dimensions', []))

            dimension_names = [d['Name'] for d in dimensions]
            self.assertIn('InstanceId', dimension_names)
            self.assertNotIn('MetricGroup', dimension_names)

    @patch('lambda_function.boto3.Session')
    def test_fallback_metric_name(self, mock_session):
        """When primary metric returns no data, tries fallback metric name."""
        mock_session.return_value.get_credentials.return_value = Mock()
        mock_session.return_value.region_name = 'us-east-1'

        with patch.object(lambda_function.ConnectQuotaMonitor, '__init__', lambda x: None):
            monitor = lambda_function.ConnectQuotaMonitor()

            # First call returns empty (ConcurrentCalls), second returns data (fallback)
            monitor.call_service_api = Mock(side_effect=[
                {'Datapoints': []},  # Primary metric: no data
                {'Datapoints': [{'Timestamp': datetime.now(timezone.utc), 'Maximum': 85.0}]},  # Fallback
            ])

            # Use a non-percentage fallback: the fallback mechanism is still valid
            # for genuine count metrics; only percentage metrics are rejected.
            metric_config = {
                'metric_name': 'ConcurrentCalls',
                'metric_name_fallback': 'ConcurrentCallsAlt',
                'namespace': 'AWS/Connect',
                'statistic': 'Maximum',
                'scope': 'INSTANCE',
                'metric_group': 'VoiceCalls'
            }

            result = monitor._monitor_via_cloudwatch(self.instance_id, metric_config)

            # Should have called twice (primary then fallback)
            self.assertEqual(monitor.call_service_api.call_count, 2)

            # Second call should use the fallback metric name
            second_call = monitor.call_service_api.call_args_list[1]
            self.assertEqual(
                second_call.kwargs.get('MetricName', second_call[1].get('MetricName')),
                'ConcurrentCallsAlt'
            )

            self.assertEqual(result, 85)

    @patch('lambda_function.boto3.Session')
    def test_fallback_without_metric_group(self, mock_session):
        """When both primary and fallback fail, tries without MetricGroup (backward compat)."""
        mock_session.return_value.get_credentials.return_value = Mock()
        mock_session.return_value.region_name = 'us-east-1'

        with patch.object(lambda_function.ConnectQuotaMonitor, '__init__', lambda x: None):
            monitor = lambda_function.ConnectQuotaMonitor()

            # First two calls empty, third (no MetricGroup) returns data
            monitor.call_service_api = Mock(side_effect=[
                {'Datapoints': []},  # Primary with MetricGroup: no data
                {'Datapoints': []},  # Fallback with MetricGroup: no data
                {'Datapoints': [{'Timestamp': datetime.now(timezone.utc), 'Maximum': 200.0}]},  # No MetricGroup
            ])

            metric_config = {
                'metric_name': 'ConcurrentCalls',
                'metric_name_fallback': 'ConcurrentHighVolumeCallsPercentage',
                'namespace': 'AWS/Connect',
                'statistic': 'Maximum',
                'scope': 'INSTANCE',
                'metric_group': 'VoiceCalls'
            }

            result = monitor._monitor_via_cloudwatch(self.instance_id, metric_config)

            # Third call should NOT have MetricGroup dimension
            third_call = monitor.call_service_api.call_args_list[2]
            dimensions = third_call.kwargs.get('Dimensions', third_call[1].get('Dimensions', []))
            dimension_names = [d['Name'] for d in dimensions]
            self.assertNotIn('MetricGroup', dimension_names)
            self.assertIn('InstanceId', dimension_names)

            self.assertEqual(result, 200)

    @patch('lambda_function.boto3.Session')
    def test_returns_zero_when_all_fallbacks_fail(self, mock_session):
        """When all query variations return empty, returns 0 (not None)."""
        mock_session.return_value.get_credentials.return_value = Mock()
        mock_session.return_value.region_name = 'us-east-1'

        with patch.object(lambda_function.ConnectQuotaMonitor, '__init__', lambda x: None):
            monitor = lambda_function.ConnectQuotaMonitor()
            monitor.call_service_api = Mock(return_value={'Datapoints': []})

            metric_config = {
                'metric_name': 'ConcurrentCalls',
                'metric_name_fallback': 'ConcurrentHighVolumeCallsPercentage',
                'namespace': 'AWS/Connect',
                'statistic': 'Maximum',
                'scope': 'INSTANCE',
                'metric_group': 'VoiceCalls'
            }

            result = monitor._monitor_via_cloudwatch(self.instance_id, metric_config)

            # Should return 0 (not None) when no data anywhere
            self.assertEqual(result, 0)

    @patch('lambda_function.boto3.Session')
    def test_returns_none_on_exception(self, mock_session):
        """On API exception, returns None (distinct from 0)."""
        mock_session.return_value.get_credentials.return_value = Mock()
        mock_session.return_value.region_name = 'us-east-1'

        with patch.object(lambda_function.ConnectQuotaMonitor, '__init__', lambda x: None):
            monitor = lambda_function.ConnectQuotaMonitor()
            monitor.call_service_api = Mock(side_effect=Exception("AccessDenied"))

            metric_config = {
                'metric_name': 'ConcurrentCalls',
                'namespace': 'AWS/Connect',
                'statistic': 'Maximum',
                'scope': 'INSTANCE',
                'metric_group': 'VoiceCalls'
            }

            result = monitor._monitor_via_cloudwatch(self.instance_id, metric_config)
            self.assertIsNone(result)


class TestCloudWatchQueryViaRealClient(unittest.TestCase):
    """Integration test of the monitor's CloudWatch code path against a real
    boto3 client, deterministically and offline via botocore's Stubber.

    The previous version of this test published a metric to the live
    ``AWS/Connect`` namespace and queried it back. That can never pass: AWS
    reserves the ``AWS/*`` namespaces, so ``put_metric_data`` into them is
    silently dropped and the follow-up query is always empty. moto is no help
    either -- its ``get_metric_statistics`` does not honour dimension filters.

    Stubber gives us the real botocore client and request validation without a
    network call: it asserts the request the monitor builds (including the
    MetricGroup dimension) and returns a controlled response, so the whole path
    -- ``_monitor_via_cloudwatch`` -> ``call_service_api`` ->
    ``get_metric_statistics`` -> datapoint parsing -> int conversion -- is
    exercised for real."""

    INSTANCE_ID = '00000000-0000-0000-0000-000000000001'

    def _monitor_with_client(self, cw_client):
        """A ConnectQuotaMonitor whose only wired dependency is a CloudWatch
        client, built without running the AWS-touching __init__."""
        monitor = lambda_function.ConnectQuotaMonitor.__new__(lambda_function.ConnectQuotaMonitor)
        monitor.get_service_client = lambda service_name: cw_client
        return monitor

    def test_query_with_metric_group_returns_value(self):
        """With metric_group set, the monitor queries InstanceId + MetricGroup and
        returns the datapoint value. Stubber's expected_params fails the test if
        the MetricGroup dimension is missing from the request -- i.e. the bug the
        fix addressed."""
        import boto3
        from botocore.stub import Stubber, ANY

        cw = boto3.client('cloudwatch', region_name='us-east-1')
        stubber = Stubber(cw)
        stubber.add_response(
            'get_metric_statistics',
            {'Label': 'ConcurrentCalls',
             'Datapoints': [{'Timestamp': datetime.now(timezone.utc), 'Maximum': 999.0, 'Unit': 'Count'}]},
            expected_params={
                'Namespace': 'AWS/Connect',
                'MetricName': 'ConcurrentCalls',
                'Dimensions': [
                    {'Name': 'InstanceId', 'Value': self.INSTANCE_ID},
                    {'Name': 'MetricGroup', 'Value': 'VoiceCalls'},
                ],
                'StartTime': ANY, 'EndTime': ANY, 'Period': 300, 'Statistics': ['Maximum'],
            },
        )
        monitor = self._monitor_with_client(cw)
        metric_config = {
            'metric_name': 'ConcurrentCalls', 'namespace': 'AWS/Connect',
            'statistic': 'Maximum', 'scope': 'INSTANCE', 'metric_group': 'VoiceCalls',
        }
        with stubber:
            result = monitor._monitor_via_cloudwatch(self.INSTANCE_ID, metric_config)
        self.assertEqual(result, 999)
        stubber.assert_no_pending_responses()

    def test_falls_back_to_query_without_metric_group(self):
        """Backward compatibility: when the grouped query returns no data, the
        monitor re-queries without the MetricGroup dimension (smaller instances
        publish these metrics with InstanceId only) and returns that value."""
        import boto3
        from botocore.stub import Stubber, ANY

        cw = boto3.client('cloudwatch', region_name='us-east-1')
        stubber = Stubber(cw)
        # First call (with MetricGroup): no data.
        stubber.add_response(
            'get_metric_statistics',
            {'Label': 'ConcurrentCalls', 'Datapoints': []},
            expected_params={
                'Namespace': 'AWS/Connect', 'MetricName': 'ConcurrentCalls',
                'Dimensions': [
                    {'Name': 'InstanceId', 'Value': self.INSTANCE_ID},
                    {'Name': 'MetricGroup', 'Value': 'VoiceCalls'},
                ],
                'StartTime': ANY, 'EndTime': ANY, 'Period': 300, 'Statistics': ['Maximum'],
            },
        )
        # Fallback call (InstanceId only): data present.
        stubber.add_response(
            'get_metric_statistics',
            {'Label': 'ConcurrentCalls',
             'Datapoints': [{'Timestamp': datetime.now(timezone.utc), 'Maximum': 42.0, 'Unit': 'Count'}]},
            expected_params={
                'Namespace': 'AWS/Connect', 'MetricName': 'ConcurrentCalls',
                'Dimensions': [{'Name': 'InstanceId', 'Value': self.INSTANCE_ID}],
                'StartTime': ANY, 'EndTime': ANY, 'Period': 300, 'Statistics': ['Maximum'],
            },
        )
        monitor = self._monitor_with_client(cw)
        metric_config = {
            'metric_name': 'ConcurrentCalls', 'namespace': 'AWS/Connect',
            'statistic': 'Maximum', 'scope': 'INSTANCE', 'metric_group': 'VoiceCalls',
        }
        with stubber:
            result = monitor._monitor_via_cloudwatch(self.INSTANCE_ID, metric_config)
        self.assertEqual(result, 42)
        stubber.assert_no_pending_responses()


if __name__ == '__main__':
    unittest.main()
