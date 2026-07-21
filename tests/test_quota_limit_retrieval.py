#!/usr/bin/env python3
"""
Comprehensive Quota Validation Script

This script validates all quota codes defined in ENHANCED_CONNECT_QUOTA_METRICS to ensure:
1. The quota codes (L-codes) are valid and exist in AWS Service Quotas
2. The monitoring methods work correctly
3. The APIs can be called successfully
4. Context requirements are correctly defined

Usage:
    python tests/test_quota_limit_retrieval.py [--instance-id INSTANCE_ID]
"""

import sys
import os
import json
from datetime import datetime
from collections import defaultdict

# Add parent directory to path to import lambda_function
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lambda_function import (
    ENHANCED_CONNECT_QUOTA_METRICS,
    ConnectQuotaMonitor,
    QUOTA_CATEGORIES
)

class QuotaValidator:
    """Validates quota configurations and their accessibility."""
    
    def __init__(self, instance_id=None):
        """Initialize the validator."""
        self.instance_id = instance_id
        self.monitor = None
        self.validation_results = {
            'valid': [],
            'invalid': [],
            'warnings': [],
            'errors': []
        }
        
    def initialize_monitor(self):
        """Initialize the Connect quota monitor."""
        try:
            print("Initializing Connect Quota Monitor...")
            self.monitor = ConnectQuotaMonitor()
            print("✅ Monitor initialized successfully\n")
            return True
        except Exception as e:
            print(f"❌ Failed to initialize monitor: {str(e)}")
            return False
    
    def get_test_instance(self):
        """Get an instance ID for testing context-aware quotas."""
        if self.instance_id:
            return self.instance_id
        
        try:
            print("Discovering Connect instances...")
            instances = self.monitor.get_connect_instances()
            
            if not instances:
                print("⚠️  No Connect instances found in this account/region")
                return None
            
            active_instances = [i for i in instances if i.get('IsActive', False)]
            if not active_instances:
                print("⚠️  No active Connect instances found")
                return None
            
            test_instance = active_instances[0]
            instance_id = test_instance['Id']
            instance_alias = test_instance.get('InstanceAlias', 'No Alias')
            
            print(f"✅ Using instance: {instance_alias} ({instance_id})\n")
            return instance_id
            
        except Exception as e:
            print(f"❌ Error discovering instances: {str(e)}")
            return None
    
    def validate_quota_code(self, quota_code, quota_config):
        """
        Validate a single quota code against Service Quotas API.
        
        Returns:
            dict: Validation result with status and details
        """
        result = {
            'quota_code': quota_code,
            'quota_name': quota_config.get('name', 'Unknown'),
            'category': quota_config.get('category', 'Unknown'),
            'scope': quota_config.get('scope', 'Unknown'),
            'method': quota_config.get('method', 'Unknown'),
            'status': 'unknown',
            'details': [],
            'errors': []
        }
        
        service = quota_config.get('service', 'connect')
        context_required = quota_config.get('context_required', False)
        
        # Skip API-only quotas (they don't have L-codes in Service Quotas)
        if quota_code.startswith('L-API-'):
            result['status'] = 'skipped'
            result['details'].append("API rate limit - not in Service Quotas API")
            return result
        
        # Skip custom quotas (email, workspaces, etc.)
        if quota_code in ['L-EMAIL-ADDR', 'L-EMAIL-CONCURRENT', 'L-CL-PCAJ', 
                          'L-CL-CAJ', 'L-CL-AIAJ', 'L-CL-PCSJ', 'L-CL-ACSJ']:
            result['status'] = 'skipped'
            result['details'].append("Custom quota definition - may not be in Service Quotas")
            return result
        
        try:
            # Build parameters for Service Quotas API call
            params = {
                'ServiceCode': service,
                'QuotaCode': quota_code
            }
            
            # Add context if required
            if context_required and self.instance_id:
                region = self.monitor.region
                account_id = self.monitor._get_account_id()
                context_id = f"arn:aws:connect:{region}:{account_id}:instance/{self.instance_id}"
                params['ContextId'] = context_id
                result['details'].append(f"Using context: {context_id}")
            
            # Try to get the quota from Service Quotas API
            response = self.monitor.call_service_api('service-quotas', 'get_service_quota', **params)
            
            if response and 'Quota' in response:
                quota_info = response['Quota']
                result['status'] = 'valid'
                result['details'].append(f"✅ Quota exists in Service Quotas API")
                result['details'].append(f"   Service: {service}")
                result['details'].append(f"   Default Value: {quota_info.get('Value', 'N/A')}")
                result['details'].append(f"   Adjustable: {quota_info.get('Adjustable', False)}")
                result['details'].append(f"   Global Quota: {quota_info.get('GlobalQuota', False)}")
                
                # Check if quota name matches
                api_name = quota_info.get('QuotaName', '')
                config_name = quota_config.get('name', '')
                if api_name and api_name != config_name:
                    result['details'].append(f"⚠️  Name mismatch: '{config_name}' vs API: '{api_name}'")
                
            else:
                result['status'] = 'error'
                result['errors'].append("No quota data returned from Service Quotas API")
                
        except Exception as e:
            error_msg = str(e)
            result['status'] = 'error'
            
            # Categorize the error
            if 'NoSuchResourceException' in error_msg:
                result['errors'].append(f"❌ Quota code not found in Service Quotas API")
                result['errors'].append(f"   This L-code may be invalid or not available in {service}")
            elif 'ResourceNotFoundException' in error_msg:
                result['errors'].append(f"❌ Resource not found")
                result['errors'].append(f"   Quota may not exist or context is invalid")
            elif 'AccessDenied' in error_msg:
                result['errors'].append(f"⚠️  Access denied - check IAM permissions")
            elif 'context' in error_msg.lower():
                result['errors'].append(f"❌ Context-related error")
                result['errors'].append(f"   May need instance context: {context_required}")
            else:
                result['errors'].append(f"❌ Error: {error_msg}")
        
        return result
    
    def validate_monitoring_method(self, quota_code, quota_config):
        """
        Test if the monitoring method works for this quota.
        
        Returns:
            dict: Validation result
        """
        result = {
            'quota_code': quota_code,
            'method': quota_config.get('method', 'Unknown'),
            'status': 'unknown',
            'details': [],
            'errors': []
        }
        
        method = quota_config.get('method')
        scope = quota_config.get('scope', 'INSTANCE')
        
        # Determine which instance ID to use
        test_instance_id = None
        if scope == 'INSTANCE':
            test_instance_id = self.instance_id
            if not test_instance_id:
                result['status'] = 'skipped'
                result['details'].append("Instance-level quota - no instance ID provided")
                return result
        
        try:
            # Try to get utilization using the configured method
            utilization = self.monitor.get_quota_utilization(
                test_instance_id,
                quota_config,
                quota_code
            )
            
            if utilization is not None:
                result['status'] = 'valid'
                result['details'].append(f"✅ Monitoring method works")
                result['details'].append(f"   Current Usage: {utilization.get('current_usage', 'N/A')}")
                result['details'].append(f"   Quota Limit: {utilization.get('quota_limit', 'N/A')}")
                result['details'].append(f"   Utilization: {utilization.get('utilization_percentage', 0):.1f}%")
            else:
                result['status'] = 'warning'
                result['details'].append("⚠️  Method returned None - may not have data yet")
                
        except Exception as e:
            result['status'] = 'error'
            result['errors'].append(f"❌ Monitoring method failed: {str(e)}")
        
        return result
    
    def run_validation(self, validate_methods=True, verbose=False):
        """
        Run comprehensive validation of all quotas.
        
        Args:
            validate_methods: Whether to test monitoring methods (slower)
            verbose: Show detailed output for each quota
        """
        print("=" * 80)
        print("CONNECT QUOTA VALIDATION REPORT")
        print("=" * 80)
        print(f"Timestamp: {datetime.utcnow().isoformat()}")
        print(f"Region: {self.monitor.region if self.monitor else 'Unknown'}")
        print(f"Total Quotas to Validate: {len(ENHANCED_CONNECT_QUOTA_METRICS)}")
        print("=" * 80)
        print()
        
        # Group quotas by category
        quotas_by_category = defaultdict(list)
        for quota_code, quota_config in ENHANCED_CONNECT_QUOTA_METRICS.items():
            category = quota_config.get('category', 'Unknown')
            quotas_by_category[category].append((quota_code, quota_config))
        
        # Validate each category
        for category in sorted(quotas_by_category.keys()):
            category_name = QUOTA_CATEGORIES.get(category, category)
            quotas = quotas_by_category[category]
            
            print(f"\n{'='*80}")
            print(f"CATEGORY: {category_name} ({len(quotas)} quotas)")
            print(f"{'='*80}\n")
            
            category_valid = 0
            category_errors = 0
            category_skipped = 0
            
            for quota_code, quota_config in quotas:
                # Validate quota code
                validation = self.validate_quota_code(quota_code, quota_config)
                
                # Print results
                status_icon = {
                    'valid': '✅',
                    'error': '❌',
                    'skipped': '⏭️ ',
                    'unknown': '❓'
                }.get(validation['status'], '❓')
                
                print(f"{status_icon} {quota_config.get('name', 'Unknown')}")
                print(f"   Code: {quota_code} | Scope: {quota_config.get('scope')} | Method: {quota_config.get('method')}")
                
                if verbose or validation['status'] == 'error':
                    for detail in validation['details']:
                        print(f"   {detail}")
                    for error in validation['errors']:
                        print(f"   {error}")
                
                # Update counters
                if validation['status'] == 'valid':
                    category_valid += 1
                    self.validation_results['valid'].append(validation)
                elif validation['status'] == 'error':
                    category_errors += 1
                    self.validation_results['invalid'].append(validation)
                elif validation['status'] == 'skipped':
                    category_skipped += 1
                
                # Test monitoring method if requested
                if validate_methods and validation['status'] == 'valid':
                    method_validation = self.validate_monitoring_method(quota_code, quota_config)
                    
                    if method_validation['status'] == 'error':
                        print(f"   ⚠️  Monitoring method issue:")
                        for error in method_validation['errors']:
                            print(f"      {error}")
                
                print()  # Blank line between quotas
            
            # Category summary
            print(f"Category Summary: ✅ {category_valid} valid | ❌ {category_errors} errors | ⏭️  {category_skipped} skipped")
        
        # Overall summary
        print("\n" + "=" * 80)
        print("OVERALL VALIDATION SUMMARY")
        print("=" * 80)
        print(f"✅ Valid Quotas: {len(self.validation_results['valid'])}")
        print(f"❌ Invalid Quotas: {len(self.validation_results['invalid'])}")
        print(f"⏭️  Skipped Quotas: {len(ENHANCED_CONNECT_QUOTA_METRICS) - len(self.validation_results['valid']) - len(self.validation_results['invalid'])}")
        
        if self.validation_results['invalid']:
            print("\n❌ QUOTAS WITH ERRORS:")
            for result in self.validation_results['invalid']:
                print(f"   • {result['quota_code']}: {result['quota_name']}")
                for error in result['errors']:
                    print(f"     {error}")
        
        print("\n" + "=" * 80)
        
        # Save detailed results to file
        report_file = 'reports/quota_validation_report.json'
        os.makedirs('reports', exist_ok=True)
        
        with open(report_file, 'w') as f:
            json.dump({
                'timestamp': datetime.utcnow().isoformat(),
                'region': self.monitor.region if self.monitor else 'Unknown',
                'total_quotas': len(ENHANCED_CONNECT_QUOTA_METRICS),
                'validation_results': self.validation_results
            }, f, indent=2, default=str)
        
        print(f"\n📄 Detailed report saved to: {report_file}")
        
        return len(self.validation_results['invalid']) == 0


def main():
    """Main function."""
    import argparse
    
    parser = argparse.ArgumentParser(description='Validate Connect quota configurations')
    parser.add_argument('--instance-id', help='Connect instance ID for testing context-aware quotas')
    parser.add_argument('--validate-methods', action='store_true', 
                       help='Test monitoring methods (slower but more comprehensive)')
    parser.add_argument('--verbose', '-v', action='store_true', 
                       help='Show detailed output for all quotas')
    
    args = parser.parse_args()
    
    # Create validator
    validator = QuotaValidator(instance_id=args.instance_id)
    
    # Initialize monitor
    if not validator.initialize_monitor():
        print("Failed to initialize monitor. Exiting.")
        sys.exit(1)
    
    # Get test instance if not provided
    if not args.instance_id:
        validator.instance_id = validator.get_test_instance()
    
    # Run validation
    success = validator.run_validation(
        validate_methods=args.validate_methods,
        verbose=args.verbose
    )
    
    # Exit with appropriate code
    sys.exit(0 if success else 1)


if __name__ == '__main__':
    main()
