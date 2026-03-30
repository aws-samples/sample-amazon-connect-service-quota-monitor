#!/usr/bin/env python3
"""
Integration tests for Amazon Connect Quota Monitor

These tests validate end-to-end functionality with real AWS resources.
USE WITH CAUTION - These tests will make actual API calls to AWS.
"""

import unittest
import json
import os
import sys
from datetime import datetime

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import lambda_function


class TestRealAWSIntegration(unittest.TestCase):
    """
    Integration tests with real AWS resources.
    
    REQUIREMENTS:
    - AWS credentials configured
    - At least one Connect instance in the account
    - Proper IAM permissions
    
    Set SKIP_INTEGRATION_TESTS=true to skip these tests
    """
    
    @classmethod
    def setUpClass(cls):
        """Set up test class"""
        cls.skip_tests = os.environ.get('SKIP_INTEGRATION_TESTS', 'true').lower() == 'true'
        if cls.skip_tests:
            print("\n⚠️  Skipping integration tests (set SKIP_INTEGRATION_TESTS=false to run)")
    
    def setUp(self):
        """Set up test fixtures"""
        if self.skip_tests:
            self.skipTest("Integration tests disabled")
    
    def test_real_instance_discovery(self):
        """Test discovery with real Connect instances"""
        try:
            monitor = lambda_function.ConnectQuotaMonitor()
            instances = monitor.get_connect_instances()
            
            print(f"\n✓ Discovered {len(instances)} Connect instance(s)")
            for instance in instances:
                print(f"  - {instance.get('InstanceAlias', 'Unknown')} ({instance.get('Id')})")
            
            # Basic validation
            self.assertIsInstance(instances, list)
            if instances:
                self.assertIn('Id', instances[0])
                self.assertIn('InstanceAlias', instances[0])
                
        except Exception as e:
            self.fail(f"Instance discovery failed: {str(e)}")
    
    def test_real_quota_monitoring(self):
        """Test quota monitoring with real data"""
        try:
            monitor = lambda_function.ConnectQuotaMonitor()
            instances = monitor.get_connect_instances()
            
            if not instances:
                self.skipTest("No Connect instances found")
            
            instance_id = instances[0]['Id']
            
            # Test monitoring a single quota
            quota_config = lambda_function.ENHANCED_CONNECT_QUOTA_METRICS['L-9A46857E']
            result = monitor.get_quota_utilization(instance_id, quota_config, 'L-9A46857E')
            
            if result:
                print(f"\n✓ Quota monitoring successful:")
                print(f"  Quota: {result['quota_name']}")
                print(f"  Usage: {result['current_usage']}/{result['quota_limit']}")
                print(f"  Utilization: {result['utilization_percentage']}%")
                
                self.assertIn('current_usage', result)
                self.assertIn('quota_limit', result)
                self.assertIn('utilization_percentage', result)
            else:
                print(f"\n⚠️  Could not monitor quota (may be expected for some quota types)")
                
        except Exception as e:
            self.fail(f"Quota monitoring failed: {str(e)}")
    
    def test_lambda_handler_execution(self):
        """Test complete Lambda handler with test mode"""
        try:
            event = {
                'test': True,
                'invocation_type': 'test_monitoring'
            }
            
            context = type('Context', (), {
                'function_name': 'test-function',
                'request_id': 'test-request-id',
                'invoked_function_arn': 'arn:aws:lambda:us-east-1:123456789012:function:test'
            })()
            
            # Execute handler
            result = lambda_function.lambda_handler(event, context)
            
            print(f"\n✓ Lambda handler executed successfully")
            print(f"  Status Code: {result['statusCode']}")
            
            self.assertEqual(result['statusCode'], 200)
            self.assertIn('body', result)
            
            # Parse response
            body = json.loads(result['body'])
            print(f"  Instances Monitored: {body.get('instances_monitored', 0)}")
            print(f"  Quotas Checked: {body.get('total_quotas_checked', 0)}")
            print(f"  Violations: {body.get('violations_found', 0)}")
            
        except Exception as e:
            self.fail(f"Lambda handler execution failed: {str(e)}")


class TestAPIThrottlingMonitor(unittest.TestCase):
    """Integration tests for API throttling monitor"""
    
    @classmethod
    def setUpClass(cls):
        """Set up test class"""
        cls.skip_tests = os.environ.get('SKIP_INTEGRATION_TESTS', 'true').lower() == 'true'
        if cls.skip_tests:
            print("\n⚠️  Skipping throttling monitor integration tests")
    
    def setUp(self):
        """Set up test fixtures"""
        if self.skip_tests:
            self.skipTest("Integration tests disabled")
    
    def test_throttling_monitor_execution(self):
        """Test API throttling monitor execution"""
        try:
            import api_throttling_monitor
            
            event = {
                'hours': 1,
                'skip_utilization': False,
                'skip_throttling': False
            }
            
            context = type('Context', (), {
                'function_name': 'test-throttling-monitor',
                'request_id': 'test-request-id'
            })()
            
            # Execute monitor
            result = api_throttling_monitor.main(event, context)
            
            print(f"\n✓ Throttling monitor executed successfully")
            print(f"  Status Code: {result['statusCode']}")
            
            self.assertEqual(result['statusCode'], 200)
            self.assertIn('body', result)
            
            # Parse response
            body = json.loads(result['body'])
            print(f"  Period: {body.get('period_hours', 0)} hour(s)")
            print(f"  Status: {body.get('status', 'unknown')}")
            
        except ImportError:
            self.skipTest("API throttling monitor module not available")
        except Exception as e:
            self.fail(f"Throttling monitor execution failed: {str(e)}")


def run_integration_tests():
    """Run integration tests"""
    # Check if integration tests should run
    if os.environ.get('SKIP_INTEGRATION_TESTS', 'true').lower() == 'true':
        print("\n" + "=" * 70)
        print("INTEGRATION TESTS SKIPPED")
        print("=" * 70)
        print("\nTo run integration tests with real AWS resources:")
        print("  export SKIP_INTEGRATION_TESTS=false")
        print("  python tests/test_integration.py")
        print("\n⚠️  WARNING: Integration tests will make actual AWS API calls")
        print("=" * 70)
        return True
    
    # Create test suite
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    
    # Add test classes
    suite.addTests(loader.loadTestsFromTestCase(TestRealAWSIntegration))
    suite.addTests(loader.loadTestsFromTestCase(TestAPIThrottlingMonitor))
    
    # Run tests
    print("\n" + "=" * 70)
    print("RUNNING INTEGRATION TESTS WITH REAL AWS RESOURCES")
    print("=" * 70)
    
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    
    print("\n" + "=" * 70)
    print("INTEGRATION TEST SUMMARY")
    print("=" * 70)
    print(f"Tests run: {result.testsRun}")
    print(f"Failures: {len(result.failures)}")
    print(f"Errors: {len(result.errors)}")
    print(f"Skipped: {len(result.skipped)}")
    print("=" * 70)
    
    return result.wasSuccessful()


if __name__ == '__main__':
    success = run_integration_tests()
    sys.exit(0 if success else 1)
