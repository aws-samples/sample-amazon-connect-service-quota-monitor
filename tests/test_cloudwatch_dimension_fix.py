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
from unittest.mock import Mock, patch, MagicMock, call
from datetime import datetime, timedelta

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

    def test_metric_config_has_fallback_for_calls(self):
        """Verify ConcurrentCalls has a fallback metric name."""
        metrics = lambda_function.ENHANCED_CONNECT_QUOTA_METRICS
        calls_config = metrics['L-12AB7C57']
        self.assertEqual(
            calls_config['metric_name_fallback'],
            'ConcurrentHighVolumeCallsPercentage'
        )


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
                    {'Timestamp': datetime.utcnow(), 'Maximum': 150.0}
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
                    {'Timestamp': datetime.utcnow(), 'Maximum': 50.0}
                ]
            })

            # Config without metric_group (like campaign calls)
            metric_config = {
                'metric_name': 'SomeOtherMetric',
                'namespace': 'AWS/Connect',
                'statistic': 'Maximum',
                'scope': 'INSTANCE'
            }

            result = monitor._monitor_via_cloudwatch(self.instance_id, metric_config)

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
                {'Datapoints': [{'Timestamp': datetime.utcnow(), 'Maximum': 85.0}]},  # Fallback
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

            # Should have called twice (primary then fallback)
            self.assertEqual(monitor.call_service_api.call_count, 2)

            # Second call should use the fallback metric name
            second_call = monitor.call_service_api.call_args_list[1]
            self.assertEqual(
                second_call.kwargs.get('MetricName', second_call[1].get('MetricName')),
                'ConcurrentHighVolumeCallsPercentage'
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
                {'Datapoints': [{'Timestamp': datetime.utcnow(), 'Maximum': 200.0}]},  # No MetricGroup
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


class TestIntegrationCloudWatchFix(unittest.TestCase):
    """Integration tests using real AWS API (requires credentials)."""

    INSTANCE_ID = os.environ.get('CONNECT_INSTANCE_ID', '6c3f17c0-3b52-4990-9c42-e27dd792b385')
    REGION = os.environ.get('AWS_REGION', 'us-east-1')

    @unittest.skipUnless(
        os.environ.get('RUN_INTEGRATION_TESTS', 'false').lower() == 'true',
        "Set RUN_INTEGRATION_TESTS=true to run integration tests"
    )
    def test_real_cloudwatch_query_with_metric_group(self):
        """Test against real CloudWatch with injected metrics."""
        import boto3

        cw = boto3.client('cloudwatch', region_name=self.REGION)

        # Inject test metric
        cw.put_metric_data(
            Namespace='AWS/Connect',
            MetricData=[{
                'MetricName': 'ConcurrentCalls',
                'Dimensions': [
                    {'Name': 'InstanceId', 'Value': self.INSTANCE_ID},
                    {'Name': 'MetricGroup', 'Value': 'VoiceCalls'}
                ],
                'Value': 999.0,
                'Unit': 'Count'
            }]
        )

        import time
        time.sleep(3)

        # Query WITHOUT MetricGroup (old bug)
        response_old = cw.get_metric_statistics(
            Namespace='AWS/Connect',
            MetricName='ConcurrentCalls',
            Dimensions=[{'Name': 'InstanceId', 'Value': self.INSTANCE_ID}],
            StartTime=datetime.utcnow() - timedelta(minutes=15),
            EndTime=datetime.utcnow(),
            Period=300,
            Statistics=['Maximum']
        )

        # Query WITH MetricGroup (fix)
        response_new = cw.get_metric_statistics(
            Namespace='AWS/Connect',
            MetricName='ConcurrentCalls',
            Dimensions=[
                {'Name': 'InstanceId', 'Value': self.INSTANCE_ID},
                {'Name': 'MetricGroup', 'Value': 'VoiceCalls'}
            ],
            StartTime=datetime.utcnow() - timedelta(minutes=15),
            EndTime=datetime.utcnow(),
            Period=300,
            Statistics=['Maximum']
        )

        # Old query should have no data, new query should have data
        self.assertEqual(len(response_old['Datapoints']), 0,
                         "Old query (InstanceId only) should return empty")
        self.assertGreater(len(response_new['Datapoints']), 0,
                           "New query (InstanceId + MetricGroup) should return data")
        self.assertEqual(response_new['Datapoints'][0]['Maximum'], 999.0)


if __name__ == '__main__':
    unittest.main()
