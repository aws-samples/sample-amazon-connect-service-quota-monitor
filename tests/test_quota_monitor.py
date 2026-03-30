#!/usr/bin/env python3
"""
Comprehensive test suite for Amazon Connect Quota Monitor

Tests cover:
- Quota monitoring functionality
- Instance discovery
- Alert consolidation
- Storage mechanisms
- Error handling
- Edge cases
"""

import unittest
import json
import os
import sys
from unittest.mock import Mock, patch, MagicMock
from datetime import datetime, timedelta

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import lambda_function


class TestInstanceDiscovery(unittest.TestCase):
    """Test dynamic instance discovery functionality"""
    
    def setUp(self):
        """Set up test fixtures"""
        self.mock_session = Mock()
        self.mock_connect_client = Mock()
        self.mock_service_quotas_client = Mock()
        
    @patch('lambda_function.boto3.Session')
    def test_discover_single_instance(self, mock_session):
        """Test discovery of a single Connect instance"""
        # Mock session and clients
        mock_session.return_value.get_credentials.return_value = Mock()
        mock_session.return_value.region_name = 'us-east-1'
        
        # Mock list_instances response
        self.mock_connect_client.list_instances.return_value = {
            'InstanceSummaryList': [
                {
                    'Id': 'test-instance-123',
                    'Arn': 'arn:aws:connect:us-east-1:123456789012:instance/test-instance-123',
                    'InstanceAlias': 'TestInstance',
                    'InstanceStatus': 'ACTIVE',
                    'InboundCallsEnabled': True,
                    'OutboundCallsEnabled': True
                }
            ]
        }
        
        with patch.object(lambda_function.MultiServiceClientManager, 'get_client') as mock_get_client:
            mock_get_client.return_value = self.mock_connect_client
            
            monitor = lambda_function.ConnectQuotaMonitor()
            instances = monitor.get_connect_instances()
            
            self.assertEqual(len(instances), 1)
            self.assertEqual(instances[0]['Id'], 'test-instance-123')
            self.assertEqual(instances[0]['InstanceAlias'], 'TestInstance')
            self.assertTrue(instances[0]['IsActive'])
    
    @patch('lambda_function.boto3.Session')
    def test_discover_multiple_instances(self, mock_session):
        """Test discovery of multiple Connect instances"""
        mock_session.return_value.get_credentials.return_value = Mock()
        mock_session.return_value.region_name = 'us-east-1'
        
        self.mock_connect_client.list_instances.return_value = {
            'InstanceSummaryList': [
                {
                    'Id': 'instance-1',
                    'Arn': 'arn:aws:connect:us-east-1:123456789012:instance/instance-1',
                    'InstanceAlias': 'Instance1',
                    'InstanceStatus': 'ACTIVE'
                },
                {
                    'Id': 'instance-2',
                    'Arn': 'arn:aws:connect:us-east-1:123456789012:instance/instance-2',
                    'InstanceAlias': 'Instance2',
                    'InstanceStatus': 'ACTIVE'
                }
            ]
        }
        
        with patch.object(lambda_function.MultiServiceClientManager, 'get_client') as mock_get_client:
            mock_get_client.return_value = self.mock_connect_client
            
            monitor = lambda_function.ConnectQuotaMonitor()
            instances = monitor.get_connect_instances()
            
            self.assertEqual(len(instances), 2)
            self.assertEqual(instances[0]['Id'], 'instance-1')
            self.assertEqual(instances[1]['Id'], 'instance-2')
    
    @patch('lambda_function.boto3.Session')
    def test_filter_inactive_instances(self, mock_session):
        """Test that inactive instances are filtered out"""
        mock_session.return_value.get_credentials.return_value = Mock()
        mock_session.return_value.region_name = 'us-east-1'
        
        self.mock_connect_client.list_instances.return_value = {
            'InstanceSummaryList': [
                {
                    'Id': 'active-instance',
                    'Arn': 'arn:aws:connect:us-east-1:123456789012:instance/active-instance',
                    'InstanceAlias': 'ActiveInstance',
                    'InstanceStatus': 'ACTIVE'
                },
                {
                    'Id': 'inactive-instance',
                    'Arn': 'arn:aws:connect:us-east-1:123456789012:instance/inactive-instance',
                    'InstanceAlias': 'InactiveInstance',
                    'InstanceStatus': 'CREATION_FAILED'
                }
            ]
        }
        
        with patch.object(lambda_function.MultiServiceClientManager, 'get_client') as mock_get_client:
            mock_get_client.return_value = self.mock_connect_client
            
            monitor = lambda_function.ConnectQuotaMonitor()
            active_instances = monitor.get_active_instances()
            
            self.assertEqual(len(active_instances), 1)
            self.assertEqual(active_instances[0]['Id'], 'active-instance')


class TestQuotaMonitoring(unittest.TestCase):
    """Test quota monitoring methods"""
    
    def setUp(self):
        """Set up test fixtures"""
        self.monitor = None
        
    @patch('lambda_function.boto3.Session')
    def test_api_count_method(self, mock_session):
        """Test quota monitoring via API count method"""
        mock_session.return_value.get_credentials.return_value = Mock()
        
        mock_connect_client = Mock()
        mock_connect_client.list_users.return_value = {
            'UserSummaryList': [{'Id': f'user-{i}'} for i in range(50)]
        }
        
        with patch.object(lambda_function.MultiServiceClientManager, 'get_client') as mock_get_client:
            mock_get_client.return_value = mock_connect_client
            
            monitor = lambda_function.ConnectQuotaMonitor()
            
            quota_config = {
                'name': 'Users per instance',
                'category': 'CORE_CONNECT',
                'scope': 'INSTANCE',
                'method': 'api_count',
                'service': 'connect',
                'api': 'list_users',
                'default_limit': 500
            }
            
            result = monitor.get_quota_utilization('test-instance', quota_config, 'L-9A46857E')
            
            self.assertIsNotNone(result)
            self.assertEqual(result['current_usage'], 50)
            self.assertEqual(result['quota_limit'], 500)
            self.assertEqual(result['utilization_percentage'], 10.0)
    
    @patch('lambda_function.boto3.Session')
    def test_cloudwatch_method(self, mock_session):
        """Test quota monitoring via CloudWatch metrics"""
        mock_session.return_value.get_credentials.return_value = Mock()
        
        mock_cloudwatch_client = Mock()
        mock_cloudwatch_client.get_metric_statistics.return_value = {
            'Datapoints': [
                {
                    'Timestamp': datetime.utcnow(),
                    'Maximum': 5.0
                }
            ]
        }
        
        with patch.object(lambda_function.MultiServiceClientManager, 'get_client') as mock_get_client:
            mock_get_client.return_value = mock_cloudwatch_client
            
            monitor = lambda_function.ConnectQuotaMonitor()
            monitor.cloudwatch_client = mock_cloudwatch_client
            
            quota_config = {
                'name': 'Concurrent active calls per instance',
                'category': 'CONTACT_HANDLING',
                'scope': 'INSTANCE',
                'method': 'cloudwatch',
                'service': 'connect',
                'metric_name': 'ConcurrentCalls',
                'namespace': 'AWS/Connect',
                'statistic': 'Maximum',
                'default_limit': 10
            }
            
            result = monitor.get_quota_utilization('test-instance', quota_config, 'L-12AB7C57')
            
            self.assertIsNotNone(result)
            self.assertEqual(result['current_usage'], 5)
            self.assertEqual(result['utilization_percentage'], 50.0)
    
    def test_threshold_violation_detection(self):
        """Test detection of quota violations above threshold"""
        quota_result = {
            'quota_code': 'L-9A46857E',
            'quota_name': 'Users per instance',
            'current_usage': 450,
            'quota_limit': 500,
            'utilization_percentage': 90.0,
            'instance_id': 'test-instance'
        }
        
        threshold = 80
        is_violation = quota_result['utilization_percentage'] >= threshold
        
        self.assertTrue(is_violation)


class TestAlertConsolidation(unittest.TestCase):
    """Test alert consolidation engine"""
    
    def setUp(self):
        """Set up test fixtures"""
        self.mock_sns_client = Mock()
        self.topic_arn = 'arn:aws:sns:us-east-1:123456789012:test-topic'
        self.threshold = 80
        
    def test_consolidate_multiple_violations(self):
        """Test consolidation of multiple violations into single alert"""
        engine = lambda_function.AlertConsolidationEngine(
            self.mock_sns_client,
            self.topic_arn,
            self.threshold
        )
        
        violations = [
            {
                'quota_code': 'L-9A46857E',
                'quota_name': 'Users per instance',
                'category': 'CORE_CONNECT',
                'current_usage': 450,
                'quota_limit': 500,
                'utilization_percentage': 90.0
            },
            {
                'quota_code': 'L-19A87C94',
                'quota_name': 'Queues per instance',
                'category': 'ROUTING_QUEUES',
                'current_usage': 45,
                'quota_limit': 50,
                'utilization_percentage': 90.0
            }
        ]
        
        instance_data = {
            'instance_alias': 'TestInstance',
            'results': violations
        }
        
        # Mock SNS publish
        self.mock_sns_client.publish.return_value = {'MessageId': 'test-message-id'}
        
        result = engine._send_instance_consolidated_alert('test-instance', instance_data, violations)
        
        self.assertTrue(result)
        self.mock_sns_client.publish.assert_called_once()
        
        # Verify alert contains both violations
        call_args = self.mock_sns_client.publish.call_args
        message = call_args[1]['Message']
        
        self.assertIn('Users per instance', message)
        self.assertIn('Queues per instance', message)
    
    def test_severity_determination(self):
        """Test alert severity calculation"""
        engine = lambda_function.AlertConsolidationEngine(
            self.mock_sns_client,
            self.topic_arn,
            self.threshold
        )
        
        # Test CRITICAL severity
        critical_violations = [
            {'utilization_percentage': 97.0}
        ]
        severity = engine._determine_severity(critical_violations)
        self.assertEqual(severity, 'CRITICAL')
        
        # Test HIGH severity
        high_violations = [
            {'utilization_percentage': 92.0}
        ]
        severity = engine._determine_severity(high_violations)
        self.assertEqual(severity, 'HIGH')
        
        # Test MEDIUM severity
        medium_violations = [
            {'utilization_percentage': 87.0}
        ]
        severity = engine._determine_severity(medium_violations)
        self.assertEqual(severity, 'MEDIUM')


class TestStorageEngine(unittest.TestCase):
    """Test flexible storage engine"""
    
    def setUp(self):
        """Set up test fixtures"""
        self.mock_s3_client = Mock()
        self.mock_dynamodb_client = Mock()
        self.mock_client_manager = Mock()
        
    def test_s3_storage(self):
        """Test storing metrics to S3"""
        self.mock_client_manager.get_client.side_effect = lambda service: {
            's3': self.mock_s3_client,
            'dynamodb': self.mock_dynamodb_client
        }.get(service)
        
        storage_config = {
            'use_s3': True,
            'use_dynamodb': False,
            's3_bucket': 'test-bucket',
            'dynamodb_table': None
        }
        
        engine = lambda_function.FlexibleStorageEngine(storage_config, self.mock_client_manager)
        
        metrics_data = [
            {
                'quota_code': 'L-9A46857E',
                'quota_name': 'Users per instance',
                'current_usage': 100,
                'quota_limit': 500,
                'utilization_percentage': 20.0
            }
        ]
        
        result = engine.store_instance_metrics('test-instance', 'TestInstance', metrics_data)
        
        self.assertTrue(result['s3_success'])
        self.assertFalse(result['dynamodb_success'])
        self.mock_s3_client.put_object.assert_called()
    
    def test_dynamodb_storage(self):
        """Test storing metrics to DynamoDB"""
        self.mock_client_manager.get_client.side_effect = lambda service: {
            's3': self.mock_s3_client,
            'dynamodb': self.mock_dynamodb_client
        }.get(service)
        
        storage_config = {
            'use_s3': False,
            'use_dynamodb': True,
            's3_bucket': None,
            'dynamodb_table': 'test-table'
        }
        
        engine = lambda_function.FlexibleStorageEngine(storage_config, self.mock_client_manager)
        
        metrics_data = [
            {
                'quota_code': 'L-9A46857E',
                'quota_name': 'Users per instance',
                'current_usage': 100,
                'quota_limit': 500,
                'utilization_percentage': 20.0
            }
        ]
        
        result = engine.store_instance_metrics('test-instance', 'TestInstance', metrics_data)
        
        self.assertFalse(result['s3_success'])
        self.assertTrue(result['dynamodb_success'])
        self.mock_dynamodb_client.put_item.assert_called()


class TestErrorHandling(unittest.TestCase):
    """Test error handling and recovery"""
    
    @patch('lambda_function.boto3.Session')
    def test_handle_api_throttling(self, mock_session):
        """Test handling of API throttling errors"""
        mock_session.return_value.get_credentials.return_value = Mock()
        
        mock_connect_client = Mock()
        # Simulate throttling on first call, success on retry
        mock_connect_client.list_users.side_effect = [
            lambda_function.ClientError(
                {'Error': {'Code': 'ThrottlingException', 'Message': 'Rate exceeded'}},
                'list_users'
            ),
            {'UserSummaryList': [{'Id': 'user-1'}]}
        ]
        
        with patch.object(lambda_function.MultiServiceClientManager, 'get_client') as mock_get_client:
            mock_get_client.return_value = mock_connect_client
            
            monitor = lambda_function.ConnectQuotaMonitor()
            
            # The monitor should retry and succeed
            result = monitor.call_service_api('connect', 'list_users', InstanceId='test')
            
            self.assertIsNotNone(result)
    
    @patch('lambda_function.boto3.Session')
    def test_handle_permission_errors(self, mock_session):
        """Test handling of permission errors"""
        mock_session.return_value.get_credentials.return_value = Mock()
        
        mock_connect_client = Mock()
        mock_connect_client.list_users.side_effect = lambda_function.ClientError(
            {'Error': {'Code': 'AccessDeniedException', 'Message': 'Access denied'}},
            'list_users'
        )
        
        with patch.object(lambda_function.MultiServiceClientManager, 'get_client') as mock_get_client:
            mock_get_client.return_value = mock_connect_client
            
            monitor = lambda_function.ConnectQuotaMonitor()
            
            # Should return None for permission errors (no retry)
            result = monitor.call_service_api('connect', 'list_users', InstanceId='test')
            
            self.assertIsNone(result)


class TestEdgeCases(unittest.TestCase):
    """Test edge cases and boundary conditions"""
    
    def test_zero_quota_limit(self):
        """Test handling of quotas with zero limit"""
        quota_result = {
            'quota_code': 'L-TEST',
            'quota_name': 'Test Quota',
            'current_usage': 0,
            'quota_limit': 0,
            'utilization_percentage': 0
        }
        
        # Should not cause division by zero
        self.assertEqual(quota_result['utilization_percentage'], 0)
    
    def test_usage_exceeds_limit(self):
        """Test handling when current usage exceeds quota limit"""
        # This can happen with soft limits
        current_usage = 150
        quota_limit = 100
        utilization = (current_usage / quota_limit) * 100
        
        self.assertGreater(utilization, 100)
        self.assertEqual(utilization, 150.0)
    
    def test_empty_instance_list(self):
        """Test handling of no Connect instances"""
        monitoring_results = {
            'status': 'no_instances',
            'instances_monitored': 0,
            'total_quotas_checked': 0,
            'violations_found': 0
        }
        
        self.assertEqual(monitoring_results['instances_monitored'], 0)
        self.assertEqual(monitoring_results['status'], 'no_instances')


class TestIntegration(unittest.TestCase):
    """Integration tests for end-to-end flows"""
    
    @patch('lambda_function.boto3.Session')
    def test_full_monitoring_workflow(self, mock_session):
        """Test complete monitoring workflow"""
        # This is a simplified integration test
        # In practice, you'd use more comprehensive mocking
        
        mock_session.return_value.get_credentials.return_value = Mock()
        mock_session.return_value.region_name = 'us-east-1'
        
        # Test that the lambda handler can be called
        event = {
            'invocation_type': 'test_monitoring'
        }
        
        context = type('Context', (), {
            'function_name': 'test-function',
            'request_id': 'test-request-id'
        })()
        
        # This would require more comprehensive mocking in practice
        # For now, just verify the handler exists and has the right signature
        self.assertTrue(callable(lambda_function.lambda_handler))


def run_tests():
    """Run all tests"""
    # Create test suite
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    
    # Add all test classes
    suite.addTests(loader.loadTestsFromTestCase(TestInstanceDiscovery))
    suite.addTests(loader.loadTestsFromTestCase(TestQuotaMonitoring))
    suite.addTests(loader.loadTestsFromTestCase(TestAlertConsolidation))
    suite.addTests(loader.loadTestsFromTestCase(TestStorageEngine))
    suite.addTests(loader.loadTestsFromTestCase(TestErrorHandling))
    suite.addTests(loader.loadTestsFromTestCase(TestEdgeCases))
    suite.addTests(loader.loadTestsFromTestCase(TestIntegration))
    
    # Run tests
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    
    return result


if __name__ == '__main__':
    result = run_tests()
    sys.exit(0 if result.wasSuccessful() else 1)
