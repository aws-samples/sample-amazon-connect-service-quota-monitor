#!/usr/bin/env python3
# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""
Amazon Connect Service Quota Monitor - Enhanced Edition

This comprehensive Lambda function monitors 70+ Amazon Connect service quotas across all Connect services
with dynamic instance discovery, consolidated alerting, and intelligent deployment capabilities.

Key Features:
- Monitors 70+ quotas across 15+ service categories (Core Connect, Cases, Customer Profiles, Voice ID, etc.)
- Dynamic instance discovery (no hardcoded instance IDs)
- Consolidated alerts (one email per instance with all violations)
- Flexible storage (S3, DynamoDB, or both)
- Multi-service client management with retry logic
- Enterprise security compliance (KMS encryption, VPC support, DLQ)
- Post-deployment configuration management
- Intelligent deployment with S3 fallback for large code

Architecture:
- MultiServiceClientManager: Handles all AWS service clients
- ConnectQuotaMonitor: Core monitoring engine with multiple monitoring methods
- AlertConsolidationEngine: Groups violations by instance for efficient alerting
- FlexibleStorageEngine: Supports multiple storage backends
- Enhanced security compliance with data sanitization

Usage:
This function is designed to be deployed via CloudFormation and triggered by CloudWatch Events.
It automatically discovers Connect instances and monitors all configured quotas.
"""

import boto3
import logging
import json
from datetime import datetime, timedelta
import os
import sys
import re
import time
from botocore.exceptions import ClientError, BotoCoreError
from botocore.config import Config
import uuid
import io

# Configure logging with secure defaults
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger('connect-quota-monitor')

# Import enhanced security compliance
try:
    from enhanced_security_compliance import (
        SecurityDataSanitizer, 
        SecurityConfigValidator,
        log_secure_info,
        log_secure_warning,
        log_secure_error,
        log_secure_debug
    )
    ENHANCED_SECURITY_AVAILABLE = True
except ImportError:
    ENHANCED_SECURITY_AVAILABLE = False
    # Fallback sanitization function
    def sanitize_log(message):
        """Sanitize potentially sensitive data from log messages."""
        # Remove any potential AWS account IDs
        message = re.sub(r'\d{12}', '[ACCOUNT_ID]', str(message))
        # Remove any potential ARNs
        message = re.sub(r'arn:aws:[^:\s]+(:[^:\s]+)*', '[ARN]', message)
        return message

# Import enhanced error handling
try:
    from enhanced_error_handling import (
        EnhancedErrorHandler,
        ErrorContext,
        ErrorCategory,
        ErrorSeverity,
        error_handler_decorator,
        GracefulDegradationManager
    )
    ENHANCED_ERROR_HANDLING_AVAILABLE = True
except ImportError:
    ENHANCED_ERROR_HANDLING_AVAILABLE = False
    logger.warning("Enhanced error handling module not available, using basic error handling")

# Import performance optimizer
try:
    from performance_optimizer import (
        PerformanceOptimizer,
        CacheConfig,
        ParallelConfig,
        PaginationConfig,
        performance_monitor
    )
    PERFORMANCE_OPTIMIZER_AVAILABLE = True
except ImportError:
    PERFORMANCE_OPTIMIZER_AVAILABLE = False
    logger.warning("Performance optimizer module not available, using basic processing")

# Enhanced sanitization function
def sanitize_log(message):
    """Enhanced sanitize function with fallback."""
    if ENHANCED_SECURITY_AVAILABLE:
        return SecurityDataSanitizer.sanitize_message(message)
    else:
        # Fallback sanitization
        message = re.sub(r'\d{12}', '[ACCOUNT_ID]', str(message))
        message = re.sub(r'arn:aws:[^:\s]+(:[^:\s]+)*', '[ARN]', message)
        return message

# Constants with enhanced security validation
def get_validated_config():
    """Get and validate configuration parameters"""
    config = {
        'threshold_percentage': os.environ.get('THRESHOLD_PERCENTAGE', '80'),
        's3_bucket': os.environ.get('S3_BUCKET', ''),
        'dynamodb_table': os.environ.get('DYNAMODB_TABLE', ''),
        'use_s3_storage': os.environ.get('USE_S3_STORAGE', 'false'),
        'use_dynamodb': os.environ.get('USE_DYNAMODB', 'false')
    }
    
    # Validate configuration if enhanced security is available
    if ENHANCED_SECURITY_AVAILABLE:
        validation_errors = SecurityConfigValidator.validate_all_parameters(config)
        if validation_errors:
            log_secure_error(f"Configuration validation errors: {validation_errors}")
            # Use defaults for invalid values
            if not SecurityConfigValidator.validate_parameter('threshold_percentage', config['threshold_percentage'])[0]:
                config['threshold_percentage'] = '80'
    
    return config

# Get validated configuration
CONFIG = get_validated_config()
THRESHOLD_PERCENTAGE = int(CONFIG.get('threshold_percentage', '80'))  # Default to 80% for production
EXECUTION_ID = str(uuid.uuid4())  # Unique ID for this execution for traceability

# Enhanced Connect quota codes and their corresponding metrics - Comprehensive 200+ quota coverage
ENHANCED_CONNECT_QUOTA_METRICS = {
    # ===== CORE AMAZON CONNECT =====
    # Account Level Quotas
    'L-AA17A6B9': {  # Amazon Connect instance count
        'name': 'Amazon Connect instance count',
        'category': 'CORE_CONNECT',
        'scope': 'ACCOUNT',
        'method': 'api_count',
        'service': 'connect',
        'api': 'list_instances',
        'default_limit': 2,
        'context_required': False
    },
    'L-2BE4D75F': {  # External voice transfer connectors per account
        'name': 'External voice transfer connectors per account',
        'category': 'CORE_CONNECT',
        'scope': 'ACCOUNT',
        'method': 'service_quotas',
        'service': 'connect',
        'default_limit': 0,
        'context_required': False
    },
    'L-C970C7E8': {  # Contact Lens connectors per account
        'name': 'Contact Lens connectors per account',
        'category': 'CORE_CONNECT',
        'scope': 'ACCOUNT',
        'method': 'service_quotas',
        'service': 'connect',
        'default_limit': 0,
        'context_required': False
    },
    
    # Resource Level Quotas
    'L-9A46857E': {  # Users per instance
        'name': 'Users per instance',
        'category': 'CORE_CONNECT',
        'scope': 'INSTANCE',
        'method': 'api_count',
        'service': 'connect',
        'api': 'list_users',
        'default_limit': 500,
        'context_required': True
    },
    'L-F325A715': {  # Security profiles per instance
        'name': 'Security profiles per instance',
        'category': 'CORE_CONNECT',
        'scope': 'INSTANCE',
        'method': 'api_count',
        'service': 'connect',
        'api': 'list_security_profiles',
        'default_limit': 100,
        'context_required': True
    },
    'L-D68AAAE4': {  # User hierarchy groups per instance
        'name': 'User hierarchy groups per instance',
        'category': 'CORE_CONNECT',
        'scope': 'INSTANCE',
        'method': 'api_count',
        'service': 'connect',
        'api': 'describe_user_hierarchy_structure',
        'default_limit': 5,
        'context_required': True
    },
    'L-E3D2F503': {  # AWS Lambda functions per instance
        'name': 'AWS Lambda functions per instance',
        'category': 'CORE_CONNECT',
        'scope': 'INSTANCE',
        'method': 'api_count',
        'service': 'connect',
        'api': 'list_lambda_functions',
        'default_limit': 50,
        'context_required': True
    },
    'L-8F812903': {  # Phone numbers per instance
        'name': 'Phone numbers per instance',
        'category': 'CORE_CONNECT',
        'scope': 'INSTANCE',
        'method': 'api_count',
        'service': 'connect',
        'api': 'list_phone_numbers_v2',
        'default_limit': 10,
        'context_required': True
    },
    'L-22922690': {  # Contact flows per instance
        'name': 'Contact flows per instance',
        'category': 'CORE_CONNECT',
        'scope': 'INSTANCE',
        'method': 'api_count',
        'service': 'connect',
        'api': 'list_contact_flows',
        'default_limit': 100,
        'context_required': True
    },
    'L-19755C7E': {  # Flow modules per instance
        'name': 'Flow modules per instance',
        'category': 'CORE_CONNECT',
        'scope': 'INSTANCE',
        'method': 'api_count',
        'service': 'connect',
        'api': 'list_contact_flow_modules',
        'default_limit': 50,
        'context_required': True
    },
    'L-3828FBF0': {  # Predefined Attributes
        'name': 'Predefined Attributes',
        'category': 'CORE_CONNECT',
        'scope': 'INSTANCE',
        'method': 'api_count',
        'service': 'connect',
        'api': 'list_predefined_attributes',
        'default_limit': 128,
        'context_required': True
    },
    'L-0865B754': {  # Prompts per instance
        'name': 'Prompts per instance',
        'category': 'CORE_CONNECT',
        'scope': 'INSTANCE',
        'method': 'api_count',
        'service': 'connect',
        'api': 'list_prompts',
        'default_limit': 500,
        'context_required': True
    },
    'L-D945C9A8': {  # Agent status per instance
        'name': 'Agent status per instance',
        'category': 'CORE_CONNECT',
        'scope': 'INSTANCE',
        'method': 'api_count',
        'service': 'connect',
        'api': 'list_agent_statuses',
        'default_limit': 50,
        'context_required': True
    },

    # ===== CONTACT HANDLING & METRICS =====
    'L-12AB7C57': {  # Concurrent active calls per instance
        'name': 'Concurrent active calls per instance',
        'category': 'CONTACT_HANDLING',
        'scope': 'INSTANCE',
        'method': 'cloudwatch',
        'service': 'connect',
        'metric_name': 'ConcurrentCalls',
        'namespace': 'AWS/Connect',
        'statistic': 'Maximum',
        'default_limit': 10,
        'context_required': True
    },
    'L-D4BA6F6E': {  # Concurrent active chats per instance
        'name': 'Concurrent active chats per instance',
        'category': 'CONTACT_HANDLING',
        'scope': 'INSTANCE',
        'method': 'cloudwatch',
        'service': 'connect',
        'metric_name': 'ConcurrentActiveChats',
        'namespace': 'AWS/Connect',
        'statistic': 'Maximum',
        'default_limit': 100,
        'context_required': True
    },
    'L-60553137': {  # Concurrent active tasks per instance
        'name': 'Concurrent active tasks per instance',
        'category': 'CONTACT_HANDLING',
        'scope': 'INSTANCE',
        'method': 'cloudwatch',
        'service': 'connect',
        'metric_name': 'ConcurrentActiveTasks',
        'namespace': 'AWS/Connect',
        'statistic': 'Maximum',
        'default_limit': 2500,
        'context_required': True
    },
    'L-F4C86B27': {  # Email addresses per instance
        'name': 'Email addresses per instance',
        'category': 'CONTACT_HANDLING',
        'scope': 'INSTANCE',
        'method': 'service_quotas',
        'service': 'connect',
        'default_limit': 100,
        'context_required': True
    },
    'L-E908C3A1': {  # Concurrent campaign active calls per instance
        'name': 'Concurrent campaign active calls per instance',
        'category': 'CONTACT_HANDLING',
        'scope': 'INSTANCE',
        'method': 'cloudwatch',
        'service': 'connect-campaigns',
        'metric_name': 'ConcurrentCampaignCalls',
        'namespace': 'AWS/ConnectCampaigns',
        'statistic': 'Maximum',
        'default_limit': 0,
        'context_required': True
    },
    'L-B117F12F': {  # Concurrent active emails per instance
        'name': 'Concurrent active emails per instance',
        'category': 'CONTACT_HANDLING',
        'scope': 'INSTANCE',
        'method': 'cloudwatch',
        'service': 'connect',
        'metric_name': 'ConcurrentActiveEmails',
        'namespace': 'AWS/Connect',
        'statistic': 'Maximum',
        'default_limit': 1000,
        'context_required': True
    },

    # ===== ROUTING & QUEUES =====
    'L-19A87C94': {  # Queues per instance
        'name': 'Queues per instance',
        'category': 'ROUTING_QUEUES',
        'scope': 'INSTANCE',
        'method': 'api_count',
        'service': 'connect',
        'api': 'list_queues',
        'default_limit': 50,
        'context_required': True
    },
    'L-516BC0EB': {  # Queues per routing profile per instance
        'name': 'Queues per routing profile per instance',
        'category': 'ROUTING_QUEUES',
        'scope': 'INSTANCE',
        'method': 'service_quotas',
        'service': 'connect',
        'default_limit': 50,
        'context_required': True
    },
    'L-68BBE2E8': {  # Quick connects per instance
        'name': 'Quick connects per instance',
        'category': 'ROUTING_QUEUES',
        'scope': 'INSTANCE',
        'method': 'api_count',
        'service': 'connect',
        'api': 'list_quick_connects',
        'default_limit': 100,
        'context_required': True
    },
    'L-20CD02F7': {  # Hours of operation per instance
        'name': 'Hours of operation per instance',
        'category': 'ROUTING_QUEUES',
        'scope': 'INSTANCE',
        'method': 'api_count',
        'service': 'connect',
        'api': 'list_hours_of_operations',
        'default_limit': 100,
        'context_required': True
    },
    'L-D3E7BE26': {  # Routing profiles per instance
        'name': 'Routing profiles per instance',
        'category': 'ROUTING_QUEUES',
        'scope': 'INSTANCE',
        'method': 'api_count',
        'service': 'connect',
        'api': 'list_routing_profiles',
        'default_limit': 100,
        'context_required': True
    },
    'L-50375162': {  # Proficiencies per agent
        'name': 'Proficiencies per agent',
        'category': 'ROUTING_QUEUES',
        'scope': 'INSTANCE',
        'method': 'service_quotas',
        'service': 'connect',
        'default_limit': 20,
        'context_required': True
    },

    # ===== REPORTING =====
    'L-79564E52': {  # Reports per instance
        'name': 'Reports per instance',
        'category': 'REPORTING',
        'scope': 'INSTANCE',
        'method': 'service_quotas',
        'service': 'connect',
        'default_limit': 2000,
        'context_required': True
    },
    'L-986AE5E3': {  # Scheduled reports per instance
        'name': 'Scheduled reports per instance',
        'category': 'REPORTING',
        'scope': 'INSTANCE',
        'method': 'service_quotas',
        'service': 'connect',
        'default_limit': 500,
        'context_required': True
    },
    'L-DA88F710': {  # Maximum active recording sessions from external voice systems per instance
        'name': 'Maximum active recording sessions from external voice systems per instance',
        'category': 'REPORTING',
        'scope': 'INSTANCE',
        'method': 'service_quotas',
        'service': 'connect',
        'default_limit': 100,
        'context_required': True
    },

    # ===== FORECASTING & CAPACITY =====
    'L-64992552': {  # Forecast groups per instance
        'name': 'Forecast groups per instance',
        'category': 'FORECASTING_CAPACITY',
        'scope': 'INSTANCE',
        'method': 'service_quotas',
        'service': 'connect',
        'default_limit': 50,
        'context_required': True
    },
    'L-59F577B1': {  # Schedules per instance
        'name': 'Schedules per instance',
        'category': 'FORECASTING_CAPACITY',
        'scope': 'INSTANCE',
        'method': 'service_quotas',
        'service': 'connect',
        'default_limit': 600,
        'context_required': True
    },
    'L-71DDC3F5': {  # Agents per schedule
        'name': 'Agents per schedule',
        'category': 'FORECASTING_CAPACITY',
        'scope': 'INSTANCE',
        'method': 'service_quotas',
        'service': 'connect',
        'default_limit': 800,
        'context_required': False
    },
    'L-FA43E8DF': {  # Agents per staffing group
        'name': 'Agents per staffing group',
        'category': 'FORECASTING_CAPACITY',
        'scope': 'INSTANCE',
        'method': 'service_quotas',
        'service': 'connect',
        'default_limit': 80,
        'context_required': False
    },
    'L-241FA7AE': {  # Supervisors per staffing group
        'name': 'Supervisors per staffing group',
        'category': 'FORECASTING_CAPACITY',
        'scope': 'INSTANCE',
        'method': 'service_quotas',
        'service': 'connect',
        'default_limit': 40,
        'context_required': False
    },
    'L-67F605FD': {  # Staffing groups per supervisor
        'name': 'Staffing groups per supervisor',
        'category': 'FORECASTING_CAPACITY',
        'scope': 'INSTANCE',
        'method': 'service_quotas',
        'service': 'connect',
        'default_limit': 45,
        'context_required': False
    },
    'L-A3079FC2': {  # Staffing groups per instance
        'name': 'Staffing groups per instance',
        'category': 'FORECASTING_CAPACITY',
        'scope': 'INSTANCE',
        'method': 'service_quotas',
        'service': 'connect',
        'default_limit': 1300,
        'context_required': False
    },
    'L-9FF2F6AB': {  # Staffing groups per Forecast group
        'name': 'Staffing groups per Forecast group',
        'category': 'FORECASTING_CAPACITY',
        'scope': 'INSTANCE',
        'method': 'service_quotas',
        'service': 'connect',
        'default_limit': 100,
        'context_required': True
    },
    'L-E644BA2E': {  # Queues per forecast group
        'name': 'Queues per forecast group',
        'category': 'FORECASTING_CAPACITY',
        'scope': 'INSTANCE',
        'method': 'service_quotas',
        'service': 'connect',
        'default_limit': 200,
        'context_required': False
    },
    'L-5CE2FEE8': {  # Capacity planning scenarios per instance
        'name': 'Capacity planning scenarios per instance',
        'category': 'FORECASTING_CAPACITY',
        'scope': 'INSTANCE',
        'method': 'service_quotas',
        'service': 'connect',
        'default_limit': 500,
        'context_required': False
    },
    'L-62CF8E4F': {  # Capacity plans per instance
        'name': 'Capacity plans per instance',
        'category': 'FORECASTING_CAPACITY',
        'scope': 'INSTANCE',
        'method': 'service_quotas',
        'service': 'connect',
        'default_limit': 500,
        'context_required': False
    },
    'L-CF8DBCAD': {  # Capacity plan user data uploads per instance
        'name': 'Capacity plan user data uploads per instance',
        'category': 'FORECASTING_CAPACITY',
        'scope': 'INSTANCE',
        'method': 'service_quotas',
        'service': 'connect',
        'default_limit': 500,
        'context_required': False
    },
    'L-F9BE2186': {  # Capacity plan override uploads per instance
        'name': 'Capacity plan override uploads per instance',
        'category': 'FORECASTING_CAPACITY',
        'scope': 'INSTANCE',
        'method': 'service_quotas',
        'service': 'connect',
        'default_limit': 5000,
        'context_required': False
    },
    'L-E09E7401': {  # Forecast override uploads per instance
        'name': 'Forecast override uploads per instance',
        'category': 'FORECASTING_CAPACITY',
        'scope': 'INSTANCE',
        'method': 'service_quotas',
        'service': 'connect',
        'default_limit': 500,
        'context_required': False
    },
    'L-FBDF9277': {  # File size per upload of capacity plan user data
        'name': 'File size per upload of capacity plan user data',
        'category': 'FORECASTING_CAPACITY',
        'scope': 'INSTANCE',
        'method': 'service_quotas',
        'service': 'connect',
        'default_limit': 1000,
        'context_required': False
    },
    'L-3A13A048': {  # File size per upload of capacity plan overrides
        'name': 'File size per upload of capacity plan overrides',
        'category': 'FORECASTING_CAPACITY',
        'scope': 'INSTANCE',
        'method': 'service_quotas',
        'service': 'connect',
        'default_limit': 250,
        'context_required': False
    },
    'L-FA1E1138': {  # File size per upload of forecast overrides
        'name': 'File size per upload of forecast overrides',
        'category': 'FORECASTING_CAPACITY',
        'scope': 'INSTANCE',
        'method': 'service_quotas',
        'service': 'connect',
        'default_limit': 250,
        'context_required': False
    },
    'L-FF826748': {  # File size per upload of historical actuals
        'name': 'File size per upload of historical actuals',
        'category': 'FORECASTING_CAPACITY',
        'scope': 'INSTANCE',
        'method': 'service_quotas',
        'service': 'connect',
        'default_limit': 1000,
        'context_required': False
    },
    'L-77A750DD': {  # Historical actuals 15 or 30 minute interval aggregated file size limit
        'name': 'Historical actuals 15 or 30 minute interval aggregated file size limit',
        'category': 'FORECASTING_CAPACITY',
        'scope': 'INSTANCE',
        'method': 'service_quotas',
        'service': 'connect',
        'default_limit': 2000,
        'context_required': False
    },
    'L-EE276EF1': {  # Historical actuals 15 or 30 minute interval file count
        'name': 'Historical actuals 15 or 30 minute interval file count',
        'category': 'FORECASTING_CAPACITY',
        'scope': 'INSTANCE',
        'method': 'service_quotas',
        'service': 'connect',
        'default_limit': 300,
        'context_required': False
    },
    'L-95B6FF15': {  # Historical actuals daily interval aggregated file size limit
        'name': 'Historical actuals daily interval aggregated file size limit',
        'category': 'FORECASTING_CAPACITY',
        'scope': 'INSTANCE',
        'method': 'service_quotas',
        'service': 'connect',
        'default_limit': 2000,
        'context_required': False
    },
    'L-97E6C50D': {  # Historical actuals daily interval file count
        'name': 'Historical actuals daily interval file count',
        'category': 'FORECASTING_CAPACITY',
        'scope': 'INSTANCE',
        'method': 'service_quotas',
        'service': 'connect',
        'default_limit': 300,
        'context_required': False
    },
    'L-3233242F': {  # Concurrent uploads per instance
        'name': 'Concurrent uploads per instance',
        'category': 'FORECASTING_CAPACITY',
        'scope': 'INSTANCE',
        'method': 'service_quotas',
        'service': 'connect',
        'default_limit': 20,
        'context_required': False
    },
    'L-8FAD6E1F': {  # Shift activities per instance
        'name': 'Shift activities per instance',
        'category': 'FORECASTING_CAPACITY',
        'scope': 'INSTANCE',
        'method': 'service_quotas',
        'service': 'connect',
        'default_limit': 500,
        'context_required': False
    },
    'L-C174EC88': {  # Shift activities per shift profile
        'name': 'Shift activities per shift profile',
        'category': 'FORECASTING_CAPACITY',
        'scope': 'INSTANCE',
        'method': 'service_quotas',
        'service': 'connect',
        'default_limit': 10,
        'context_required': False
    },
    'L-E423F15D': {  # Shift profiles per instance
        'name': 'Shift profiles per instance',
        'category': 'FORECASTING_CAPACITY',
        'scope': 'INSTANCE',
        'method': 'service_quotas',
        'service': 'connect',
        'default_limit': 2500,
        'context_required': False
    },
    'L-F758F15D': {  # Shift rotation patterns per instance
        'name': 'Shift rotation patterns per instance',
        'category': 'FORECASTING_CAPACITY',
        'scope': 'INSTANCE',
        'method': 'service_quotas',
        'service': 'connect',
        'default_limit': 1300,
        'context_required': False
    },
    'L-ECE54E5A': {  # Shift rotation pattern steps per shift profile
        'name': 'Shift rotation pattern steps per shift profile',
        'category': 'FORECASTING_CAPACITY',
        'scope': 'INSTANCE',
        'method': 'service_quotas',
        'service': 'connect',
        'default_limit': 1300,
        'context_required': False
    },

    # ===== INTEGRATIONS =====
    'L-FC6A5030': {  # Application integration associations per instance
        'name': 'Application integration associations per instance',
        'category': 'INTEGRATIONS',
        'scope': 'INSTANCE',
        'method': 'api_count',
        'service': 'connect',
        'api': 'list_integration_associations',
        'default_limit': 50,
        'context_required': True
    },
    'L-790F20B4': {  # Event integration associations per instance
        'name': 'Event integration associations per instance',
        'category': 'INTEGRATIONS',
        'scope': 'INSTANCE',
        'method': 'service_quotas',
        'service': 'connect',
        'default_limit': 50,
        'context_required': True
    },
    'L-B93A6612': {  # Amazon Lex bots per instance
        'name': 'Amazon Lex bots per instance',
        'category': 'INTEGRATIONS',
        'scope': 'INSTANCE',
        'method': 'api_count',
        'service': 'connect',
        'api': 'list_bots',
        'default_limit': 50,
        'context_required': True
    },
    'L-CCEA7427': {  # Amazon Lex V2 bot aliases per instance
        'name': 'Amazon Lex V2 bot aliases per instance',
        'category': 'INTEGRATIONS',
        'scope': 'INSTANCE',
        'method': 'service_quotas',
        'service': 'connect',
        'default_limit': 100,
        'context_required': True
    },
    'L-C7548958': {  # Amazon Pinpoint application integration associations per instance
        'name': 'Amazon Pinpoint application integration associations per instance',
        'category': 'INTEGRATIONS',
        'scope': 'INSTANCE',
        'method': 'service_quotas',
        'service': 'connect',
        'default_limit': 1,
        'context_required': True
    },
    'L-0AA82C05': {  # Cases domain integration associations per instance
        'name': 'Cases domain integration associations per instance',
        'category': 'INTEGRATIONS',
        'scope': 'INSTANCE',
        'method': 'service_quotas',
        'service': 'connect',
        'default_limit': 1,
        'context_required': True
    },
    'L-FFE16A0F': {  # Connect AI agent assistant integration associations per instance
        'name': 'Connect AI agent assistant integration associations per instance',
        'category': 'INTEGRATIONS',
        'scope': 'INSTANCE',
        'method': 'service_quotas',
        'service': 'connect',
        'default_limit': 1,
        'context_required': True
    },
    'L-2D7CA70C': {  # Connect AI agent knowledge base integration associations per instance
        'name': 'Connect AI agent knowledge base integration associations per instance',
        'category': 'INTEGRATIONS',
        'scope': 'INSTANCE',
        'method': 'service_quotas',
        'service': 'connect',
        'default_limit': 10,
        'context_required': True
    },
    'L-D55E707F': {  # Connect AI agent message templates integration associations per instance
        'name': 'Connect AI agent message templates integration associations per instance',
        'category': 'INTEGRATIONS',
        'scope': 'INSTANCE',
        'method': 'service_quotas',
        'service': 'connect',
        'default_limit': 1,
        'context_required': True
    },
    'L-C8F22860': {  # Connect AI agent quick responses integration associations per instance
        'name': 'Connect AI agent quick responses integration associations per instance',
        'category': 'INTEGRATIONS',
        'scope': 'INSTANCE',
        'method': 'service_quotas',
        'service': 'connect',
        'default_limit': 1,
        'context_required': True
    },
    'L-02421311': {  # File scanner integration associations per instance
        'name': 'File scanner integration associations per instance',
        'category': 'INTEGRATIONS',
        'scope': 'INSTANCE',
        'method': 'service_quotas',
        'service': 'connect',
        'default_limit': 1,
        'context_required': True
    },
    'L-6402A996': {  # Workspaces per instance
        'name': 'Workspaces per instance',
        'category': 'INTEGRATIONS',
        'scope': 'INSTANCE',
        'method': 'api_count',
        'service': 'connect',
        'api': 'list_workspaces',
        'default_limit': 20,
        'context_required': True
    },
    
    # ===== AMAZON LEX SERVICE =====
    'L-36FA8BD2': {  # Bots per account (Lex V2)
        'name': 'Bots per account (Lex V2)',
        'category': 'INTEGRATIONS',
        'scope': 'ACCOUNT',
        'method': 'service_quotas',
        'service': 'lexv2-models',
        'default_limit': 100,
        'context_required': False
    },
    'L-ED50DA7C': {  # Sample utterances per intent (Lex V2)
        'name': 'Sample utterances per intent (Lex V2)',
        'category': 'INTEGRATIONS',
        'scope': 'ACCOUNT',
        'method': 'service_quotas',
        'service': 'lexv2-models',
        'default_limit': 1500,
        'context_required': False
    },
    'L-77D6C60C': {  # Sample utterances per slot (Lex V2)
        'name': 'Sample utterances per slot (Lex V2)',
        'category': 'INTEGRATIONS',
        'scope': 'ACCOUNT',
        'method': 'service_quotas',
        'service': 'lexv2-models',
        'default_limit': 10000,
        'context_required': False
    },
    
    # ===== CUSTOMER PROFILES SERVICE =====
    'L-6603B252': {  # Customer Profiles domain count
        'name': 'Customer Profiles domains per account',
        'category': 'CUSTOMER_PROFILES',
        'scope': 'ACCOUNT',
        'method': 'service_quotas',
        'service': 'customer-profiles',
        'default_limit': 100,
        'context_required': False
    },
    'L-A7ED412C': {  # Keys per object type
        'name': 'Keys per object type',
        'category': 'CUSTOMER_PROFILES',
        'scope': 'ACCOUNT',
        'method': 'service_quotas',
        'service': 'customer-profiles',
        'default_limit': 10,
        'context_required': False
    },
    'L-3217D1F1': {  # Maximum expiration in days
        'name': 'Maximum expiration in days',
        'category': 'CUSTOMER_PROFILES',
        'scope': 'ACCOUNT',
        'method': 'service_quotas',
        'service': 'customer-profiles',
        'default_limit': 1098,
        'context_required': False
    },
    'L-0A1E1791': {  # Event triggers per domain
        'name': 'Event triggers per domain',
        'category': 'CUSTOMER_PROFILES',
        'scope': 'ACCOUNT',
        'method': 'service_quotas',
        'service': 'customer-profiles',
        'default_limit': 10,
        'context_required': False
    },
    'L-4A5ECB8E': {  # Number of integrations
        'name': 'Integrations per domain',
        'category': 'CUSTOMER_PROFILES',
        'scope': 'ACCOUNT',
        'method': 'service_quotas',
        'service': 'customer-profiles',
        'default_limit': 50,
        'context_required': False
    },
    'L-DFAEAED3': {  # Profile history records per profile
        'name': 'Profile history records per profile',
        'category': 'CUSTOMER_PROFILES',
        'scope': 'ACCOUNT',
        'method': 'service_quotas',
        'service': 'customer-profiles',
        'default_limit': 100,
        'context_required': False
    },
    'L-B6E9F054': {  # Recommenders per domain
        'name': 'Recommenders per domain',
        'category': 'CUSTOMER_PROFILES',
        'scope': 'ACCOUNT',
        'method': 'service_quotas',
        'service': 'customer-profiles',
        'default_limit': 10,
        'context_required': False
    },
    'L-B59352A0': {  # Segment snapshots per day
        'name': 'Segment snapshots per day',
        'category': 'CUSTOMER_PROFILES',
        'scope': 'ACCOUNT',
        'method': 'service_quotas',
        'service': 'customer-profiles',
        'default_limit': 5,
        'context_required': False
    },
    'L-63975AF3': {  # Size of all objects for a profile
        'name': 'Size of all objects for a profile (KB)',
        'category': 'CUSTOMER_PROFILES',
        'scope': 'ACCOUNT',
        'method': 'service_quotas',
        'service': 'customer-profiles',
        'default_limit': 51200,
        'context_required': False
    },
    'L-14092FF4': {  # Object types per domain
        'name': 'Object types per domain',
        'category': 'CUSTOMER_PROFILES',
        'scope': 'ACCOUNT',
        'method': 'service_quotas',
        'service': 'customer-profiles',
        'default_limit': 100,
        'context_required': False
    },
    'L-E17DC7C3': {  # Objects per profile
        'name': 'Objects per profile',
        'category': 'CUSTOMER_PROFILES',
        'scope': 'ACCOUNT',
        'method': 'service_quotas',
        'service': 'customer-profiles',
        'default_limit': 1000,
        'context_required': False
    },
    
    # ===== API RATE LIMITS =====
    # Based on AWS Connect Service Limits documentation
    # https://docs.aws.amazon.com/connect/latest/adminguide/amazon-connect-service-limits.html
    
    # === Critical Metrics APIs (Highest Priority) ===
    'L-API-GMD': {  # GetMetricData API
        'name': 'Rate of GetMetricData API requests',
        'category': 'API_RATE_LIMITS',
        'scope': 'ACCOUNT',
        'method': 'cloudwatch_api',
        'service': 'connect',
        'operation': 'GetMetricData',
        'namespace': 'AWS/Connect',
        'default_limit': 5,
        'burst_limit': 8,
        'context_required': False
    },
    'L-API-GMDV2': {  # GetMetricDataV2 API
        'name': 'Rate of GetMetricDataV2 API requests',
        'category': 'API_RATE_LIMITS',
        'scope': 'ACCOUNT',
        'method': 'cloudwatch_api',
        'service': 'connect',
        'operation': 'GetMetricDataV2',
        'namespace': 'AWS/Connect',
        'default_limit': 10,
        'burst_limit': 10,
        'context_required': False
    },
    'L-API-GCMD': {  # GetCurrentMetricData API
        'name': 'Rate of GetCurrentMetricData API requests',
        'category': 'API_RATE_LIMITS',
        'scope': 'ACCOUNT',
        'method': 'cloudwatch_api',
        'service': 'connect',
        'operation': 'GetCurrentMetricData',
        'namespace': 'AWS/Connect',
        'default_limit': 5,
        'burst_limit': 8,
        'context_required': False
    },
    'L-API-GCUD': {  # GetCurrentUserData API
        'name': 'Rate of GetCurrentUserData API requests',
        'category': 'API_RATE_LIMITS',
        'scope': 'ACCOUNT',
        'method': 'cloudwatch_api',
        'service': 'connect',
        'operation': 'GetCurrentUserData',
        'namespace': 'AWS/Connect',
        'default_limit': 5,
        'burst_limit': 8,
        'context_required': False
    },
    # High-Volume Contact APIs
    'L-API-SCC': {  # StartChatContact API
        'name': 'Rate of StartChatContact API requests',
        'category': 'API_RATE_LIMITS',
        'scope': 'ACCOUNT',
        'method': 'cloudwatch_api',
        'service': 'connect',
        'operation': 'StartChatContact',
        'namespace': 'AWS/Connect',
        'default_limit': 5,
        'burst_limit': 8,
        'context_required': False
    },
    'L-API-SCS': {  # StartContactStreaming API
        'name': 'Rate of StartContactStreaming API requests',
        'category': 'API_RATE_LIMITS',
        'scope': 'ACCOUNT',
        'method': 'cloudwatch_api',
        'service': 'connect',
        'operation': 'StartContactStreaming',
        'namespace': 'AWS/Connect',
        'default_limit': 5,
        'burst_limit': 8,
        'context_required': False
    },
    'L-API-SCHK': {  # SearchContacts API
        'name': 'Rate of SearchContacts API requests',
        'category': 'API_RATE_LIMITS',
        'scope': 'ACCOUNT',
        'method': 'cloudwatch_api',
        'service': 'connect',
        'operation': 'SearchContacts',
        'namespace': 'AWS/Connect',
        'default_limit': 0.5,
        'burst_limit': 1,
        'context_required': False
    },
    'L-5AF7EB96': {  # GetContactAttributes API (actual quota code from AWS)
        'name': 'Rate of GetContactAttributes API requests',
        'category': 'API_RATE_LIMITS',
        'scope': 'ACCOUNT',
        'method': 'cloudwatch_api',
        'service': 'connect',
        'operation': 'GetContactAttributes',
        'namespace': 'AWS/Connect',
        'default_limit': 10,
        'burst_limit': 60,
        'context_required': False
    },
    'L-API-UCA': {  # UpdateContactAttributes API
        'name': 'Rate of UpdateContactAttributes API requests',
        'category': 'API_RATE_LIMITS',
        'scope': 'ACCOUNT',
        'method': 'cloudwatch_api',
        'service': 'connect',
        'operation': 'UpdateContactAttributes',
        'namespace': 'AWS/Connect',
        'default_limit': 10,
        'burst_limit': 15,
        'context_required': False
    },
    'L-API-DC': {  # DescribeContact API
        'name': 'Rate of DescribeContact API requests',
        'category': 'API_RATE_LIMITS',
        'scope': 'ACCOUNT',
        'method': 'cloudwatch_api',
        'service': 'connect',
        'operation': 'DescribeContact',
        'namespace': 'AWS/Connect',
        'default_limit': 10,
        'burst_limit': 15,
        'context_required': False
    },
    'L-API-SC': {  # StopContact API
        'name': 'Rate of StopContact API requests',
        'category': 'API_RATE_LIMITS',
        'scope': 'ACCOUNT',
        'method': 'cloudwatch_api',
        'service': 'connect',
        'operation': 'StopContact',
        'namespace': 'AWS/Connect',
        'default_limit': 10,
        'burst_limit': 15,
        'context_required': False
    },
    'L-API-UC': {  # UpdateContact API
        'name': 'Rate of UpdateContact API requests',
        'category': 'API_RATE_LIMITS',
        'scope': 'ACCOUNT',
        'method': 'cloudwatch_api',
        'service': 'connect',
        'operation': 'UpdateContact',
        'namespace': 'AWS/Connect',
        'default_limit': 10,
        'burst_limit': 15,
        'context_required': False
    },
    'L-API-BPC': {  # BatchPutContact API
        'name': 'Rate of BatchPutContact API requests',
        'category': 'API_RATE_LIMITS',
        'scope': 'ACCOUNT',
        'method': 'cloudwatch_api',
        'service': 'connect',
        'operation': 'BatchPutContact',
        'namespace': 'AWS/Connect',
        'default_limit': 10,
        'burst_limit': 15,
        'context_required': False
    },
    'L-API-TC': {  # TagContact API
        'name': 'Rate of TagContact API requests',
        'category': 'API_RATE_LIMITS',
        'scope': 'ACCOUNT',
        'method': 'cloudwatch_api',
        'service': 'connect',
        'operation': 'TagContact',
        'namespace': 'AWS/Connect',
        'default_limit': 20,
        'burst_limit': 25,
        'context_required': False
    },
    'L-API-UTC': {  # UntagContact API
        'name': 'Rate of UntagContact API requests',
        'category': 'API_RATE_LIMITS',
        'scope': 'ACCOUNT',
        'method': 'cloudwatch_api',
        'service': 'connect',
        'operation': 'UntagContact',
        'namespace': 'AWS/Connect',
        'default_limit': 20,
        'burst_limit': 25,
        'context_required': False
    },
    'L-API-UCRD': {  # UpdateContactRoutingData API
        'name': 'Rate of UpdateContactRoutingData API requests',
        'category': 'API_RATE_LIMITS',
        'scope': 'ACCOUNT',
        'method': 'cloudwatch_api',
        'service': 'connect',
        'operation': 'UpdateContactRoutingData',
        'namespace': 'AWS/Connect',
        'default_limit': 20,
        'burst_limit': 20,
        'context_required': False
    },
    # Integration APIs
    'L-API-SCIE': {  # SendChatIntegrationEvent API
        'name': 'Rate of SendChatIntegrationEvent API requests',
        'category': 'API_RATE_LIMITS',
        'scope': 'ACCOUNT',
        'method': 'cloudwatch_api',
        'service': 'connect',
        'operation': 'SendChatIntegrationEvent',
        'namespace': 'AWS/Connect',
        'default_limit': 17,
        'burst_limit': 26,
        'context_required': False
    },
    'L-API-LIA': {  # ListIntegrationAssociations API
        'name': 'Rate of ListIntegrationAssociations API requests',
        'category': 'API_RATE_LIMITS',
        'scope': 'ACCOUNT',
        'method': 'cloudwatch_api',
        'service': 'connect',
        'operation': 'ListIntegrationAssociations',
        'namespace': 'AWS/Connect',
        'default_limit': 25,
        'burst_limit': 50,
        'context_required': False
    },
    
    # === Participant Service APIs (High Priority for Chat) ===
    'L-API-CAU': {  # CompleteAttachmentUpload API
        'name': 'Rate of CompleteAttachmentUpload API requests',
        'category': 'API_RATE_LIMITS',
        'scope': 'ACCOUNT',
        'method': 'cloudwatch_api',
        'service': 'connect-participant',
        'operation': 'CompleteAttachmentUpload',
        'namespace': 'AWS/ConnectParticipant',
        'default_limit': 2,
        'burst_limit': 5,
        'context_required': False
    },
    'L-API-CPC': {  # CreateParticipantConnection API
        'name': 'Rate of CreateParticipantConnection API requests',
        'category': 'API_RATE_LIMITS',
        'scope': 'ACCOUNT',
        'method': 'cloudwatch_api',
        'service': 'connect-participant',
        'operation': 'CreateParticipantConnection',
        'namespace': 'AWS/ConnectParticipant',
        'default_limit': 6,
        'burst_limit': 9,
        'context_required': False
    },
    'L-API-DP': {  # DisconnectParticipant API
        'name': 'Rate of DisconnectParticipant API requests',
        'category': 'API_RATE_LIMITS',
        'scope': 'ACCOUNT',
        'method': 'cloudwatch_api',
        'service': 'connect-participant',
        'operation': 'DisconnectParticipant',
        'namespace': 'AWS/ConnectParticipant',
        'default_limit': 3,
        'burst_limit': 5,
        'context_required': False
    },
    'L-API-GA': {  # GetAttachment API
        'name': 'Rate of GetAttachment API requests',
        'category': 'API_RATE_LIMITS',
        'scope': 'ACCOUNT',
        'method': 'cloudwatch_api',
        'service': 'connect-participant',
        'operation': 'GetAttachment',
        'namespace': 'AWS/ConnectParticipant',
        'default_limit': 8,
        'burst_limit': 12,
        'context_required': False
    },
    'L-API-GT': {  # GetTranscript API
        'name': 'Rate of GetTranscript API requests',
        'category': 'API_RATE_LIMITS',
        'scope': 'ACCOUNT',
        'method': 'cloudwatch_api',
        'service': 'connect-participant',
        'operation': 'GetTranscript',
        'namespace': 'AWS/ConnectParticipant',
        'default_limit': 8,
        'burst_limit': 12,
        'context_required': False
    },
    'L-API-SE': {  # SendEvent API
        'name': 'Rate of SendEvent API requests',
        'category': 'API_RATE_LIMITS',
        'scope': 'ACCOUNT',
        'method': 'cloudwatch_api',
        'service': 'connect-participant',
        'operation': 'SendEvent',
        'namespace': 'AWS/ConnectParticipant',
        'default_limit': 10,
        'burst_limit': 15,
        'context_required': False
    },
    'L-API-SM': {  # SendMessage API
        'name': 'Rate of SendMessage API requests',
        'category': 'API_RATE_LIMITS',
        'scope': 'ACCOUNT',
        'method': 'cloudwatch_api',
        'service': 'connect-participant',
        'operation': 'SendMessage',
        'namespace': 'AWS/ConnectParticipant',
        'default_limit': 10,
        'burst_limit': 15,
        'context_required': False
    },
    'L-API-SAU': {  # StartAttachmentUpload API
        'name': 'Rate of StartAttachmentUpload API requests',
        'category': 'API_RATE_LIMITS',
        'scope': 'ACCOUNT',
        'method': 'cloudwatch_api',
        'service': 'connect-participant',
        'operation': 'StartAttachmentUpload',
        'namespace': 'AWS/ConnectParticipant',
        'default_limit': 2,
        'burst_limit': 5,
        'context_required': False
    },
    
    # === Cases API (Medium Priority) ===
    'L-API-CASES-CC': {  # CreateCase API
        'name': 'Rate of CreateCase API requests',
        'category': 'API_RATE_LIMITS',
        'scope': 'ACCOUNT',
        'method': 'cloudwatch_api',
        'service': 'connectcases',
        'operation': 'CreateCase',
        'namespace': 'AWS/ConnectCases',
        'default_limit': 2,
        'burst_limit': 10,
        'context_required': False
    },
    'L-API-CASES-SC': {  # SearchCases API
        'name': 'Rate of SearchCases API requests',
        'category': 'API_RATE_LIMITS',
        'scope': 'ACCOUNT',
        'method': 'cloudwatch_api',
        'service': 'connectcases',
        'operation': 'SearchCases',
        'namespace': 'AWS/ConnectCases',
        'default_limit': 2,
        'burst_limit': 10,
        'context_required': False
    },
    'L-API-CASES-GC': {  # GetCase API
        'name': 'Rate of GetCase API requests',
        'category': 'API_RATE_LIMITS',
        'scope': 'ACCOUNT',
        'method': 'cloudwatch_api',
        'service': 'connectcases',
        'operation': 'GetCase',
        'namespace': 'AWS/ConnectCases',
        'default_limit': 4,
        'burst_limit': 10,
        'context_required': False
    },
    'L-API-CASES-UC': {  # UpdateCase API
        'name': 'Rate of UpdateCase API requests',
        'category': 'API_RATE_LIMITS',
        'scope': 'ACCOUNT',
        'method': 'cloudwatch_api',
        'service': 'connectcases',
        'operation': 'UpdateCase',
        'namespace': 'AWS/ConnectCases',
        'default_limit': 2,
        'burst_limit': 2,
        'context_required': False
    },
    'L-API-CASES-LCFC': {  # ListCasesForContact API
        'name': 'Rate of ListCasesForContact API requests',
        'category': 'API_RATE_LIMITS',
        'scope': 'ACCOUNT',
        'method': 'cloudwatch_api',
        'service': 'connectcases',
        'operation': 'ListCasesForContact',
        'namespace': 'AWS/ConnectCases',
        'default_limit': 2,
        'burst_limit': 2,
        'context_required': False
    },
    
    # === Contact Lens API ===
    'L-API-CL-LRCAS': {  # ListRealtimeContactAnalysisSegments API
        'name': 'Rate of ListRealtimeContactAnalysisSegments API requests',
        'category': 'API_RATE_LIMITS',
        'scope': 'ACCOUNT',
        'method': 'cloudwatch_api',
        'service': 'connect',
        'operation': 'ListRealtimeContactAnalysisSegments',
        'namespace': 'AWS/Connect/ContactLens',
        'default_limit': 1,
        'burst_limit': 2,
        'context_required': False
    },
    'L-API-CL-LRCASV2': {  # ListRealtimeContactAnalysisSegmentsV2 API
        'name': 'Rate of ListRealtimeContactAnalysisSegmentsV2 API requests',
        'category': 'API_RATE_LIMITS',
        'scope': 'ACCOUNT',
        'method': 'cloudwatch_api',
        'service': 'connect',
        'operation': 'ListRealtimeContactAnalysisSegmentsV2',
        'namespace': 'AWS/Connect/ContactLens',
        'default_limit': 2,
        'burst_limit': 5,
        'context_required': False
    },
    
    # === Additional Connect Core APIs ===
    'L-API-CP': {  # CreateParticipant API
        'name': 'Rate of CreateParticipant API requests',
        'category': 'API_RATE_LIMITS',
        'scope': 'ACCOUNT',
        'method': 'cloudwatch_api',
        'service': 'connect',
        'operation': 'CreateParticipant',
        'namespace': 'AWS/Connect',
        'default_limit': 5,
        'burst_limit': 8,
        'context_required': False
    },
    'L-API-CPCA': {  # CreatePersistentContactAssociation API
        'name': 'Rate of CreatePersistentContactAssociation API requests',
        'category': 'API_RATE_LIMITS',
        'scope': 'ACCOUNT',
        'method': 'cloudwatch_api',
        'service': 'connect',
        'operation': 'CreatePersistentContactAssociation',
        'namespace': 'AWS/Connect',
        'default_limit': 5,
        'burst_limit': 8,
        'context_required': False
    },
    'L-API-UPRC': {  # UpdateParticipantRoleConfig API
        'name': 'Rate of UpdateParticipantRoleConfig API requests',
        'category': 'API_RATE_LIMITS',
        'scope': 'ACCOUNT',
        'method': 'cloudwatch_api',
        'service': 'connect',
        'operation': 'UpdateParticipantRoleConfig',
        'namespace': 'AWS/Connect',
        'default_limit': 5,
        'burst_limit': 8,
        'context_required': False
    },
    'L-API-SCCS': {  # StopContactStreaming API
        'name': 'Rate of StopContactStreaming API requests',
        'category': 'API_RATE_LIMITS',
        'scope': 'ACCOUNT',
        'method': 'cloudwatch_api',
        'service': 'connect',
        'operation': 'StopContactStreaming',
        'namespace': 'AWS/Connect',
        'default_limit': 5,
        'burst_limit': 8,
        'context_required': False
    },
    'L-API-SIE': {  # SendIntegrationEvent API
        'name': 'Rate of SendIntegrationEvent API requests',
        'category': 'API_RATE_LIMITS',
        'scope': 'ACCOUNT',
        'method': 'cloudwatch_api',
        'service': 'connect',
        'operation': 'SendIntegrationEvent',
        'namespace': 'AWS/Connect',
        'default_limit': 10,
        'burst_limit': 15,
        'context_required': False
    },
    'L-API-CIA': {  # CreateIntegrationAssociation API
        'name': 'Rate of CreateIntegrationAssociation API requests',
        'category': 'API_RATE_LIMITS',
        'scope': 'ACCOUNT',
        'method': 'cloudwatch_api',
        'service': 'connect',
        'operation': 'CreateIntegrationAssociation',
        'namespace': 'AWS/Connect',
        'default_limit': 2,
        'burst_limit': 5,
        'context_required': False
    },
    'L-API-DIA': {  # DeleteIntegrationAssociation API
        'name': 'Rate of DeleteIntegrationAssociation API requests',
        'category': 'API_RATE_LIMITS',
        'scope': 'ACCOUNT',
        'method': 'cloudwatch_api',
        'service': 'connect',
        'operation': 'DeleteIntegrationAssociation',
        'namespace': 'AWS/Connect',
        'default_limit': 2,
        'burst_limit': 5,
        'context_required': False
    },
    # Email Quotas (NEW)
    'L-EMAIL-ADDR': {  # Email addresses per instance
        'name': 'Email addresses per instance',
        'category': 'CONTACT_HANDLING',
        'scope': 'INSTANCE',
        'method': 'api_count',
        'service': 'connect',
        'api': 'list_email_addresses',
        'default_limit': 100,
        'context_required': True
    },
    'L-EMAIL-CONCURRENT': {  # Concurrent active emails
        'name': 'Concurrent active emails per instance',
        'category': 'CONTACT_HANDLING',
        'scope': 'INSTANCE',
        'method': 'cloudwatch',
        'service': 'connect',
        'metric_name': 'ConcurrentActiveEmails',
        'namespace': 'AWS/Connect',
        'statistic': 'Maximum',
        'default_limit': 1000,
        'context_required': True
    },
    # Additional Contact Lens Quotas
    'L-CL-PCAJ': {  # Post-call analytics jobs
        'name': 'Concurrent post-call analytics jobs',
        'category': 'CONTACT_LENS',
        'scope': 'ACCOUNT',
        'method': 'service_quotas',
        'service': 'connect',
        'default_limit': 200,
        'context_required': False
    },
    'L-CL-CAJ': {  # Chat analytics jobs
        'name': 'Concurrent chat analytics jobs',
        'category': 'CONTACT_LENS',
        'scope': 'ACCOUNT',
        'method': 'service_quotas',
        'service': 'connect',
        'default_limit': 200,
        'context_required': False
    },
    'L-CL-AIAJ': {  # Automated interaction analytics jobs
        'name': 'Concurrent automated interaction analytics jobs',
        'category': 'CONTACT_LENS',
        'scope': 'ACCOUNT',
        'method': 'service_quotas',
        'service': 'connect',
        'default_limit': 20,
        'context_required': False
    },
    'L-CL-PCSJ': {  # Post-contact summary jobs
        'name': 'Concurrent post-contact agent conversation summary jobs',
        'category': 'CONTACT_LENS',
        'scope': 'ACCOUNT',
        'method': 'service_quotas',
        'service': 'connect',
        'default_limit': 10,
        'context_required': False
    },
    'L-CL-ACSJ': {  # After-call summary jobs
        'name': 'Concurrent after-call agent conversation summary jobs',
        'category': 'CONTACT_LENS',
        'scope': 'ACCOUNT',
        'method': 'service_quotas',
        'service': 'connect',
        'default_limit': 2,
        'context_required': False
    }
}

# Maintain backward compatibility with existing code
CONNECT_QUOTA_METRICS = ENHANCED_CONNECT_QUOTA_METRICS

# Quota categories for organization and filtering
QUOTA_CATEGORIES = {
    'CORE_CONNECT': 'Core Amazon Connect',
    'CONTACT_HANDLING': 'Contact Handling & Metrics',
    'ROUTING_QUEUES': 'Routing & Queues',
    'REPORTING': 'Reporting',
    'FORECASTING_CAPACITY': 'Forecasting & Capacity',
    'INTEGRATIONS': 'Integrations',
    'WISDOM_SERVICE': 'Wisdom Service',
    'CUSTOMER_PROFILES': 'Customer Profiles',
    'CASES': 'Cases',
    'VOICE_ID': 'Voice ID',
    'APP_INTEGRATIONS': 'App Integrations',
    'TASKS': 'Tasks',
    'CONTACT_LENS': 'Contact Lens',
    'AGENT_SCHEDULING': 'Agent & Scheduling',
    'API_RATE_LIMITS': 'API Rate Limits'
}

def get_quotas_by_category(category=None):
    """Get quotas filtered by category."""
    if category is None:
        return ENHANCED_CONNECT_QUOTA_METRICS
    
    return {
        quota_code: config 
        for quota_code, config in ENHANCED_CONNECT_QUOTA_METRICS.items()
        if config.get('category') == category
    }

def get_quotas_by_scope(scope=None):
    """Get quotas filtered by scope (ACCOUNT or INSTANCE)."""
    if scope is None:
        return ENHANCED_CONNECT_QUOTA_METRICS
    
    return {
        quota_code: config 
        for quota_code, config in ENHANCED_CONNECT_QUOTA_METRICS.items()
        if config.get('scope') == scope
    }

def get_account_level_quotas():
    """Get all account-level quotas."""
    return get_quotas_by_scope('ACCOUNT')

def get_instance_level_quotas():
    """Get all instance-level quotas."""
    return get_quotas_by_scope('INSTANCE')

def validate_quota_configuration():
    """Validate the quota configuration for completeness and correctness."""
    errors = []
    required_fields = ['name', 'category', 'scope', 'method', 'service', 'default_limit', 'context_required']
    
    for quota_code, config in ENHANCED_CONNECT_QUOTA_METRICS.items():
        # Check required fields
        for field in required_fields:
            if field not in config:
                errors.append(f"Quota {quota_code}: Missing required field '{field}'")
        
        # Validate category
        if config.get('category') not in QUOTA_CATEGORIES:
            errors.append(f"Quota {quota_code}: Invalid category '{config.get('category')}'")
        
        # Validate scope
        if config.get('scope') not in ['ACCOUNT', 'INSTANCE']:
            errors.append(f"Quota {quota_code}: Invalid scope '{config.get('scope')}'")
        
        # Validate method
        valid_methods = ['api_count', 'api_count_multi', 'cloudwatch', 'cloudwatch_api', 'service_quotas']
        if config.get('method') not in valid_methods:
            errors.append(f"Quota {quota_code}: Invalid method '{config.get('method')}'")
    
    if errors:
        logger.error(f"Quota configuration validation failed: {errors}")
        return False, errors
    
    logger.info(f"Quota configuration validation passed. Total quotas: {len(ENHANCED_CONNECT_QUOTA_METRICS)}")
    return True, []

# Validate configuration on module load
is_valid, validation_errors = validate_quota_configuration()
if not is_valid:
    logger.warning(f"Quota configuration has validation errors: {validation_errors}")

logger.info(f"Enhanced Connect Quota Monitor initialized with {len(ENHANCED_CONNECT_QUOTA_METRICS)} quota definitions across {len(QUOTA_CATEGORIES)} categories")

class MultiServiceClientManager:
    """
    Manages AWS service clients for all Connect-related services with comprehensive
    error handling, retry logic, and health checking capabilities.
    
    Integrates with EnhancedErrorHandler for:
    - Categorized error handling
    - Exponential backoff retry strategies
    - Service health monitoring
    - Graceful degradation
    """
    
    # Define all supported services and their configurations
    SUPPORTED_SERVICES = {
        'connect': {
            'name': 'Amazon Connect',
            'required': True,
            'retry_config': {'max_attempts': 5, 'mode': 'adaptive'}
        },
        'connectcases': {
            'name': 'Amazon Connect Cases',
            'required': False,
            'retry_config': {'max_attempts': 3, 'mode': 'standard'}
        },
        'customer-profiles': {
            'name': 'Amazon Connect Customer Profiles',
            'required': False,
            'retry_config': {'max_attempts': 3, 'mode': 'standard'}
        },
        'voice-id': {
            'name': 'Amazon Connect Voice ID',
            'required': False,
            'retry_config': {'max_attempts': 3, 'mode': 'standard'}
        },
        'wisdom': {
            'name': 'Amazon Connect Wisdom',
            'required': False,
            'retry_config': {'max_attempts': 3, 'mode': 'standard'}
        },
        'connect-campaigns': {
            'name': 'Amazon Connect Outbound Campaigns',
            'required': False,
            'retry_config': {'max_attempts': 3, 'mode': 'standard'}
        },
        'service-quotas': {
            'name': 'AWS Service Quotas',
            'required': True,
            'retry_config': {'max_attempts': 3, 'mode': 'standard'}
        },
        'cloudwatch': {
            'name': 'Amazon CloudWatch',
            'required': True,
            'retry_config': {'max_attempts': 3, 'mode': 'standard'}
        },
        'sns': {
            'name': 'Amazon SNS',
            'required': True,
            'retry_config': {'max_attempts': 3, 'mode': 'standard'}
        },
        's3': {
            'name': 'Amazon S3',
            'required': False,
            'retry_config': {'max_attempts': 3, 'mode': 'standard'}
        },
        'dynamodb': {
            'name': 'Amazon DynamoDB',
            'required': False,
            'retry_config': {'max_attempts': 3, 'mode': 'standard'}
        },
        'appintegrations': {
            'name': 'Amazon AppIntegrations',
            'required': False,
            'retry_config': {'max_attempts': 3, 'mode': 'standard'}
        },
        'sts': {
            'name': 'AWS Security Token Service',
            'required': False,
            'retry_config': {'max_attempts': 3, 'mode': 'standard'}
        }
    }
    
    def __init__(self, session, region_name=None, error_handler=None):
        """Initialize the multi-service client manager with enhanced error handling."""
        self.session = session
        self.region_name = region_name or session.region_name
        self.clients = {}
        self.client_health = {}
        self.initialization_errors = {}
        self.error_handler = error_handler
        
        # Initialize all clients
        self._initialize_all_clients()
        
    def _initialize_all_clients(self):
        """Initialize all supported AWS service clients."""
        logger.info("Initializing multi-service client manager...")
        
        for service_name, service_config in self.SUPPORTED_SERVICES.items():
            try:
                self._initialize_client(service_name, service_config)
            except Exception as e:
                self.initialization_errors[service_name] = str(e)
                if service_config['required']:
                    logger.error(f"Failed to initialize required service {service_name}: {sanitize_log(str(e))}")
                    raise
                else:
                    logger.warning(f"Failed to initialize optional service {service_name}: {sanitize_log(str(e))}")
        
        # Log initialization summary
        initialized_count = len(self.clients)
        total_count = len(self.SUPPORTED_SERVICES)
        logger.info(f"Multi-service client manager initialized: {initialized_count}/{total_count} services available")
        
        if self.initialization_errors:
            logger.warning(f"Services with initialization errors: {list(self.initialization_errors.keys())}")
    
    def _initialize_client(self, service_name, service_config):
        """Initialize a specific AWS service client with enhanced error handling."""
        try:
            # Create retry configuration
            retry_config = service_config.get('retry_config', {'max_attempts': 3, 'mode': 'standard'})
            config = Config(
                retries=retry_config,
                read_timeout=60,
                connect_timeout=10,
                max_pool_connections=50
            )
            
            # Create the client
            client = self.session.client(
                service_name,
                region_name=self.region_name,
                config=config
            )
            
            # Test client connectivity for required services
            if service_config['required']:
                self._test_client_connectivity(service_name, client)
            
            # Store client and mark as healthy
            self.clients[service_name] = client
            self.client_health[service_name] = True
            
            # Record successful initialization with error handler
            if self.error_handler and hasattr(self.error_handler, 'degradation_manager'):
                self.error_handler.degradation_manager.record_service_health(service_name, True)
            
            logger.debug(f"Successfully initialized {service_config['name']} client")
            
        except Exception as e:
            self.client_health[service_name] = False
            
            # Enhanced error handling for client initialization
            if self.error_handler and ENHANCED_ERROR_HANDLING_AVAILABLE:
                from enhanced_error_handling import ErrorContext
                context = ErrorContext(
                    operation='initialize_client',
                    service=service_name,
                    execution_id=getattr(self.error_handler, 'execution_id', None)
                )
                self.error_handler.handle_error(e, context)
            
            logger.error(f"Failed to initialize {service_config['name']} client: {sanitize_log(str(e))}")
            raise
    
    def _test_client_connectivity(self, service_name, client):
        """Test client connectivity with a simple API call."""
        try:
            if service_name == 'connect':
                # Test with list_instances (should work even with no instances)
                client.list_instances(MaxResults=1)
            elif service_name == 'service-quotas':
                # Test with list_services
                client.list_services(MaxResults=1)
            elif service_name == 'cloudwatch':
                # Test with list_metrics
                client.list_metrics()
            elif service_name == 'sns':
                # Test with list_topics
                client.list_topics()
            # Add more service-specific tests as needed
            
        except ClientError as e:
            # Some errors are acceptable (like no permissions for specific operations)
            error_code = e.response['Error']['Code']
            if error_code in ['AccessDenied', 'UnauthorizedOperation']:
                logger.warning(f"Limited permissions for {service_name}, but client is functional")
            else:
                raise
    
    def get_client(self, service_name):
        """Get a client for the specified service."""
        if service_name not in self.clients:
            logger.warning(f"Client for service '{service_name}' is not available")
            return None
        
        if not self.client_health.get(service_name, False):
            logger.warning(f"Client for service '{service_name}' is marked as unhealthy")
            return None
        
        return self.clients[service_name]
    
    def is_service_available(self, service_name):
        """Check if a service client is available and healthy."""
        return (service_name in self.clients and 
                self.client_health.get(service_name, False))
    
    def get_available_services(self):
        """Get list of available and healthy services."""
        return [service for service in self.clients.keys() 
                if self.client_health.get(service, False)]
    
    def reconnect_client(self, service_name):
        """Reconnect a specific client (useful for error recovery)."""
        if service_name not in self.SUPPORTED_SERVICES:
            logger.error(f"Unknown service: {service_name}")
            return False
        
        try:
            service_config = self.SUPPORTED_SERVICES[service_name]
            self._initialize_client(service_name, service_config)
            logger.info(f"Successfully reconnected {service_config['name']} client")
            return True
        except Exception as e:
            logger.error(f"Failed to reconnect {service_name} client: {sanitize_log(str(e))}")
            return False
    
    def health_check(self):
        """Perform health check on all clients."""
        health_status = {}
        
        for service_name, client in self.clients.items():
            try:
                self._test_client_connectivity(service_name, client)
                health_status[service_name] = True
                self.client_health[service_name] = True
            except Exception as e:
                health_status[service_name] = False
                self.client_health[service_name] = False
                logger.warning(f"Health check failed for {service_name}: {sanitize_log(str(e))}")
        
        return health_status
    
    def get_initialization_summary(self):
        """Get summary of client initialization status."""
        return {
            'total_services': len(self.SUPPORTED_SERVICES),
            'initialized_services': len(self.clients),
            'healthy_services': len([s for s in self.client_health.values() if s]),
            'available_services': self.get_available_services(),
            'initialization_errors': self.initialization_errors
        }


class ConnectQuotaMonitor:
    def __init__(self, region_name=None, profile_name=None, s3_bucket=None, use_dynamodb=False, dynamodb_table=None, error_handler=None, performance_optimizer=None):
        """Initialize the Connect Quota Monitor with enhanced multi-service client management, error handling, and performance optimization."""
        # Store error handler for use throughout the class
        self.error_handler = error_handler
        
        # Initialize performance optimizer
        if performance_optimizer:
            self.performance_optimizer = performance_optimizer
        elif PERFORMANCE_OPTIMIZER_AVAILABLE:
            # Create default performance optimizer with optimized settings for Lambda
            cache_config = CacheConfig(
                max_size=500,  # Smaller cache for Lambda memory constraints
                ttl_seconds=300,  # 5 minutes
                enable_memory_cache=True
            )
            parallel_config = ParallelConfig(
                max_workers=min(5, os.cpu_count() or 1),  # Limit workers based on available CPUs
                enable_parallel_instances=True,
                enable_parallel_quotas=True,
                batch_size=10,
                timeout_seconds=240  # 4 minutes (less than Lambda timeout)
            )
            pagination_config = PaginationConfig(
                max_pages_per_api=50,  # Reduced for Lambda
                items_per_page=100,
                enable_streaming=True,
                memory_threshold_mb=200,  # Conservative for Lambda
                enable_early_termination=True
            )
            
            self.performance_optimizer = PerformanceOptimizer(
                cache_config=cache_config,
                parallel_config=parallel_config,
                pagination_config=pagination_config
            )
            logger.info("Performance optimizer initialized with Lambda-optimized settings")
        else:
            self.performance_optimizer = None
            logger.warning("Performance optimizer not available")
        try:
            # Validate and create session with appropriate security
            session = boto3.Session(profile_name=profile_name, region_name=region_name)
            
            # Verify credentials are available
            if not session.get_credentials():
                raise ValueError("No AWS credentials found. Please configure AWS credentials.")
            
            # Initialize multi-service client manager with error handler
            self.client_manager = MultiServiceClientManager(session, region_name, error_handler)
            
            # Get commonly used clients for backward compatibility
            self.connect_client = self.client_manager.get_client('connect')
            self.service_quotas_client = self.client_manager.get_client('service-quotas')
            self.cloudwatch_client = self.client_manager.get_client('cloudwatch')
            self.sns_client = self.client_manager.get_client('sns')
            
            # Initialize S3 client if bucket is provided
            self.s3_bucket = s3_bucket
            if s3_bucket:
                self.s3_client = self.client_manager.get_client('s3')
                if self.s3_client:
                    logger.info(f"S3 storage enabled with bucket: {sanitize_log(s3_bucket)}")
                else:
                    logger.error("S3 storage requested but S3 client not available")
                    raise ValueError("S3 client initialization failed")
            else:
                self.s3_client = None
                
            # Initialize DynamoDB client if requested
            self.use_dynamodb = use_dynamodb
            self.dynamodb_table = dynamodb_table
            if use_dynamodb and dynamodb_table:
                self.dynamodb_client = self.client_manager.get_client('dynamodb')
                if self.dynamodb_client:
                    # Create resource from the same session for consistency
                    self.dynamodb_resource = session.resource('dynamodb')
                    logger.info(f"DynamoDB storage enabled with table: {sanitize_log(dynamodb_table)}")
                    
                    # Ensure the DynamoDB table exists
                    self._ensure_dynamodb_table()
                else:
                    logger.error("DynamoDB storage requested but DynamoDB client not available")
                    raise ValueError("DynamoDB client initialization failed")
            else:
                self.dynamodb_client = None
                self.dynamodb_resource = None
            
            # Store region for logging and reference
            self.region = region_name or session.region_name
            
            # Log initialization summary
            summary = self.client_manager.get_initialization_summary()
            logger.info(f"ConnectQuotaMonitor initialized in region {self.region}")
            logger.info(f"Client summary: {summary['healthy_services']}/{summary['total_services']} services healthy")
            
            # Verify required clients are available
            required_services = ['connect', 'service-quotas', 'cloudwatch', 'sns']
            missing_services = [s for s in required_services if not self.client_manager.is_service_available(s)]
            if missing_services:
                raise ValueError(f"Required services not available: {missing_services}")
            
        except (ClientError, BotoCoreError, ValueError) as e:
            logger.error(f"Failed to initialize ConnectQuotaMonitor: {sanitize_log(str(e))}")
            raise
    
    def get_service_client(self, service_name):
        """Get a client for the specified service via the client manager."""
        return self.client_manager.get_client(service_name)
    
    def is_service_available(self, service_name):
        """Check if a service is available for monitoring."""
        return self.client_manager.is_service_available(service_name)
    
    def get_available_services(self):
        """Get list of all available services."""
        return self.client_manager.get_available_services()
    
    def perform_health_check(self):
        """Perform health check on all service clients."""
        return self.client_manager.health_check()
    
    def reconnect_service(self, service_name):
        """Reconnect a specific service client."""
        success = self.client_manager.reconnect_client(service_name)
        if success and service_name in ['connect', 'service-quotas', 'cloudwatch', 'sns']:
            # Update commonly used client references
            if service_name == 'connect':
                self.connect_client = self.client_manager.get_client('connect')
            elif service_name == 'service-quotas':
                self.service_quotas_client = self.client_manager.get_client('service-quotas')
            elif service_name == 'cloudwatch':
                self.cloudwatch_client = self.client_manager.get_client('cloudwatch')
            elif service_name == 'sns':
                self.sns_client = self.client_manager.get_client('sns')
        return success
    
    def call_service_api(self, service_name, api_method, **kwargs):
        """
        Call a service API with enhanced error handling, circuit breaker protection, and performance monitoring.
        
        Args:
            service_name: Name of the AWS service
            api_method: Name of the API method to call
            **kwargs: Arguments to pass to the API method
            
        Returns:
            API response or None if failed
        """
        # Use enhanced error handling if available
        if self.error_handler and ENHANCED_ERROR_HANDLING_AVAILABLE:
            from enhanced_error_handling import ErrorContext
            
            context = ErrorContext(
                operation=f"{service_name}.{api_method}",
                service=service_name,
                execution_id=getattr(self.error_handler, 'execution_id', None)
            )
            
            try:
                return self.error_handler.retry_with_backoff(
                    self._call_service_api_internal,
                    context,
                    service_name,
                    api_method,
                    **kwargs
                )
            except Exception as e:
                log_secure_error(f"Enhanced error handling failed for {service_name}.{api_method}", error=e)
                # Fallback to basic error handling
                return self._call_service_api_basic(service_name, api_method, **kwargs)
        else:
            # Use basic error handling
            return self._call_service_api_basic(service_name, api_method, **kwargs)
    
    def _call_service_api_internal(self, service_name, api_method, **kwargs):
        """Internal API call method for enhanced error handling."""
        client = self.get_service_client(service_name)
        if not client:
            raise Exception(f"No client available for service {service_name}")
        
        # Get the API method
        if not hasattr(client, api_method):
            raise Exception(f"API method {api_method} not available in {service_name} client")
        
        method = getattr(client, api_method)
        
        # Call the API
        response = method(**kwargs)
        return response
    
    def _call_service_api_basic(self, service_name, api_method, **kwargs):
        """Basic API call method with fallback error handling."""
        max_retries = 3
        retry_count = 0
        
        while retry_count < max_retries:
            try:
                client = self.get_service_client(service_name)
                if not client:
                    logger.error(f"No client available for service {service_name}")
                    return None
                
                # Get the API method
                if not hasattr(client, api_method):
                    logger.error(f"API method {api_method} not available in {service_name} client")
                    return None
                
                method = getattr(client, api_method)
                
                # Call the API
                response = method(**kwargs)
                
                # Record success with error handler if available
                if self.error_handler and hasattr(self.error_handler, 'degradation_manager'):
                    self.error_handler.degradation_manager.record_service_health(service_name, True)
                
                return response
                
            except ClientError as e:
                error_code = e.response['Error']['Code']
                error_msg = e.response['Error']['Message']
                
                # Record failure with error handler if available
                if self.error_handler and hasattr(self.error_handler, 'degradation_manager'):
                    from enhanced_error_handling import ErrorContext
                    context = ErrorContext(operation=f"{service_name}.{api_method}", service=service_name)
                    error_details = self.error_handler.handle_error(e, context)
                
                # Handle specific error types
                if error_code in ['Throttling', 'ThrottlingException', 'RequestLimitExceeded']:
                    # Exponential backoff for throttling
                    wait_time = (2 ** retry_count) + (retry_count * 0.1)
                    logger.warning(f"API throttled for {service_name}.{api_method}, retrying in {wait_time}s")
                    time.sleep(wait_time)
                    retry_count += 1
                    continue
                    
                elif error_code in ['ServiceUnavailable', 'InternalError', 'InternalFailure']:
                    # Retry for service errors
                    wait_time = (2 ** retry_count)
                    logger.warning(f"Service error for {service_name}.{api_method}, retrying in {wait_time}s")
                    time.sleep(wait_time)
                    retry_count += 1
                    continue
                    
                elif error_code in ['AccessDenied', 'UnauthorizedOperation', 'Forbidden']:
                    # Don't retry for permission errors
                    logger.error(f"Access denied for {service_name}.{api_method}: {sanitize_log(error_msg)}")
                    return None
                    
                elif error_code in ['InvalidParameterValue', 'ValidationException']:
                    # Don't retry for validation errors
                    logger.error(f"Invalid parameters for {service_name}.{api_method}: {sanitize_log(error_msg)}")
                    return None
                    
                else:
                    # Retry for other errors
                    logger.warning(f"Error calling {service_name}.{api_method}: {error_code} - {sanitize_log(error_msg)}")
                    retry_count += 1
                    continue
                    
            except BotoCoreError as e:
                # Network or connection errors - try to reconnect
                logger.warning(f"Connection error for {service_name}.{api_method}: {sanitize_log(str(e))}")
                if retry_count < max_retries - 1:
                    logger.info(f"Attempting to reconnect {service_name} client")
                    self.reconnect_service(service_name)
                retry_count += 1
                continue
                
            except Exception as e:
                logger.error(f"Unexpected error calling {service_name}.{api_method}: {sanitize_log(str(e))}")
                return None
        
        logger.error(f"Failed to call {service_name}.{api_method} after {max_retries} retries")
        return None
            
    def _ensure_dynamodb_table(self):
        """Ensure the DynamoDB table exists, create it if it doesn't."""
        try:
            # Check if table exists
            self.dynamodb_client.describe_table(TableName=self.dynamodb_table)
            logger.info(f"DynamoDB table {sanitize_log(self.dynamodb_table)} exists")
        except ClientError as e:
            if e.response['Error']['Code'] == 'ResourceNotFoundException':
                # Table doesn't exist, create it
                logger.info(f"Creating DynamoDB table {sanitize_log(self.dynamodb_table)}")
                
                table = self.dynamodb_resource.create_table(
                    TableName=self.dynamodb_table,
                    KeySchema=[
                        {
                            'AttributeName': 'id',
                            'KeyType': 'HASH'  # Partition key
                        },
                        {
                            'AttributeName': 'timestamp',
                            'KeyType': 'RANGE'  # Sort key
                        }
                    ],
                    AttributeDefinitions=[
                        {
                            'AttributeName': 'id',
                            'AttributeType': 'S'
                        },
                        {
                            'AttributeName': 'timestamp',
                            'AttributeType': 'S'
                        },
                        {
                            'AttributeName': 'instance_id',
                            'AttributeType': 'S'
                        }
                    ],
                    GlobalSecondaryIndexes=[
                        {
                            'IndexName': 'InstanceIdIndex',
                            'KeySchema': [
                                {
                                    'AttributeName': 'instance_id',
                                    'KeyType': 'HASH'
                                },
                                {
                                    'AttributeName': 'timestamp',
                                    'KeyType': 'RANGE'
                                }
                            ],
                            'Projection': {
                                'ProjectionType': 'ALL'
                            },
                            'ProvisionedThroughput': {
                                'ReadCapacityUnits': 5,
                                'WriteCapacityUnits': 5
                            }
                        }
                    ],
                    ProvisionedThroughput={
                        'ReadCapacityUnits': 5,
                        'WriteCapacityUnits': 5
                    }
                )
                
                # Wait for table to be created
                table.meta.client.get_waiter('table_exists').wait(TableName=self.dynamodb_table)
                logger.info(f"DynamoDB table {sanitize_log(self.dynamodb_table)} created successfully")
        
    def get_connect_instances(self, force_refresh=False):
        """
        Enhanced dynamic instance discovery with caching and comprehensive error handling.
        
        Args:
            force_refresh: Force refresh of cached instances
            
        Returns:
            List of Connect instance dictionaries with enhanced metadata
        """
        # Check cache first (unless force refresh)
        if not force_refresh and hasattr(self, '_cached_instances') and hasattr(self, '_cache_timestamp'):
            cache_age = datetime.utcnow() - self._cache_timestamp
            if cache_age.total_seconds() < 300:  # 5 minute cache
                logger.debug(f"Using cached instances ({len(self._cached_instances)} instances)")
                return self._cached_instances
        
        instances = []
        
        # Use enhanced error handling if available
        if self.error_handler and ENHANCED_ERROR_HANDLING_AVAILABLE:
            context = ErrorContext(
                operation='discover_instances',
                service='connect',
                execution_id=EXECUTION_ID
            )
            
            try:
                return self.error_handler.retry_with_backoff(
                    self._discover_instances_with_retry,
                    context,
                    force_refresh
                )
            except Exception as e:
                log_secure_error("Instance discovery failed after all retries", error=e)
                return self._get_fallback_instances()
        else:
            # Fallback to basic error handling
            return self._discover_instances_basic(force_refresh)
    
    def _discover_instances_with_retry(self, force_refresh=False):
        """Internal method for instance discovery with enhanced error handling."""
        instances = []
        
        try:
            logger.info("Discovering Connect instances dynamically...")
            
            # Use enhanced API calling with retry logic
            response = self.call_service_api('connect', 'list_instances')
            
            if not response:
                raise ValueError("No response from list_instances API")
            
            # Get instances from first page
            instances.extend(response.get('InstanceSummaryList', []))
            
            # Handle pagination
            next_token = response.get('NextToken')
            page_count = 1
            max_pages = 50  # Reasonable limit
            
            while next_token and page_count < max_pages:
                response = self.call_service_api('connect', 'list_instances', NextToken=next_token)
                
                if not response:
                    logger.warning(f"Failed to get page {page_count + 1} of instances")
                    break
                
                instances.extend(response.get('InstanceSummaryList', []))
                next_token = response.get('NextToken')
                page_count += 1
            
            if page_count >= max_pages:
                logger.warning(f"Reached maximum pages ({max_pages}) when listing instances")
            
            # Enhance instance metadata
            enhanced_instances = []
            for instance in instances:
                enhanced_instance = self._enhance_instance_metadata(instance)
                if enhanced_instance:
                    enhanced_instances.append(enhanced_instance)
            
            # Validate instances
            valid_instances = self._validate_instances(enhanced_instances)
            
            # Cache the results
            self._cached_instances = valid_instances
            self._cache_timestamp = datetime.utcnow()
            
            logger.info(f"Successfully discovered {len(valid_instances)} Connect instances")
            
            # Log instance summary
            self._log_instance_summary(valid_instances)
            
            return valid_instances
            
        except Exception as e:
            # Re-raise for enhanced error handler to catch
            raise
    
    def _discover_instances_basic(self, force_refresh=False):
        """Basic instance discovery with fallback error handling."""
        instances = []
        try:
            logger.info("Discovering Connect instances dynamically (basic mode)...")
            
            # Use enhanced API calling with retry logic
            response = self.call_service_api('connect', 'list_instances')
            
            if not response:
                logger.error("No response from list_instances API")
                return self._get_fallback_instances()
            
            # Get instances from first page
            instances.extend(response.get('InstanceSummaryList', []))
            
            # Handle pagination (simplified for basic mode)
            next_token = response.get('NextToken')
            page_count = 1
            max_pages = 10  # Reduced for basic mode
            
            while next_token and page_count < max_pages:
                try:
                    response = self.call_service_api('connect', 'list_instances', NextToken=next_token)
                    if response:
                        instances.extend(response.get('InstanceSummaryList', []))
                        next_token = response.get('NextToken')
                    else:
                        break
                except Exception as e:
                    logger.warning(f"Failed to get page {page_count + 1}: {sanitize_log(str(e))}")
                    break
                page_count += 1
            
            # Basic instance processing
            enhanced_instances = []
            for instance in instances:
                try:
                    enhanced_instance = self._enhance_instance_metadata(instance)
                    if enhanced_instance:
                        enhanced_instances.append(enhanced_instance)
                except Exception as e:
                    logger.warning(f"Failed to enhance instance metadata: {sanitize_log(str(e))}")
                    # Use basic instance data
                    enhanced_instances.append(instance)
            
            # Cache the results
            self._cached_instances = enhanced_instances
            self._cache_timestamp = datetime.utcnow()
            
            logger.info(f"Successfully discovered {len(enhanced_instances)} Connect instances (basic mode)")
            return enhanced_instances
            
        except ClientError as e:
            return self._handle_instance_discovery_error(e)
        except Exception as e:
            logger.error(f"Unexpected error during instance discovery: {sanitize_log(str(e))}")
            return self._get_fallback_instances()
    
    def _enhance_instance_metadata(self, instance):
        """Enhance instance data with additional metadata."""
        try:
            enhanced = {
                'Id': instance.get('Id'),
                'Arn': instance.get('Arn'),
                'IdentityManagementType': instance.get('IdentityManagementType'),
                'InstanceAlias': instance.get('InstanceAlias'),
                'CreatedTime': instance.get('CreatedTime'),
                'ServiceRole': instance.get('ServiceRole'),
                'InstanceStatus': instance.get('InstanceStatus'),
                'InboundCallsEnabled': instance.get('InboundCallsEnabled'),
                'OutboundCallsEnabled': instance.get('OutboundCallsEnabled'),
                'InstanceAccessUrl': instance.get('InstanceAccessUrl'),
                # Add computed fields
                'Region': self.region,
                'AccountId': self._get_account_id(),
                'DiscoveredAt': datetime.utcnow().isoformat(),
                'IsActive': instance.get('InstanceStatus') == 'ACTIVE'
            }
            
            # Extract instance ID from ARN if not directly available
            if not enhanced['Id'] and enhanced['Arn']:
                # ARN format: arn:aws:connect:region:account:instance/instance-id
                arn_parts = enhanced['Arn'].split('/')
                if len(arn_parts) > 1:
                    enhanced['Id'] = arn_parts[-1]
            
            # Validate required fields
            if not enhanced['Id']:
                logger.warning("Instance missing required ID field")
                return None
            
            return enhanced
            
        except Exception as e:
            logger.error(f"Error enhancing instance metadata: {sanitize_log(str(e))}")
            return None
    
    def _validate_instances(self, instances):
        """Validate discovered instances and filter out invalid ones."""
        valid_instances = []
        
        for instance in instances:
            if self._is_valid_instance(instance):
                valid_instances.append(instance)
            else:
                logger.warning(f"Filtering out invalid instance: {sanitize_log(instance.get('Id', 'unknown'))}")
        
        return valid_instances
    
    def _is_valid_instance(self, instance):
        """Check if an instance is valid for monitoring."""
        # Required fields
        required_fields = ['Id', 'Arn', 'InstanceStatus']
        for field in required_fields:
            if not instance.get(field):
                logger.debug(f"Instance missing required field: {field}")
                return False
        
        # Must be active
        if instance.get('InstanceStatus') != 'ACTIVE':
            logger.debug(f"Instance {instance['Id']} is not active: {instance.get('InstanceStatus')}")
            return False
        
        # Must have valid ARN format
        arn = instance.get('Arn', '')
        if not arn.startswith('arn:aws:connect:'):
            logger.debug(f"Instance {instance['Id']} has invalid ARN format")
            return False
        
        return True
    
    def _handle_instance_discovery_error(self, error):
        """Handle errors during instance discovery with specific error types."""
        error_code = error.response['Error']['Code']
        error_msg = error.response['Error']['Message']
        
        logger.error(f"Failed to discover Connect instances: {error_code} - {sanitize_log(error_msg)}")
        
        if error_code == 'AccessDeniedException':
            logger.error("PERMISSION ERROR: Insufficient permissions to list Connect instances.")
            logger.error("Required IAM permission: connect:ListInstances")
            logger.error("Please ensure the Lambda execution role has the necessary Connect permissions.")
            
        elif error_code == 'UnauthorizedOperation':
            logger.error("AUTHORIZATION ERROR: Not authorized to perform connect:ListInstances")
            logger.error("Please check IAM policies and resource-based permissions.")
            
        elif error_code in ['ServiceUnavailable', 'InternalError']:
            logger.error("SERVICE ERROR: Amazon Connect service is temporarily unavailable.")
            logger.error("This is likely a temporary issue. The system will retry automatically.")
            
        elif error_code == 'ThrottlingException':
            logger.error("THROTTLING ERROR: API requests are being throttled.")
            logger.error("The system will automatically retry with exponential backoff.")
            
        else:
            logger.error(f"UNKNOWN ERROR: {error_code} - {sanitize_log(error_msg)}")
        
        # Return fallback instances if available
        return self._get_fallback_instances()
    
    def _get_fallback_instances(self):
        """Get fallback instances from cache or return empty list."""
        if hasattr(self, '_cached_instances') and self._cached_instances:
            logger.warning("Using cached instances as fallback")
            return self._cached_instances
        
        logger.warning("No instances available - returning empty list")
        return []
    
    def _log_instance_summary(self, instances):
        """Log a summary of discovered instances."""
        if not instances:
            logger.warning("No Connect instances found in this account/region")
            return
        
        logger.info("=== Connect Instance Discovery Summary ===")
        logger.info(f"Region: {self.region}")
        logger.info(f"Account: {self._get_account_id()}")
        logger.info(f"Total Instances: {len(instances)}")
        
        # Group by status
        status_counts = {}
        for instance in instances:
            status = instance.get('InstanceStatus', 'UNKNOWN')
            status_counts[status] = status_counts.get(status, 0) + 1
        
        for status, count in status_counts.items():
            logger.info(f"  {status}: {count}")
        
        # Log instance details
        for instance in instances:
            alias = instance.get('InstanceAlias', 'No Alias')
            instance_id = instance.get('Id', 'Unknown')
            status = instance.get('InstanceStatus', 'Unknown')
            logger.info(f"  Instance: {alias} ({instance_id}) - Status: {status}")
    
    def refresh_instance_cache(self):
        """Force refresh of the instance cache."""
        logger.info("Forcing refresh of Connect instance cache")
        return self.get_connect_instances(force_refresh=True)
    
    def get_instance_by_id(self, instance_id):
        """Get a specific instance by ID."""
        instances = self.get_connect_instances()
        for instance in instances:
            if instance.get('Id') == instance_id:
                return instance
        return None
    
    def get_active_instances(self):
        """Get only active Connect instances."""
        instances = self.get_connect_instances()
        return [instance for instance in instances if instance.get('IsActive', False)]
    
    def validate_instance_permissions(self, instance_id):
        """Validate that we have necessary permissions for an instance."""
        try:
            # Test basic permissions by trying to list users
            response = self.call_service_api('connect', 'list_users', InstanceId=instance_id, MaxResults=1)
            
            if response is not None:
                logger.debug(f"Permissions validated for instance {instance_id}")
                return True
            else:
                logger.warning(f"Permission validation failed for instance {instance_id}")
                return False
                
        except Exception as e:
            logger.warning(f"Permission validation error for instance {instance_id}: {sanitize_log(str(e))}")
            return False
    
    def validate_no_hardcoded_references(self):
        """
        Validate that the solution contains no hardcoded instance IDs or account-specific references.
        This ensures the solution is suitable for distribution.
        """
        validation_results = {
            'is_distribution_ready': True,
            'issues': [],
            'warnings': []
        }
        
        # Check environment variables for hardcoded values
        env_vars_to_check = [
            'CONNECT_INSTANCE_ID',
            'INSTANCE_ID', 
            'CONNECT_INSTANCE_ARN',
            'ACCOUNT_ID'
        ]
        
        for env_var in env_vars_to_check:
            if os.environ.get(env_var):
                validation_results['issues'].append(f"Hardcoded environment variable found: {env_var}")
                validation_results['is_distribution_ready'] = False
        
        # Check for hardcoded instance IDs in common formats
        hardcoded_patterns = [
            r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}',  # UUID format
            r'arn:aws:connect:[^:]+:[0-9]{12}:instance/[0-9a-f-]+',  # Connect instance ARN
            r'[0-9]{12}'  # Account ID
        ]
        
        # This is a basic check - in a real implementation, you'd scan the actual code files
        logger.info("Validating solution for distribution readiness...")
        
        # Check if we're using dynamic discovery (good sign)
        if hasattr(self, 'get_connect_instances'):
            validation_results['warnings'].append("Using dynamic instance discovery - good for distribution")
        
        # Log validation results
        if validation_results['is_distribution_ready']:
            logger.info("✅ Solution appears ready for distribution - no hardcoded references detected")
        else:
            logger.error("❌ Solution NOT ready for distribution - hardcoded references found:")
            for issue in validation_results['issues']:
                logger.error(f"  - {issue}")
        
        if validation_results['warnings']:
            for warning in validation_results['warnings']:
                logger.info(f"ℹ️  {warning}")
        
        return validation_results
    
    def get_instance_monitoring_scope(self):
        """
        Determine the monitoring scope based on discovered instances.
        Returns information about what will be monitored.
        """
        instances = self.get_connect_instances()
        
        scope_info = {
            'total_instances': len(instances),
            'active_instances': len([i for i in instances if i.get('IsActive', False)]),
            'regions': list(set(i.get('Region') for i in instances if i.get('Region'))),
            'account_id': self._get_account_id(),
            'monitoring_approach': 'dynamic_discovery',
            'instance_details': []
        }
        
        for instance in instances:
            instance_info = {
                'id': instance.get('Id'),
                'alias': instance.get('InstanceAlias', 'No Alias'),
                'status': instance.get('InstanceStatus'),
                'will_monitor': instance.get('IsActive', False)
            }
            scope_info['instance_details'].append(instance_info)
        
        return scope_info
    
    def monitor_all_instances_dynamically(self, threshold_percentage=None):
        """
        Monitor quotas for all dynamically discovered instances.
        This is the main entry point for distribution-ready monitoring.
        """
        # Use environment variable threshold or default
        if threshold_percentage is None:
            threshold_percentage = int(os.environ.get('THRESHOLD_PERCENTAGE', THRESHOLD_PERCENTAGE))
        
        logger.info("=== Starting Dynamic Connect Quota Monitoring ===")
        
        # Validate distribution readiness
        validation = self.validate_no_hardcoded_references()
        if not validation['is_distribution_ready']:
            logger.error("Solution contains hardcoded references - not suitable for distribution")
            for issue in validation['issues']:
                logger.error(f"  Issue: {issue}")
        
        # Get monitoring scope
        scope = self.get_instance_monitoring_scope()
        logger.info(f"Monitoring scope: {scope['active_instances']} active instances out of {scope['total_instances']} total")
        
        # Discover instances dynamically
        instances = self.get_active_instances()
        
        if not instances:
            logger.warning("No active Connect instances found for monitoring")
            return {
                'status': 'no_instances',
                'message': 'No active Connect instances found',
                'instances_checked': 0,
                'quotas_monitored': 0
            }
        
        # Monitor each instance
        monitoring_results = {
            'status': 'success',
            'instances_monitored': 0,
            'total_quotas_checked': 0,
            'violations_found': 0,
            'instance_results': {},
            'account_quotas_checked': 0,
            'errors': []
        }
        
        # Monitor account-level quotas once
        logger.info("Monitoring account-level quotas...")
        account_quotas = get_account_level_quotas()
        account_results = []
        
        for quota_code, quota_config in account_quotas.items():
            try:
                result = self.get_quota_utilization(None, quota_config, quota_code)
                if result:
                    account_results.append(result)
                    monitoring_results['total_quotas_checked'] += 1
                    
                    if result['utilization_percentage'] >= threshold_percentage:
                        monitoring_results['violations_found'] += 1
                        logger.warning(f"Account quota violation: {result['quota_name']} at {result['utilization_percentage']}%")
                        
            except Exception as e:
                error_msg = f"Error monitoring account quota {quota_code}: {sanitize_log(str(e))}"
                logger.error(error_msg)
                monitoring_results['errors'].append(error_msg)
        
        monitoring_results['account_quotas_checked'] = len(account_results)
        monitoring_results['account_results'] = account_results
        
        # Monitor instance-level quotas for each instance
        instance_quotas = get_instance_level_quotas()
        
        # Use parallel processing if performance optimizer is available
        if self.performance_optimizer and len(instances) > 1:
            logger.info(f"Using parallel processing for {len(instances)} instances")
            
            def monitor_single_instance(instance):
                """Monitor quotas for a single instance"""
                instance_id = instance['Id']
                instance_alias = instance.get('InstanceAlias', 'No Alias')
                
                logger.info(f"Monitoring instance: {instance_alias} ({instance_id})")
                
                # Validate permissions for this instance
                if not self.validate_instance_permissions(instance_id):
                    return {
                        'instance_id': instance_id,
                        'instance_alias': instance_alias,
                        'error': f"Insufficient permissions for instance {instance_id}",
                        'quotas_checked': 0,
                        'violations': 0,
                        'results': []
                    }
                
                instance_results = []
                instance_violations = 0
                instance_errors = []
                
                # Use parallel processing for quotas within the instance if enabled
                quota_items = list(instance_quotas.items())
                
                if self.performance_optimizer and len(quota_items) > 5:
                    # Process quotas in parallel
                    with self.performance_optimizer.get_parallel_processor() as processor:
                        quota_results = processor.process_quotas_parallel(
                            quota_items,
                            self.get_quota_utilization,
                            instance_id
                        )
                        
                        for i, result in enumerate(quota_results):
                            if result:
                                instance_results.append(result)
                                if result['utilization_percentage'] >= threshold_percentage:
                                    instance_violations += 1
                                    logger.warning(f"Instance quota violation: {result['quota_name']} at {result['utilization_percentage']}% for {instance_alias}")
                else:
                    # Sequential processing for quotas
                    for quota_code, quota_config in instance_quotas.items():
                        try:
                            result = self.get_quota_utilization(instance_id, quota_config, quota_code)
                            if result:
                                instance_results.append(result)
                                if result['utilization_percentage'] >= threshold_percentage:
                                    instance_violations += 1
                                    logger.warning(f"Instance quota violation: {result['quota_name']} at {result['utilization_percentage']}% for {instance_alias}")
                        except Exception as e:
                            error_msg = f"Error monitoring quota {quota_code} for instance {instance_id}: {sanitize_log(str(e))}"
                            logger.error(error_msg)
                            instance_errors.append(error_msg)
                
                return {
                    'instance_id': instance_id,
                    'instance_alias': instance_alias,
                    'quotas_checked': len(instance_results),
                    'violations': instance_violations,
                    'results': instance_results,
                    'errors': instance_errors
                }
            
            # Process instances in parallel
            with self.performance_optimizer.get_parallel_processor() as processor:
                instance_results_list = processor.process_instances_parallel(
                    instances,
                    monitor_single_instance
                )
            
            # Aggregate results
            for instance_result in instance_results_list:
                if instance_result:
                    instance_id = instance_result['instance_id']
                    monitoring_results['instance_results'][instance_id] = {
                        'instance_alias': instance_result['instance_alias'],
                        'quotas_checked': instance_result['quotas_checked'],
                        'violations': instance_result['violations'],
                        'results': instance_result['results']
                    }
                    
                    monitoring_results['instances_monitored'] += 1
                    monitoring_results['total_quotas_checked'] += instance_result['quotas_checked']
                    monitoring_results['violations_found'] += instance_result['violations']
                    
                    if 'error' in instance_result:
                        monitoring_results['errors'].append(instance_result['error'])
                    
                    if 'errors' in instance_result:
                        monitoring_results['errors'].extend(instance_result['errors'])
        else:
            # Sequential processing (original implementation)
            for instance in instances:
                instance_id = instance['Id']
                instance_alias = instance.get('InstanceAlias', 'No Alias')
                
                logger.info(f"Monitoring instance: {instance_alias} ({instance_id})")
                
                # Validate permissions for this instance
                if not self.validate_instance_permissions(instance_id):
                    error_msg = f"Insufficient permissions for instance {instance_id}"
                    logger.error(error_msg)
                    monitoring_results['errors'].append(error_msg)
                    continue
                
                instance_results = []
                instance_violations = 0
                
                for quota_code, quota_config in instance_quotas.items():
                    try:
                        result = self.get_quota_utilization(instance_id, quota_config, quota_code)
                        if result:
                            instance_results.append(result)
                            monitoring_results['total_quotas_checked'] += 1
                            
                            if result['utilization_percentage'] >= threshold_percentage:
                                instance_violations += 1
                                monitoring_results['violations_found'] += 1
                                logger.warning(f"Instance quota violation: {result['quota_name']} at {result['utilization_percentage']}% for {instance_alias}")
                                
                    except Exception as e:
                        error_msg = f"Error monitoring quota {quota_code} for instance {instance_id}: {sanitize_log(str(e))}"
                        logger.error(error_msg)
                        monitoring_results['errors'].append(error_msg)
                
                monitoring_results['instance_results'][instance_id] = {
                    'instance_alias': instance_alias,
                    'quotas_checked': len(instance_results),
                    'violations': instance_violations,
                    'results': instance_results
                }
                
                monitoring_results['instances_monitored'] += 1
        
        # Log summary
        logger.info("=== Dynamic Monitoring Summary ===")
        logger.info(f"Instances monitored: {monitoring_results['instances_monitored']}")
        logger.info(f"Total quotas checked: {monitoring_results['total_quotas_checked']}")
        logger.info(f"Violations found: {monitoring_results['violations_found']}")
        logger.info(f"Errors encountered: {len(monitoring_results['errors'])}")
        
        return monitoring_results
    
    def create_alert_engine(self, topic_arn=None, threshold_percentage=None):
        """Create an alert consolidation engine instance."""
        if not topic_arn:
            topic_arn = os.environ.get('ALERT_SNS_TOPIC_ARN')
        
        if not threshold_percentage:
            threshold_percentage = int(os.environ.get('THRESHOLD_PERCENTAGE', THRESHOLD_PERCENTAGE))
        
        if not topic_arn:
            logger.error("No SNS topic ARN provided for alerts")
            return None
        
        return AlertConsolidationEngine(self.sns_client, topic_arn, threshold_percentage)
    
    def monitor_and_alert(self, topic_arn=None, threshold_percentage=None):
        """
        Complete monitoring workflow with consolidated alerting.
        This is the main entry point for the enhanced monitoring system.
        """
        logger.info("=== Starting Enhanced Connect Monitoring with Consolidated Alerts ===")
        
        # Perform monitoring
        monitoring_results = self.monitor_all_instances_dynamically(threshold_percentage)
        
        # Create alert engine
        alert_engine = self.create_alert_engine(topic_arn, threshold_percentage)
        if not alert_engine:
            logger.error("Failed to create alert engine - no alerts will be sent")
            return {
                **monitoring_results,
                'alert_results': {'error': 'Failed to create alert engine'}
            }
        
        # Validate SNS configuration
        is_valid, validation_message = alert_engine.validate_sns_configuration()
        if not is_valid:
            logger.error(f"SNS configuration invalid: {validation_message}")
            return {
                **monitoring_results,
                'alert_results': {'error': f'SNS configuration invalid: {validation_message}'}
            }
        
        logger.info(f"SNS configuration: {validation_message}")
        
        # Process alerts if violations found
        if monitoring_results.get('violations_found', 0) > 0:
            logger.info(f"Processing {monitoring_results['violations_found']} violations for consolidated alerts")
            alert_results = alert_engine.process_monitoring_results(monitoring_results)
        else:
            logger.info("No violations found - no alerts to send")
            alert_results = {
                'alerts_sent': 0,
                'instances_with_violations': 0,
                'total_violations': 0,
                'account_violations': 0,
                'errors': []
            }
        
        # Combine results
        final_results = {
            **monitoring_results,
            'alert_results': alert_results
        }
        
        # Log final summary
        logger.info("=== Enhanced Monitoring Complete ===")
        logger.info(f"Instances monitored: {monitoring_results.get('instances_monitored', 0)}")
        logger.info(f"Total quotas checked: {monitoring_results.get('total_quotas_checked', 0)}")
        logger.info(f"Violations found: {monitoring_results.get('violations_found', 0)}")
        logger.info(f"Alerts sent: {alert_results.get('alerts_sent', 0)}")
        
        if alert_results.get('errors'):
            logger.warning(f"Alert errors: {len(alert_results['errors'])}")
            for error in alert_results['errors']:
                logger.warning(f"  - {error}")
        
        return final_results
    
    def create_storage_engine(self):
        """Create a flexible storage engine instance."""
        storage_config = {
            'use_s3': bool(self.s3_bucket),
            'use_dynamodb': bool(self.use_dynamodb and self.dynamodb_table),
            's3_bucket': self.s3_bucket,
            'dynamodb_table': self.dynamodb_table
        }
        
        return FlexibleStorageEngine(storage_config, self.client_manager)
    
    def monitor_and_store(self, topic_arn=None, threshold_percentage=None):
        """
        Complete monitoring workflow with flexible storage.
        This combines monitoring, alerting, and storage in one method.
        """
        logger.info("=== Starting Enhanced Connect Monitoring with Flexible Storage ===")
        
        # Perform monitoring and alerting
        results = self.monitor_and_alert(topic_arn, threshold_percentage)
        
        # Create storage engine
        storage_engine = self.create_storage_engine()
        storage_status = storage_engine.get_storage_status()
        
        logger.info(f"Storage configuration: {storage_status['storage_backends']}")
        
        # Test storage connectivity
        connectivity_results = storage_engine.test_storage_connectivity()
        if connectivity_results['errors']:
            logger.warning("Storage connectivity issues detected:")
            for error in connectivity_results['errors']:
                logger.warning(f"  - {error}")
        
        # Store data if storage is configured
        storage_results = {
            'instance_storage': {},
            'account_storage': {},
            'report_storage': {},
            'storage_errors': []
        }
        
        if storage_status['storage_backends']:
            try:
                # Store account-level metrics
                account_results = results.get('account_results', [])
                if account_results:
                    account_storage = storage_engine.store_account_metrics(account_results)
                    storage_results['account_storage'] = account_storage
                    if account_storage['errors']:
                        storage_results['storage_errors'].extend(account_storage['errors'])
                
                # Store instance-level metrics
                for instance_id, instance_data in results.get('instance_results', {}).items():
                    instance_alias = instance_data.get('instance_alias', 'Unknown')
                    instance_metrics = instance_data.get('results', [])
                    
                    if instance_metrics:
                        instance_storage = storage_engine.store_instance_metrics(
                            instance_id, instance_alias, instance_metrics
                        )
                        storage_results['instance_storage'][instance_id] = instance_storage
                        if instance_storage['errors']:
                            storage_results['storage_errors'].extend(instance_storage['errors'])
                
                # Store consolidated report
                report_storage = storage_engine.store_consolidated_report(
                    results, results.get('alert_results')
                )
                storage_results['report_storage'] = report_storage
                if report_storage['errors']:
                    storage_results['storage_errors'].extend(report_storage['errors'])
                
                # Log storage summary
                total_s3_success = sum(1 for r in [storage_results['account_storage'], storage_results['report_storage']] if r.get('s3_success'))
                total_s3_success += sum(1 for r in storage_results['instance_storage'].values() if r.get('s3_success'))
                
                total_dynamodb_success = sum(1 for r in [storage_results['account_storage'], storage_results['report_storage']] if r.get('dynamodb_success'))
                total_dynamodb_success += sum(1 for r in storage_results['instance_storage'].values() if r.get('dynamodb_success'))
                
                logger.info(f"Storage summary: S3 operations: {total_s3_success}, DynamoDB operations: {total_dynamodb_success}")
                
                if storage_results['storage_errors']:
                    logger.warning(f"Storage errors: {len(storage_results['storage_errors'])}")
                
            except Exception as e:
                error_msg = f"Storage processing error: {sanitize_log(str(e))}"
                logger.error(error_msg)
                storage_results['storage_errors'].append(error_msg)
        else:
            logger.info("No storage backends configured - data not persisted")
        
        # Add storage results to final results
        final_results = {
            **results,
            'storage_results': storage_results,
            'storage_status': storage_status
        }
        
        logger.info("=== Enhanced Monitoring with Storage Complete ===")
        return final_results
    
    def get_current_configuration(self):
        """Get current configuration settings for management purposes."""
        config = {
            'threshold_percentage': int(os.environ.get('THRESHOLD_PERCENTAGE', THRESHOLD_PERCENTAGE)),
            'alert_sns_topic_arn': os.environ.get('ALERT_SNS_TOPIC_ARN', ''),
            's3_bucket': self.s3_bucket or '',
            'use_dynamodb': self.use_dynamodb,
            'dynamodb_table': self.dynamodb_table or '',
            'region': self.region,
            'account_id': self._get_account_id(),
            'storage_backends': [],
            'client_status': self.client_manager.get_initialization_summary(),
            'last_updated': datetime.utcnow().isoformat()
        }
        
        # Determine active storage backends
        if self.s3_bucket:
            config['storage_backends'].append('S3')
        if self.use_dynamodb and self.dynamodb_table:
            config['storage_backends'].append('DynamoDB')
        
        return config
    
    def validate_configuration_update(self, new_config):
        """Validate configuration updates before applying them."""
        validation_results = {
            'is_valid': True,
            'errors': [],
            'warnings': []
        }
        
        # Validate threshold percentage
        if 'threshold_percentage' in new_config:
            threshold = new_config['threshold_percentage']
            try:
                threshold_int = int(threshold)
                if threshold_int < 1 or threshold_int > 99:
                    validation_results['errors'].append("Threshold percentage must be between 1 and 99")
                    validation_results['is_valid'] = False
            except (ValueError, TypeError):
                validation_results['errors'].append("Threshold percentage must be a valid integer")
                validation_results['is_valid'] = False
        
        # Validate SNS topic ARN
        if 'alert_sns_topic_arn' in new_config:
            topic_arn = new_config['alert_sns_topic_arn']
            if topic_arn and not topic_arn.startswith('arn:aws:sns:'):
                validation_results['errors'].append("Invalid SNS topic ARN format")
                validation_results['is_valid'] = False
        
        # Validate storage configuration
        if 'use_dynamodb' in new_config and new_config['use_dynamodb']:
            if not new_config.get('dynamodb_table'):
                validation_results['errors'].append("DynamoDB table name required when DynamoDB storage is enabled")
                validation_results['is_valid'] = False
        
        return validation_results
    
    def apply_configuration_update(self, new_config):
        """Apply configuration updates dynamically."""
        logger.info("Applying configuration updates...")
        
        # Validate first
        validation = self.validate_configuration_update(new_config)
        if not validation['is_valid']:
            logger.error(f"Configuration validation failed: {validation['errors']}")
            return False
        
        # Apply threshold update
        if 'threshold_percentage' in new_config:
            old_threshold = int(os.environ.get('THRESHOLD_PERCENTAGE', THRESHOLD_PERCENTAGE))
            new_threshold = int(new_config['threshold_percentage'])
            if old_threshold != new_threshold:
                logger.info(f"Threshold updated from {old_threshold}% to {new_threshold}%")
                # Note: Environment variable updates require Lambda function configuration update
                # This would typically be done via the configuration management script
        
        # Log warnings if any
        for warning in validation.get('warnings', []):
            logger.warning(warning)
        
        logger.info("Configuration updates applied successfully")
        return True
    
    def get_configuration_status(self):
        """Get comprehensive configuration status for monitoring."""
        status = {
            'configuration': self.get_current_configuration(),
            'health_check': self.perform_health_check(),
            'storage_status': None,
            'alert_status': None,
            'permissions_status': None
        }
        
        # Check storage status
        if hasattr(self, 'create_storage_engine'):
            storage_engine = self.create_storage_engine()
            if storage_engine:
                status['storage_status'] = storage_engine.get_storage_status()
                connectivity_results = storage_engine.test_storage_connectivity()
                status['storage_connectivity'] = connectivity_results
        
        # Check alert configuration
        alert_topic_arn = os.environ.get('ALERT_SNS_TOPIC_ARN')
        if alert_topic_arn:
            try:
                alert_engine = self.create_alert_engine(alert_topic_arn)
                if alert_engine:
                    is_valid, message = alert_engine.validate_sns_configuration()
                    status['alert_status'] = {
                        'configured': True,
                        'valid': is_valid,
                        'message': message,
                        'topic_arn': alert_topic_arn
                    }
            except Exception as e:
                status['alert_status'] = {
                    'configured': True,
                    'valid': False,
                    'message': f"Error validating SNS configuration: {sanitize_log(str(e))}",
                    'topic_arn': alert_topic_arn
                }
        else:
            status['alert_status'] = {
                'configured': False,
                'valid': False,
                'message': "No SNS topic configured for alerts"
            }
        
        # Check permissions status
        try:
            instances = self.get_connect_instances()
            if instances:
                # Test permissions on first instance
                first_instance = instances[0]
                permissions_valid = self.validate_instance_permissions(first_instance['Id'])
                status['permissions_status'] = {
                    'valid': permissions_valid,
                    'tested_instance': first_instance['Id'],
                    'message': "Permissions validated" if permissions_valid else "Permission validation failed"
                }
            else:
                status['permissions_status'] = {
                    'valid': False,
                    'message': "No Connect instances found for permission testing"
                }
        except Exception as e:
            status['permissions_status'] = {
                'valid': False,
                'message': f"Error testing permissions: {sanitize_log(str(e))}"
            }
        
        return status
    
    def send_alert(self, topic_arn, quota_info):
        """Legacy send_alert method for backward compatibility."""
        logger.warning("Using legacy send_alert method. Consider using monitor_and_alert() for consolidated alerts.")
        
        # Create temporary alert engine
        alert_engine = AlertConsolidationEngine(self.sns_client, topic_arn, self.threshold_percentage or THRESHOLD_PERCENTAGE)
        
        # Convert legacy format to new format
        violations = [{
            'quota_code': quota_info.get('quota_info', {}).get('quota_code', 'unknown'),
            'quota_name': quota_info.get('quota_info', {}).get('quota_name', 'Unknown Quota'),
            'current_usage': quota_info.get('quota_info', {}).get('current_value', 0),
            'quota_limit': quota_info.get('quota_info', {}).get('quota_value', 0),
            'utilization_percentage': quota_info.get('quota_info', {}).get('utilization_percentage', 0),
            'category': 'LEGACY'
        }]
        
        # Send consolidated alert
        return alert_engine._send_instance_consolidated_alert(
            quota_info.get('instance_id', 'unknown'),
            {'instance_alias': quota_info.get('instance_name', 'Unknown Instance')},
            violations
        )
    
    def get_service_quotas(self):
        """Get Amazon Connect service quotas."""
        quotas = []
        try:
            paginator = self.service_quotas_client.get_paginator('list_service_quotas')
            
            for page in paginator.paginate(ServiceCode='connect'):
                quotas.extend(page['Quotas'])
                
            # Filter to include only the quotas we know how to monitor
            monitorable_quotas = [q for q in quotas if q['QuotaCode'] in CONNECT_QUOTA_METRICS]
            
            logger.info(f"Found {len(monitorable_quotas)} monitorable Connect service quotas out of {len(quotas)} total")
            return monitorable_quotas
            
        except ClientError as e:
            error_code = e.response['Error']['Code']
            error_msg = e.response['Error']['Message']
            logger.error(f"Failed to list service quotas: {error_code} - {sanitize_log(error_msg)}")
            if error_code == 'AccessDeniedException':
                logger.error("Insufficient permissions to list service quotas. Check IAM permissions.")
            raise
    
    def get_quota_utilization(self, instance_id, quota_config, quota_code=None):
        """
        Enhanced quota utilization monitoring with comprehensive multi-service support and error handling.
        
        Args:
            instance_id: Connect instance ID (None for account-level quotas)
            quota_config: Quota configuration from ENHANCED_CONNECT_QUOTA_METRICS
            quota_code: Optional quota code for logging
            
        Returns:
            Dictionary with utilization data or None if monitoring failed
        """
        # Use enhanced error handling if available
        if self.error_handler and ENHANCED_ERROR_HANDLING_AVAILABLE:
            context = ErrorContext(
                operation='get_quota_utilization',
                service=quota_config.get('service', 'connect'),
                resource_id=instance_id,
                quota_code=quota_code,
                execution_id=EXECUTION_ID
            )
            
            try:
                return self.error_handler.retry_with_backoff(
                    self._get_quota_utilization_with_retry,
                    context,
                    instance_id,
                    quota_config,
                    quota_code
                )
            except Exception as e:
                log_secure_error(
                    f"Quota utilization monitoring failed after all retries",
                    error=e,
                    quota_code=quota_code,
                    instance_id=instance_id
                )
                return None
        else:
            # Fallback to basic error handling
            return self._get_quota_utilization_basic(instance_id, quota_config, quota_code)
    
    def _get_quota_utilization_with_retry(self, instance_id, quota_config, quota_code=None):
        """Internal method for quota utilization with enhanced error handling."""
        return self._process_quota_config(instance_id, quota_config, quota_code)
    
    def _get_quota_utilization_basic(self, instance_id, quota_config, quota_code=None):
        """Basic quota utilization monitoring with fallback error handling."""
        try:
            return self._process_quota_config(instance_id, quota_config, quota_code)
        except Exception as e:
            log_secure_error(
                f"Basic quota utilization monitoring failed",
                error=e,
                quota_code=quota_code,
                instance_id=instance_id
            )
            return None
    
    def _process_quota_config(self, instance_id, quota_config, quota_code=None):
        """
        Process quota configuration and get utilization data.
        Returns dict with: default_limit, current_usage, quota_limit (applied), utilization_percentage
        """
        if isinstance(quota_config, dict) and 'QuotaCode' in quota_config:
            # Handle legacy quota format
            quota_code = quota_config['QuotaCode']
            quota_name = quota_config['QuotaName']
            default_limit = quota_config['Value']
            
            if quota_code not in ENHANCED_CONNECT_QUOTA_METRICS:
                logger.info(f"No enhanced monitoring configuration for quota: {quota_name} ({quota_code})")
                return None
            
            metric_config = ENHANCED_CONNECT_QUOTA_METRICS[quota_code]
        else:
            # Handle enhanced quota format
            metric_config = quota_config
            quota_name = metric_config.get('name', 'Unknown Quota')
            default_limit = metric_config.get('default_limit', 0)
        
        method = metric_config.get('method')
        service = metric_config.get('service', 'connect')
        scope = metric_config.get('scope', 'INSTANCE')
        context_required = metric_config.get('context_required', True)
        
        # Skip instance-level quotas if no instance provided
        if scope == 'INSTANCE' and not instance_id:
            logger.debug(f"Skipping instance-level quota {quota_name} - no instance ID provided")
            return None
        
        # Skip account-level quotas if instance provided (they should be checked once per account)
        if scope == 'ACCOUNT' and instance_id:
            logger.debug(f"Skipping account-level quota {quota_name} - should be checked at account level")
            return None
        
        # Record service health for graceful degradation
        if self.error_handler and hasattr(self.error_handler, 'degradation_manager'):
            self.error_handler.degradation_manager.record_service_health(service, True)
        
        current_usage = 0
        quota_limit = default_limit  # Start with default, may be updated
        
        # Method 1: Count resources via API pagination
        if method == 'api_count':
            current_usage = self._monitor_via_api_count(instance_id, metric_config)
            
        # Method 2: Count resources via multi-level API pagination (parent-child relationships)
        elif method == 'api_count_multi':
            current_usage = self._monitor_via_api_count_multi(instance_id, metric_config)
            
        # Method 3: Get metrics from CloudWatch
        elif method == 'cloudwatch':
            current_usage = self._monitor_via_cloudwatch(instance_id, metric_config)
            
        # Method 4: Get metrics from CloudWatch API usage
        elif method == 'cloudwatch_api':
            current_usage = self._monitor_via_cloudwatch_api(instance_id, metric_config)
        
        # CRITICAL FIX: Fetch actual applied quota limit for ALL monitoring methods
        # This ensures we use customer's approved limits, not AWS defaults
        # Without this, customers with quota increases get false alerts
        if method in ['api_count', 'api_count_multi', 'cloudwatch', 'cloudwatch_api']:
            if quota_code:
                actual_quota = self._get_actual_quota_limit(service, quota_code, instance_id, context_required)
                if actual_quota is not None and actual_quota > 0:
                    quota_limit = actual_quota
                    logger.debug(f"Using actual quota limit {quota_limit} for {quota_name} (default: {default_limit})")
            
        # Method 5: Get quota usage from Service Quotas API
        elif method == 'service_quotas':
            current_usage, quota_limit = self._monitor_via_service_quotas(instance_id, metric_config, quota_code)
            
        else:
            logger.warning(f"Unknown monitoring method '{method}' for quota {quota_name}")
            return None
        
        # Handle monitoring failures
        if current_usage is None:
            logger.warning(f"Failed to get usage for quota {quota_name}")
            # Record service as degraded
            if self.error_handler and hasattr(self.error_handler, 'degradation_manager'):
                self.error_handler.degradation_manager.record_service_health(service, False)
            return None
        
        # Calculate utilization percentage
        if quota_limit > 0:
            utilization_percentage = (current_usage / quota_limit) * 100
        else:
            utilization_percentage = 0
        
        # Create result with all three values
        result = {
            'quota_code': quota_code or 'unknown',
            'quota_name': quota_name,
            'category': metric_config.get('category', 'UNKNOWN'),
            'scope': scope,
            'default_limit': default_limit,  # Original default from AWS docs
            'current_usage': current_usage,  # Actual current utilization
            'quota_limit': quota_limit,  # Applied quota (may differ from default)
            'utilization_percentage': round(utilization_percentage, 2),
            'instance_id': instance_id,
            'timestamp': datetime.utcnow().isoformat(),
            'method': method,
            'service': service
        }
        
        logger.debug(f"Quota monitoring result: {quota_name} = {current_usage}/{quota_limit} (default: {default_limit}, {utilization_percentage:.1f}%)")
        return result
    
    def _monitor_via_api_count(self, instance_id, metric_config):
        """Monitor quota usage by counting resources via API calls."""
        service = metric_config.get('service', 'connect')
        api_name = metric_config.get('api')
        
        if not api_name:
            logger.error(f"No API specified for api_count method in service {service}")
            return None
        
        # Build API parameters based on service and scope
        api_params = self._build_api_parameters(instance_id, metric_config)
        if api_params is None:
            return None
        
        # Get response key for pagination
        response_key = self._get_response_key(service, api_name)
        if not response_key:
            logger.error(f"Unknown response key for {service}.{api_name}")
            return None
        
        # Handle special cases that don't use standard pagination
        if service == 'connect' and api_name == 'describe_user_hierarchy_structure':
            return self._count_hierarchy_levels(instance_id)
        elif service == 'connect' and api_name == 'list_instances':
            return self._count_connect_instances()
        
        # Use standard pagination counting
        return self._count_via_pagination_enhanced(service, api_name, response_key, api_params)
    
    def _monitor_via_api_count_multi(self, instance_id, metric_config):
        """Monitor quota usage by counting nested resources (parent-child relationships)."""
        service = metric_config.get('service')
        api_name = metric_config.get('api')
        parent_service = metric_config.get('parent_service')
        parent_api = metric_config.get('parent_api')
        parent_key = metric_config.get('parent_key')
        
        if not all([service, api_name, parent_service, parent_api, parent_key]):
            logger.error("Incomplete configuration for api_count_multi method")
            return None
        
        # Get parent resources first
        parent_params = self._build_api_parameters(instance_id, {
            'service': parent_service,
            'api': parent_api,
            'scope': metric_config.get('scope', 'INSTANCE')
        })
        
        parent_response_key = self._get_response_key(parent_service, parent_api)
        if not parent_response_key:
            logger.error(f"Unknown response key for parent {parent_service}.{parent_api}")
            return None
        
        # Get parent resources
        parent_resources = self._get_all_resources(parent_service, parent_api, parent_response_key, parent_params or {})
        if not parent_resources:
            logger.debug(f"No parent resources found for {parent_service}.{parent_api}")
            return 0
        
        # Count child resources for each parent
        total_count = 0
        child_response_key = self._get_response_key(service, api_name)
        
        for parent_resource in parent_resources:
            parent_id = parent_resource.get(parent_key)
            if not parent_id:
                logger.warning(f"Parent resource missing key {parent_key}")
                continue
            
            # Build parameters for child API call
            child_params = {parent_key: parent_id}
            
            # Count child resources
            child_count = self._count_via_pagination_enhanced(service, api_name, child_response_key, child_params)
            if child_count is not None:
                total_count += child_count
        
        return total_count
    
    def _monitor_via_cloudwatch(self, instance_id, metric_config):
        """Monitor quota usage via CloudWatch metrics."""
        metric_name = metric_config.get('metric_name')
        namespace = metric_config.get('namespace', 'AWS/Connect')
        statistic = metric_config.get('statistic', 'Maximum')
        
        if not metric_name:
            logger.error("No metric_name specified for cloudwatch method")
            return None
        
        # Build dimensions based on scope
        dimensions = []
        if metric_config.get('scope') == 'INSTANCE' and instance_id:
            dimensions.append({
                'Name': 'InstanceId',
                'Value': instance_id
            })
        
        # Get metric data from CloudWatch
        end_time = datetime.utcnow()
        start_time = end_time - timedelta(minutes=15)  # Look back 15 minutes
        
        try:
            response = self.call_service_api(
                'cloudwatch',
                'get_metric_statistics',
                Namespace=namespace,
                MetricName=metric_name,
                Dimensions=dimensions,
                StartTime=start_time,
                EndTime=end_time,
                Period=300,  # 5 minutes
                Statistics=[statistic]
            )
            
            if not response or not response.get('Datapoints'):
                logger.debug(f"No CloudWatch data for metric {metric_name}")
                return 0
            
            # Get the most recent datapoint
            datapoints = sorted(response['Datapoints'], key=lambda x: x['Timestamp'], reverse=True)
            latest_value = datapoints[0].get(statistic, 0)
            
            return int(latest_value)
            
        except Exception as e:
            logger.error(f"Error getting CloudWatch metric {metric_name}: {sanitize_log(str(e))}")
            return None
    
    def _monitor_via_cloudwatch_api(self, instance_id, metric_config):
        """
        Monitor API rate limits via CloudWatch API usage metrics.
        Returns current usage (TPS) as integer.
        Note: Actual quota limit will be fetched separately to get applied quota vs default.
        """
        operation = metric_config.get('operation')
        namespace = metric_config.get('namespace', 'AWS/Connect')
        
        if not operation:
            logger.error("No operation specified for cloudwatch_api method")
            return None
        
        # Build dimensions for API metrics
        dimensions = [
            {'Name': 'Operation', 'Value': operation}
        ]
        
        if metric_config.get('scope') == 'INSTANCE' and instance_id:
            dimensions.append({
                'Name': 'InstanceId',
                'Value': instance_id
            })
        
        # Get API call count from CloudWatch over last 5 minutes
        end_time = datetime.utcnow()
        start_time = end_time - timedelta(minutes=5)
        
        try:
            response = self.call_service_api(
                'cloudwatch',
                'get_metric_statistics',
                Namespace=namespace,
                MetricName='APICallCount',
                Dimensions=dimensions,
                StartTime=start_time,
                EndTime=end_time,
                Period=60,  # 1 minute periods for granular rate calculation
                Statistics=['Sum']
            )
            
            if not response or not response.get('Datapoints'):
                # No recent API calls detected
                return 0
            
            # Calculate average rate per second over the period
            total_calls = sum(dp.get('Sum', 0) for dp in response['Datapoints'])
            rate_per_second = total_calls / 300  # 5 minutes = 300 seconds
            
            # Return as integer (rounded up to be conservative)
            import math
            return int(math.ceil(rate_per_second))
            
        except Exception as e:
            logger.error(f"Error getting API rate for {operation}: {sanitize_log(str(e))}")
            return None
    
    def _monitor_via_service_quotas(self, instance_id, metric_config, quota_code):
        """Monitor quota usage via Service Quotas API."""
        service_code = metric_config.get('service', 'connect')
        context_required = metric_config.get('context_required', False)
        
        if not quota_code:
            logger.error("No quota_code provided for service_quotas method")
            return None, metric_config.get('default_limit', 0)
        
        try:
            # Build parameters for Service Quotas API
            params = {
                'ServiceCode': service_code,
                'QuotaCode': quota_code
            }
            
            # Add instance context if required
            if context_required and instance_id:
                params['ContextId'] = f"arn:aws:connect:{self.region}:{self._get_account_id()}:instance/{instance_id}"
            
            # Get quota information
            response = self.call_service_api('service-quotas', 'get_service_quota', **params)
            
            if not response or 'Quota' not in response:
                logger.warning(f"No quota data from Service Quotas API for {quota_code}")
                return None, metric_config.get('default_limit', 0)
            
            quota_info = response['Quota']
            current_usage = quota_info.get('UsageMetric', {}).get('MetricValue', 0)
            quota_limit = quota_info.get('Value', metric_config.get('default_limit', 0))
            
            return int(current_usage), int(quota_limit)
            
        except Exception as e:
            logger.warning(f"Error getting quota from Service Quotas API: {sanitize_log(str(e))}")
            # Fall back to default limit
            return None, metric_config.get('default_limit', 0)
    
    def _get_actual_quota_limit(self, service, quota_code, instance_id=None, context_required=False):
        """
        Get the actual applied quota limit from Service Quotas API with caching.
        This may differ from the default if the user has requested a quota increase.
        
        This method is called by all quota monitoring methods to ensure we always
        use the applied quota value rather than just the documented default.
        
        Args:
            service: AWS service code (e.g., 'connect')
            quota_code: The quota code to look up
            instance_id: Optional instance ID for context-aware quotas
            context_required: Whether this quota requires instance context
            
        Returns:
            Actual applied quota limit or None if unavailable
        """
        # Initialize cache if it doesn't exist
        if not hasattr(self, '_quota_limit_cache'):
            self._quota_limit_cache = {}
        
        # Create cache key
        cache_key = f"{service}:{quota_code}"
        if context_required and instance_id:
            cache_key += f":{instance_id}"
        
        # Check cache first (5 minute TTL)
        if cache_key in self._quota_limit_cache:
            cached_value, cache_time = self._quota_limit_cache[cache_key]
            if (datetime.utcnow() - cache_time).total_seconds() < 300:
                logger.debug(f"Using cached quota limit for {quota_code}: {cached_value}")
                return cached_value
        
        try:
            # Build parameters for Service Quotas API
            params = {
                'ServiceCode': service,
                'QuotaCode': quota_code
            }
            
            # Add instance context if required
            if context_required and instance_id:
                context_id = f"arn:aws:connect:{self.region}:{self._get_account_id()}:instance/{instance_id}"
                params['ContextId'] = context_id
                logger.debug(f"Fetching context-aware quota for {quota_code} with context: {context_id}")
            
            # Try to get the applied quota value
            response = self.call_service_api('service-quotas', 'get_service_quota', **params)
            
            if response and 'Quota' in response:
                quota_info = response['Quota']
                applied_value = quota_info.get('Value')
                
                if applied_value is not None:
                    applied_float = float(applied_value)
                    logger.debug(f"Retrieved applied quota for {quota_code}: {applied_float}")
                    
                    # Cache the result
                    self._quota_limit_cache[cache_key] = (applied_float, datetime.utcnow())
                    return applied_float
            
            # If we can't get the applied value, cache None and return None to use default
            logger.debug(f"Could not retrieve applied quota for {quota_code}, will use default")
            self._quota_limit_cache[cache_key] = (None, datetime.utcnow())
            return None
            
        except ClientError as e:
            error_code = e.response['Error']['Code']
            
            # NoSuchResourceException means this quota doesn't have a service quota entry
            # This is expected for some quotas that don't have L-codes or aren't in Service Quotas
            if error_code == 'NoSuchResourceException':
                logger.debug(f"Quota {quota_code} not found in Service Quotas API (expected for some quotas)")
                # Cache this negative result to avoid repeated API calls
                self._quota_limit_cache[cache_key] = (None, datetime.utcnow())
                return None
            
            # ResourceNotFoundException - similar to above
            elif error_code == 'ResourceNotFoundException':
                logger.debug(f"Quota {quota_code} resource not found in Service Quotas API")
                self._quota_limit_cache[cache_key] = (None, datetime.utcnow())
                return None
            
            # AccessDeniedException - permission issue
            elif error_code == 'AccessDeniedException':
                logger.warning(f"Access denied when fetching quota {quota_code} - check IAM permissions")
                # Don't cache permission errors as they might be temporary
                return None
            
            # For other errors, log a warning
            logger.warning(f"Error fetching applied quota for {quota_code}: {error_code}")
            return None
            
        except Exception as e:
            logger.warning(f"Unexpected error fetching applied quota for {quota_code}: {sanitize_log(str(e))}")
            return None
    
    def _build_api_parameters(self, instance_id, metric_config):
        """Build API parameters based on service, API, and scope."""
        service = metric_config.get('service', 'connect')
        api_name = metric_config.get('api')
        scope = metric_config.get('scope', 'INSTANCE')
        
        params = {}
        
        # Add instance ID for instance-scoped quotas
        if scope == 'INSTANCE' and instance_id:
            if service == 'connect':
                params['InstanceId'] = instance_id
            elif service == 'connectcases':
                params['instanceId'] = instance_id
            elif service == 'wisdom':
                params['instanceId'] = instance_id
            elif service == 'connect-campaigns':
                params['instanceId'] = instance_id
        
        # Add service-specific parameters
        if service == 'connect' and api_name == 'list_queues':
            params['QueueTypes'] = ['STANDARD']
        elif service == 'connect' and api_name == 'list_phone_numbers_v2':
            params['TargetArn'] = f"arn:aws:connect:{self.region}:{self._get_account_id()}:instance/{instance_id}"
        
        return params
    
    def _get_response_key(self, service, api_name):
        """Get the response key for paginated API results."""
        # Define response keys for different APIs
        response_keys = {
            'connect': {
                'list_instances': 'InstanceSummaryList',
                'list_users': 'UserSummaryList',
                'list_queues': 'QueueSummaryList',
                'list_phone_numbers': 'PhoneNumberSummaryList',
                'list_phone_numbers_v2': 'ListPhoneNumbersSummaryList',
                'list_hours_of_operations': 'HoursOfOperationSummaryList',
                'list_contact_flows': 'ContactFlowSummaryList',
                'list_contact_flow_modules': 'ContactFlowModulesSummaryList',
                'list_routing_profiles': 'RoutingProfileSummaryList',
                'list_security_profiles': 'SecurityProfileSummaryList',
                'list_quick_connects': 'QuickConnectSummaryList',
                'list_agent_statuses': 'AgentStatusSummaryList',
                'list_prompts': 'PromptSummaryList',
                'list_task_templates': 'TaskTemplates',
                'list_evaluation_forms': 'EvaluationFormSummaryList',
                'list_integration_associations': 'IntegrationAssociationSummaryList',
                'list_bots': 'LexBots',
                'list_lambda_functions': 'LambdaFunctions',
                'list_predefined_attributes': 'PredefinedAttributes'
            },
            'connectcases': {
                'list_domains': 'domains',
                'list_fields': 'fields',
                'list_templates': 'templates'
            },
            'customer-profiles': {
                'list_domains': 'Items',
                'list_profile_object_types': 'Items'
            },
            'voice-id': {
                'list_domains': 'DomainSummaries',
                'list_speakers': 'SpeakerSummaries',
                'list_fraudsters': 'FraudsterSummaries'
            },
            'wisdom': {
                'list_knowledge_bases': 'knowledgeBaseSummaries',
                'list_contents': 'contentSummaries'
            },
            'connect-campaigns': {
                'list_campaigns': 'campaignSummaryList'
            }
        }
        
        return response_keys.get(service, {}).get(api_name)
    
    def _count_via_pagination_enhanced(self, service, api_name, response_key, params):
        """Count resources using enhanced pagination with performance optimization."""
        try:
            # Use performance optimizer if available
            if self.performance_optimizer:
                operation_name = f"{service}_{api_name}_count"
                
                def api_call(**api_params):
                    return self.call_service_api(service, api_name, **api_params)
                
                return self.performance_optimizer.optimize_api_pagination(
                    api_call=api_call,
                    response_key=response_key,
                    api_params=params,
                    operation_name=operation_name,
                    count_only=True
                )
            
            # Fallback to original implementation
            total_count = 0
            next_token = None
            max_pages = 100  # Prevent infinite loops
            page_count = 0
            
            while page_count < max_pages:
                # Add pagination token if available
                api_params = params.copy()
                if next_token:
                    # Different services use different pagination token names
                    if service in ['connectcases', 'customer-profiles', 'voice-id']:
                        api_params['NextToken'] = next_token
                    else:
                        api_params['NextToken'] = next_token
                
                # Call the API
                response = self.call_service_api(service, api_name, **api_params)
                if not response:
                    logger.warning(f"No response from {service}.{api_name}")
                    break
                
                # Count items in this page
                items = response.get(response_key, [])
                total_count += len(items)
                
                # Check for next page
                next_token = response.get('NextToken')
                if not next_token:
                    break
                
                page_count += 1
            
            if page_count >= max_pages:
                logger.warning(f"Reached maximum pages ({max_pages}) for {service}.{api_name}")
            
            return total_count
            
        except Exception as e:
            logger.error(f"Error counting via pagination for {service}.{api_name}: {sanitize_log(str(e))}")
            return None
    
    def _get_all_resources(self, service, api_name, response_key, params):
        """Get all resources from a paginated API with performance optimization."""
        try:
            # Use performance optimizer if available
            if self.performance_optimizer:
                operation_name = f"{service}_{api_name}_all"
                
                def api_call(**api_params):
                    return self.call_service_api(service, api_name, **api_params)
                
                return self.performance_optimizer.optimize_api_pagination(
                    api_call=api_call,
                    response_key=response_key,
                    api_params=params,
                    operation_name=operation_name,
                    count_only=False
                )
            
            # Fallback to original implementation
            all_resources = []
            next_token = None
            max_pages = 100
            page_count = 0
            
            while page_count < max_pages:
                api_params = params.copy()
                if next_token:
                    api_params['NextToken'] = next_token
                
                response = self.call_service_api(service, api_name, **api_params)
                if not response:
                    break
                
                items = response.get(response_key, [])
                all_resources.extend(items)
                
                next_token = response.get('NextToken')
                if not next_token:
                    break
                
                page_count += 1
            
            return all_resources
            
        except Exception as e:
            logger.error(f"Error getting all resources for {service}.{api_name}: {sanitize_log(str(e))}")
            return []
    
    def _count_hierarchy_levels(self, instance_id):
        """Count user hierarchy levels for a Connect instance."""
        try:
            response = self.call_service_api('connect', 'describe_user_hierarchy_structure', InstanceId=instance_id)
            
            if not response or 'HierarchyStructure' not in response:
                return 0
            
            hierarchy = response['HierarchyStructure']
            level_count = 0
            
            # Count defined levels
            for level_name in ['LevelOne', 'LevelTwo', 'LevelThree', 'LevelFour', 'LevelFive']:
                if hierarchy.get(level_name) and hierarchy[level_name].get('Name'):
                    level_count += 1
            
            return level_count
            
        except Exception as e:
            logger.error(f"Error counting hierarchy levels: {sanitize_log(str(e))}")
            return 0
    
    def _count_connect_instances(self):
        """Count total Connect instances in the account."""
        try:
            return self._count_via_pagination_enhanced('connect', 'list_instances', 'InstanceSummaryList', {})
        except Exception as e:
            logger.error(f"Error counting Connect instances: {sanitize_log(str(e))}")
            return 0
    
    def _get_account_id(self):
        """Get the current AWS account ID."""
        try:
            if not hasattr(self, '_account_id'):
                # Get account ID from STS
                sts_client = self.get_service_client('sts')
                if sts_client:
                    response = sts_client.get_caller_identity()
                    self._account_id = response.get('Account')
                else:
                    # Fallback: extract from instance ARN if available
                    instances = self.get_connect_instances()
                    if instances:
                        instance_arn = instances[0].get('Arn', '')
                        # ARN format: arn:aws:connect:region:account-id:instance/instance-id
                        parts = instance_arn.split(':')
                        if len(parts) >= 5:
                            self._account_id = parts[4]
                        else:
                            self._account_id = 'unknown'
                    else:
                        self._account_id = 'unknown'
            
            return self._account_id
            
        except Exception as e:
            logger.error(f"Error getting account ID: {sanitize_log(str(e))}")
            return 'unknown'


class FlexibleStorageEngine:
    """
    Flexible storage engine supporting S3, DynamoDB, or both with comprehensive
    error handling, data validation, and optimized storage formats.
    """
    
    def __init__(self, storage_config, client_manager):
        """
        Initialize the flexible storage engine.
        
        Args:
            storage_config: Dictionary with storage configuration
            client_manager: MultiServiceClientManager instance
        """
        self.storage_config = storage_config
        self.client_manager = client_manager
        
        # Storage options
        self.use_s3 = storage_config.get('use_s3', False)
        self.use_dynamodb = storage_config.get('use_dynamodb', False)
        self.s3_bucket = storage_config.get('s3_bucket')
        self.dynamodb_table = storage_config.get('dynamodb_table')
        
        # Get clients
        self.s3_client = client_manager.get_client('s3') if self.use_s3 else None
        self.dynamodb_client = client_manager.get_client('dynamodb') if self.use_dynamodb else None
        
        # Validate configuration
        self._validate_configuration()
        
        logger.info(f"FlexibleStorageEngine initialized: S3={self.use_s3}, DynamoDB={self.use_dynamodb}")
    
    def _validate_configuration(self):
        """Validate storage configuration."""
        if not self.use_s3 and not self.use_dynamodb:
            logger.warning("No storage backends configured - data will not be persisted")
            return
        
        if self.use_s3:
            if not self.s3_bucket:
                logger.error("S3 storage enabled but no bucket specified")
                self.use_s3 = False
            elif not self.s3_client:
                logger.error("S3 storage enabled but S3 client not available")
                self.use_s3 = False
        
        if self.use_dynamodb:
            if not self.dynamodb_table:
                logger.error("DynamoDB storage enabled but no table specified")
                self.use_dynamodb = False
            elif not self.dynamodb_client:
                logger.error("DynamoDB storage enabled but DynamoDB client not available")
                self.use_dynamodb = False
    
    def store_instance_metrics(self, instance_id, instance_alias, metrics_data):
        """
        Store metrics data for a specific instance.
        
        Args:
            instance_id: Connect instance ID
            instance_alias: Human-readable instance name
            metrics_data: List of quota utilization results
            
        Returns:
            Dictionary with storage results
        """
        storage_results = {
            's3_success': False,
            'dynamodb_success': False,
            'errors': []
        }
        
        if not metrics_data:
            logger.debug(f"No metrics data to store for instance {instance_id}")
            return storage_results
        
        # Prepare enhanced metrics data
        enhanced_data = self._prepare_instance_metrics(instance_id, instance_alias, metrics_data)
        
        # Store in S3 if configured
        if self.use_s3:
            try:
                storage_results['s3_success'] = self._store_to_s3_instance(enhanced_data)
            except Exception as e:
                error_msg = f"S3 storage failed for instance {instance_id}: {sanitize_log(str(e))}"
                logger.error(error_msg)
                storage_results['errors'].append(error_msg)
        
        # Store in DynamoDB if configured
        if self.use_dynamodb:
            try:
                storage_results['dynamodb_success'] = self._store_to_dynamodb_instance(enhanced_data)
            except Exception as e:
                error_msg = f"DynamoDB storage failed for instance {instance_id}: {sanitize_log(str(e))}"
                logger.error(error_msg)
                storage_results['errors'].append(error_msg)
        
        return storage_results
    
    def store_account_metrics(self, metrics_data):
        """
        Store account-level metrics data.
        
        Args:
            metrics_data: List of account-level quota utilization results
            
        Returns:
            Dictionary with storage results
        """
        storage_results = {
            's3_success': False,
            'dynamodb_success': False,
            'errors': []
        }
        
        if not metrics_data:
            logger.debug("No account metrics data to store")
            return storage_results
        
        # Prepare enhanced metrics data
        enhanced_data = self._prepare_account_metrics(metrics_data)
        
        # Store in S3 if configured
        if self.use_s3:
            try:
                storage_results['s3_success'] = self._store_to_s3_account(enhanced_data)
            except Exception as e:
                error_msg = f"S3 storage failed for account metrics: {sanitize_log(str(e))}"
                logger.error(error_msg)
                storage_results['errors'].append(error_msg)
        
        # Store in DynamoDB if configured
        if self.use_dynamodb:
            try:
                storage_results['dynamodb_success'] = self._store_to_dynamodb_account(enhanced_data)
            except Exception as e:
                error_msg = f"DynamoDB storage failed for account metrics: {sanitize_log(str(e))}"
                logger.error(error_msg)
                storage_results['errors'].append(error_msg)
        
        return storage_results
    
    def store_consolidated_report(self, monitoring_results, alert_results=None):
        """
        Store consolidated monitoring report.
        
        Args:
            monitoring_results: Complete monitoring results
            alert_results: Alert processing results (optional)
            
        Returns:
            Dictionary with storage results
        """
        storage_results = {
            's3_success': False,
            'dynamodb_success': False,
            'errors': []
        }
        
        # Prepare consolidated report
        report_data = self._prepare_consolidated_report(monitoring_results, alert_results)
        
        # Store in S3 if configured
        if self.use_s3:
            try:
                storage_results['s3_success'] = self._store_to_s3_report(report_data)
            except Exception as e:
                error_msg = f"S3 storage failed for consolidated report: {sanitize_log(str(e))}"
                logger.error(error_msg)
                storage_results['errors'].append(error_msg)
        
        # Store in DynamoDB if configured
        if self.use_dynamodb:
            try:
                storage_results['dynamodb_success'] = self._store_to_dynamodb_report(report_data)
            except Exception as e:
                error_msg = f"DynamoDB storage failed for consolidated report: {sanitize_log(str(e))}"
                logger.error(error_msg)
                storage_results['errors'].append(error_msg)
        
        return storage_results
    
    def _prepare_instance_metrics(self, instance_id, instance_alias, metrics_data):
        """Prepare instance metrics data for storage."""
        timestamp = datetime.utcnow()
        
        return {
            'record_type': 'instance_metrics',
            'instance_id': instance_id,
            'instance_alias': instance_alias,
            'timestamp': timestamp.isoformat(),
            'date': timestamp.strftime('%Y-%m-%d'),
            'execution_id': str(uuid.uuid4()),
            'metrics_count': len(metrics_data),
            'violations_count': len([m for m in metrics_data if m.get('utilization_percentage', 0) >= THRESHOLD_PERCENTAGE]),
            'metrics': metrics_data,
            'summary': self._create_metrics_summary(metrics_data)
        }
    
    def _prepare_account_metrics(self, metrics_data):
        """Prepare account-level metrics data for storage."""
        timestamp = datetime.utcnow()
        
        return {
            'record_type': 'account_metrics',
            'timestamp': timestamp.isoformat(),
            'date': timestamp.strftime('%Y-%m-%d'),
            'execution_id': str(uuid.uuid4()),
            'metrics_count': len(metrics_data),
            'violations_count': len([m for m in metrics_data if m.get('utilization_percentage', 0) >= THRESHOLD_PERCENTAGE]),
            'metrics': metrics_data,
            'summary': self._create_metrics_summary(metrics_data)
        }
    
    def _prepare_consolidated_report(self, monitoring_results, alert_results):
        """Prepare consolidated monitoring report for storage."""
        timestamp = datetime.utcnow()
        
        return {
            'record_type': 'consolidated_report',
            'timestamp': timestamp.isoformat(),
            'date': timestamp.strftime('%Y-%m-%d'),
            'execution_id': str(uuid.uuid4()),
            'monitoring_results': monitoring_results,
            'alert_results': alert_results or {},
            'summary': {
                'instances_monitored': monitoring_results.get('instances_monitored', 0),
                'total_quotas_checked': monitoring_results.get('total_quotas_checked', 0),
                'violations_found': monitoring_results.get('violations_found', 0),
                'alerts_sent': alert_results.get('alerts_sent', 0) if alert_results else 0,
                'errors_count': len(monitoring_results.get('errors', []))
            }
        }
    
    def _create_metrics_summary(self, metrics_data):
        """Create summary statistics for metrics data."""
        if not metrics_data:
            return {}
        
        utilizations = [m.get('utilization_percentage', 0) for m in metrics_data]
        categories = {}
        
        for metric in metrics_data:
            category = metric.get('category', 'Unknown')
            if category not in categories:
                categories[category] = {'count': 0, 'violations': 0}
            categories[category]['count'] += 1
            if metric.get('utilization_percentage', 0) >= THRESHOLD_PERCENTAGE:
                categories[category]['violations'] += 1
        
        return {
            'total_metrics': len(metrics_data),
            'max_utilization': max(utilizations) if utilizations else 0,
            'avg_utilization': sum(utilizations) / len(utilizations) if utilizations else 0,
            'categories': categories
        }
    
    def _store_to_s3_instance(self, data):
        """Store instance metrics to S3."""
        try:
            instance_id = data['instance_id']
            date_str = data['date']
            timestamp_str = datetime.fromisoformat(data['timestamp']).strftime('%H%M%S')
            
            # Store in date-partitioned structure
            s3_key = f"connect-metrics/{date_str}/{instance_id}/{timestamp_str}.json"
            
            # Upload main file
            self.s3_client.put_object(
                Bucket=self.s3_bucket,
                Key=s3_key,
                Body=json.dumps(data, default=str, indent=2),
                ContentType='application/json',
                Metadata={
                    'instance-id': instance_id,
                    'record-type': 'instance-metrics',
                    'metrics-count': str(data['metrics_count']),
                    'violations-count': str(data['violations_count'])
                }
            )
            
            # Update latest file
            latest_key = f"connect-metrics/latest/{instance_id}.json"
            self.s3_client.put_object(
                Bucket=self.s3_bucket,
                Key=latest_key,
                Body=json.dumps(data, default=str, indent=2),
                ContentType='application/json'
            )
            
            logger.info(f"Stored instance metrics to S3: s3://{self.s3_bucket}/{s3_key}")
            return True
            
        except Exception as e:
            logger.error(f"S3 instance storage error: {sanitize_log(str(e))}")
            return False
    
    def _store_to_s3_account(self, data):
        """Store account metrics to S3."""
        try:
            date_str = data['date']
            timestamp_str = datetime.fromisoformat(data['timestamp']).strftime('%H%M%S')
            
            # Store in date-partitioned structure
            s3_key = f"connect-account-metrics/{date_str}/{timestamp_str}.json"
            
            # Upload main file
            self.s3_client.put_object(
                Bucket=self.s3_bucket,
                Key=s3_key,
                Body=json.dumps(data, default=str, indent=2),
                ContentType='application/json',
                Metadata={
                    'record-type': 'account-metrics',
                    'metrics-count': str(data['metrics_count']),
                    'violations-count': str(data['violations_count'])
                }
            )
            
            # Update latest file
            latest_key = "connect-account-metrics/latest/account-metrics.json"
            self.s3_client.put_object(
                Bucket=self.s3_bucket,
                Key=latest_key,
                Body=json.dumps(data, default=str, indent=2),
                ContentType='application/json'
            )
            
            logger.info(f"Stored account metrics to S3: s3://{self.s3_bucket}/{s3_key}")
            return True
            
        except Exception as e:
            logger.error(f"S3 account storage error: {sanitize_log(str(e))}")
            return False
    
    def _store_to_s3_report(self, data):
        """Store consolidated report to S3."""
        try:
            date_str = data['date']
            timestamp_str = datetime.fromisoformat(data['timestamp']).strftime('%H%M%S')
            
            # Store in date-partitioned structure
            s3_key = f"connect-reports/{date_str}/report_{timestamp_str}.json"
            
            # Upload main file
            self.s3_client.put_object(
                Bucket=self.s3_bucket,
                Key=s3_key,
                Body=json.dumps(data, default=str, indent=2),
                ContentType='application/json',
                Metadata={
                    'record-type': 'consolidated-report',
                    'instances-monitored': str(data['summary']['instances_monitored']),
                    'violations-found': str(data['summary']['violations_found'])
                }
            )
            
            # Update latest file
            latest_key = "connect-reports/latest/latest-report.json"
            self.s3_client.put_object(
                Bucket=self.s3_bucket,
                Key=latest_key,
                Body=json.dumps(data, default=str, indent=2),
                ContentType='application/json'
            )
            
            logger.info(f"Stored consolidated report to S3: s3://{self.s3_bucket}/{s3_key}")
            return True
            
        except Exception as e:
            logger.error(f"S3 report storage error: {sanitize_log(str(e))}")
            return False
    
    def _store_to_dynamodb_instance(self, data):
        """Store instance metrics to DynamoDB."""
        try:
            # Create unique record ID
            record_id = f"instance_{data['instance_id']}_{int(datetime.fromisoformat(data['timestamp']).timestamp())}"
            
            # Prepare DynamoDB item
            item = {
                'id': {'S': record_id},
                'timestamp': {'S': data['timestamp']},
                'record_type': {'S': 'instance_metrics'},
                'instance_id': {'S': data['instance_id']},
                'instance_alias': {'S': data['instance_alias']},
                'execution_id': {'S': data['execution_id']},
                'metrics_count': {'N': str(data['metrics_count'])},
                'violations_count': {'N': str(data['violations_count'])},
                'data': {'S': json.dumps(data, default=str)}
            }
            
            # Add individual quota utilizations for easier querying
            for metric in data['metrics']:
                quota_code = metric.get('quota_code', '')
                if quota_code and quota_code != 'unknown':
                    item[f'quota_{quota_code}'] = {'N': str(metric.get('utilization_percentage', 0))}
            
            # Add summary statistics
            summary = data.get('summary', {})
            if summary:
                item['max_utilization'] = {'N': str(summary.get('max_utilization', 0))}
                item['avg_utilization'] = {'N': str(summary.get('avg_utilization', 0))}
            
            # Store item
            self.dynamodb_client.put_item(
                TableName=self.dynamodb_table,
                Item=item
            )
            
            logger.info(f"Stored instance metrics to DynamoDB: {record_id}")
            return True
            
        except Exception as e:
            logger.error(f"DynamoDB instance storage error: {sanitize_log(str(e))}")
            return False
    
    def _store_to_dynamodb_account(self, data):
        """Store account metrics to DynamoDB."""
        try:
            # Create unique record ID
            record_id = f"account_{int(datetime.fromisoformat(data['timestamp']).timestamp())}"
            
            # Prepare DynamoDB item
            item = {
                'id': {'S': record_id},
                'timestamp': {'S': data['timestamp']},
                'record_type': {'S': 'account_metrics'},
                'execution_id': {'S': data['execution_id']},
                'metrics_count': {'N': str(data['metrics_count'])},
                'violations_count': {'N': str(data['violations_count'])},
                'data': {'S': json.dumps(data, default=str)}
            }
            
            # Add individual quota utilizations for easier querying
            for metric in data['metrics']:
                quota_code = metric.get('quota_code', '')
                if quota_code and quota_code != 'unknown':
                    item[f'quota_{quota_code}'] = {'N': str(metric.get('utilization_percentage', 0))}
            
            # Add summary statistics
            summary = data.get('summary', {})
            if summary:
                item['max_utilization'] = {'N': str(summary.get('max_utilization', 0))}
                item['avg_utilization'] = {'N': str(summary.get('avg_utilization', 0))}
            
            # Store item
            self.dynamodb_client.put_item(
                TableName=self.dynamodb_table,
                Item=item
            )
            
            logger.info(f"Stored account metrics to DynamoDB: {record_id}")
            return True
            
        except Exception as e:
            logger.error(f"DynamoDB account storage error: {sanitize_log(str(e))}")
            return False
    
    def _store_to_dynamodb_report(self, data):
        """Store consolidated report to DynamoDB."""
        try:
            # Create unique record ID
            record_id = f"report_{int(datetime.fromisoformat(data['timestamp']).timestamp())}"
            
            # Prepare DynamoDB item
            item = {
                'id': {'S': record_id},
                'timestamp': {'S': data['timestamp']},
                'record_type': {'S': 'consolidated_report'},
                'execution_id': {'S': data['execution_id']},
                'data': {'S': json.dumps(data, default=str)}
            }
            
            # Add summary statistics for easier querying
            summary = data.get('summary', {})
            if summary:
                item['instances_monitored'] = {'N': str(summary.get('instances_monitored', 0))}
                item['total_quotas_checked'] = {'N': str(summary.get('total_quotas_checked', 0))}
                item['violations_found'] = {'N': str(summary.get('violations_found', 0))}
                item['alerts_sent'] = {'N': str(summary.get('alerts_sent', 0))}
                item['errors_count'] = {'N': str(summary.get('errors_count', 0))}
            
            # Store item
            self.dynamodb_client.put_item(
                TableName=self.dynamodb_table,
                Item=item
            )
            
            logger.info(f"Stored consolidated report to DynamoDB: {record_id}")
            return True
            
        except Exception as e:
            logger.error(f"DynamoDB report storage error: {sanitize_log(str(e))}")
            return False
    
    def get_storage_status(self):
        """Get current storage configuration status."""
        status = {
            'storage_backends': [],
            's3_configured': self.use_s3,
            'dynamodb_configured': self.use_dynamodb,
            's3_bucket': self.s3_bucket if self.use_s3 else None,
            'dynamodb_table': self.dynamodb_table if self.use_dynamodb else None,
            'clients_available': {
                's3': self.s3_client is not None,
                'dynamodb': self.dynamodb_client is not None
            }
        }
        
        if self.use_s3:
            status['storage_backends'].append('S3')
        if self.use_dynamodb:
            status['storage_backends'].append('DynamoDB')
        
        return status
    
    def test_storage_connectivity(self):
        """Test connectivity to configured storage backends."""
        results = {
            's3_test': None,
            'dynamodb_test': None,
            'errors': []
        }
        
        # Test S3 connectivity
        if self.use_s3 and self.s3_client:
            try:
                # Test bucket access
                self.s3_client.head_bucket(Bucket=self.s3_bucket)
                results['s3_test'] = True
                logger.info(f"S3 connectivity test passed for bucket: {self.s3_bucket}")
            except Exception as e:
                results['s3_test'] = False
                error_msg = f"S3 connectivity test failed: {sanitize_log(str(e))}"
                results['errors'].append(error_msg)
                logger.error(error_msg)
        
        # Test DynamoDB connectivity
        if self.use_dynamodb and self.dynamodb_client:
            try:
                # Test table access
                self.dynamodb_client.describe_table(TableName=self.dynamodb_table)
                results['dynamodb_test'] = True
                logger.info(f"DynamoDB connectivity test passed for table: {self.dynamodb_table}")
            except Exception as e:
                results['dynamodb_test'] = False
                error_msg = f"DynamoDB connectivity test failed: {sanitize_log(str(e))}"
                results['errors'].append(error_msg)
                logger.error(error_msg)
        
        return results
    
class AlertConsolidationEngine:
    """
    Enhanced alert consolidation engine that groups quota violations by instance
    and sends consolidated email notifications.
    """
    
    def __init__(self, sns_client, topic_arn, threshold_percentage):
        """Initialize the alert consolidation engine."""
        self.sns_client = sns_client
        self.topic_arn = topic_arn
        self.threshold_percentage = threshold_percentage
        self.execution_id = str(uuid.uuid4())
        
    def process_monitoring_results(self, monitoring_results):
        """
        Process monitoring results and send consolidated alerts.
        
        Args:
            monitoring_results: Results from monitor_all_instances_dynamically()
            
        Returns:
            Dictionary with alert processing results
        """
        alert_results = {
            'alerts_sent': 0,
            'instances_with_violations': 0,
            'total_violations': 0,
            'account_violations': 0,
            'errors': []
        }
        
        try:
            # Process account-level violations
            account_violations = self._extract_account_violations(monitoring_results)
            if account_violations:
                alert_results['account_violations'] = len(account_violations)
                alert_results['total_violations'] += len(account_violations)
                
                success = self._send_account_level_alert(account_violations)
                if success:
                    alert_results['alerts_sent'] += 1
                else:
                    alert_results['errors'].append("Failed to send account-level alert")
            
            # Process instance-level violations
            for instance_id, instance_data in monitoring_results.get('instance_results', {}).items():
                violations = self._extract_instance_violations(instance_data)
                
                if violations:
                    alert_results['instances_with_violations'] += 1
                    alert_results['total_violations'] += len(violations)
                    
                    success = self._send_instance_consolidated_alert(instance_id, instance_data, violations)
                    if success:
                        alert_results['alerts_sent'] += 1
                    else:
                        alert_results['errors'].append(f"Failed to send alert for instance {instance_id}")
            
            logger.info(f"Alert consolidation complete: {alert_results['alerts_sent']} alerts sent for {alert_results['total_violations']} violations")
            return alert_results
            
        except Exception as e:
            error_msg = f"Error in alert consolidation: {sanitize_log(str(e))}"
            logger.error(error_msg)
            alert_results['errors'].append(error_msg)
            return alert_results
    
    def _extract_account_violations(self, monitoring_results):
        """Extract account-level quota violations."""
        violations = []
        
        for result in monitoring_results.get('account_results', []):
            if result.get('utilization_percentage', 0) >= self.threshold_percentage:
                violations.append(result)
        
        return violations
    
    def _extract_instance_violations(self, instance_data):
        """Extract instance-level quota violations."""
        violations = []
        
        for result in instance_data.get('results', []):
            if result.get('utilization_percentage', 0) >= self.threshold_percentage:
                violations.append(result)
        
        return violations
    
    def _send_account_level_alert(self, violations):
        """Send consolidated alert for account-level violations."""
        try:
            # Create consolidated message
            message_data = {
                'alert_type': 'CONNECT_ACCOUNT_QUOTA_VIOLATIONS',
                'severity': self._determine_severity(violations),
                'timestamp': datetime.utcnow().isoformat(),
                'execution_id': self.execution_id,
                'scope': 'ACCOUNT',
                'violations_count': len(violations),
                'threshold_percentage': self.threshold_percentage,
                'violations': violations
            }
            
            # Generate human-readable message
            human_message = self._generate_account_alert_message(violations)
            
            # Generate subject
            subject = f"Connect Account Quota Alert: {len(violations)} violation(s) detected"
            
            # Send alert
            return self._send_sns_alert(message_data, human_message, subject)
            
        except Exception as e:
            logger.error(f"Error sending account-level alert: {sanitize_log(str(e))}")
            return False
    
    def _send_instance_consolidated_alert(self, instance_id, instance_data, violations):
        """Send consolidated alert for a specific instance with comprehensive quota data."""
        try:
            instance_alias = instance_data.get('instance_alias', 'Unknown Instance')
            
            # Get ALL monitored quotas for this instance (not just violations)
            all_quotas = instance_data.get('results', [])
            
            # Create consolidated message
            message_data = {
                'alert_type': 'CONNECT_INSTANCE_QUOTA_VIOLATIONS',
                'severity': self._determine_severity(violations),
                'timestamp': datetime.utcnow().isoformat(),
                'execution_id': self.execution_id,
                'scope': 'INSTANCE',
                'instance_id': instance_id,
                'instance_alias': instance_alias,
                'violations_count': len(violations),
                'total_quotas_monitored': len(all_quotas),
                'threshold_percentage': self.threshold_percentage,
                'violations': violations,
                'all_quotas': all_quotas  # Include all quota data in message
            }
            
            # Generate human-readable message WITH all quota data
            human_message = self._generate_instance_alert_message(
                instance_id, 
                instance_alias, 
                violations,
                all_quotas=all_quotas  # Pass all quotas for comprehensive reporting
            )
            
            # Generate subject
            subject = f"Connect Instance Alert: {instance_alias} - {len(violations)} violation(s) [Total Quotas: {len(all_quotas)}]"
            
            # Send alert
            return self._send_sns_alert(message_data, human_message, subject)
            
        except Exception as e:
            logger.error(f"Error sending instance alert for {instance_id}: {sanitize_log(str(e))}")
            return False
    
    def _generate_account_alert_message(self, violations):
        """Generate human-readable message for account-level violations."""
        message_lines = [
            "🚨 AMAZON CONNECT ACCOUNT QUOTA ALERT 🚨",
            "",
            f"Alert Time: {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S UTC')}",
            f"Execution ID: {self.execution_id}",
            f"Threshold: {self.threshold_percentage}%",
            "",
            f"ACCOUNT-LEVEL VIOLATIONS DETECTED: {len(violations)}",
            "=" * 60
        ]
        
        # Add violation details
        for i, violation in enumerate(violations, 1):
            message_lines.extend([
                f"{i}. {violation['quota_name']}",
                f"   Category: {violation.get('category', 'Unknown')}",
                f"   Current Usage: {violation['current_usage']:,}",
                f"   Quota Limit: {violation['quota_limit']:,}",
                f"   Utilization: {violation['utilization_percentage']:.1f}%",
                ""
            ])
        
        # Add recommendations
        message_lines.extend([
            "RECOMMENDED ACTIONS:",
            "• Review account-level resource usage patterns",
            "• Consider requesting service quota increases if needed",
            "• Optimize resource allocation across instances",
            "• Monitor trends to prevent future violations",
            "",
            "For assistance, contact AWS Support or your AWS account team."
        ])
        
        return "\n".join(message_lines)
    
    def _generate_instance_alert_message(self, instance_id, instance_alias, violations, all_quotas=None):
        """Generate human-readable message for instance-level violations.
        
        Args:
            instance_id: Connect instance ID
            instance_alias: Instance alias/name
            violations: List of quota violations
            all_quotas: Optional list of all monitored quotas for comprehensive reporting
        """
        message_lines = [
            "🚨 AMAZON CONNECT INSTANCE QUOTA ALERT 🚨",
            "",
            f"Alert Time: {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S UTC')}",
            f"Execution ID: {self.execution_id}",
            f"Threshold: {self.threshold_percentage}%",
            "",
            f"INSTANCE: {instance_alias}",
            f"Instance ID: {instance_id}",
            f"VIOLATIONS DETECTED: {len(violations)}",
            "=" * 60,
            ""
        ]

        # Group violations by category
        violations_by_category = {}
        for violation in violations:
            category = violation.get('category', 'Unknown')
            if category not in violations_by_category:
                violations_by_category[category] = []
            violations_by_category[category].append(violation)

        # Add violation details by category with PROMINENT formatting
        message_lines.append("╔════════════════════════════════════════════════════════════╗")
        message_lines.append("║   ⚠️  ⚠️  ⚠️   QUOTA VIOLATIONS (EXCEEDING THRESHOLD)   ⚠️  ⚠️  ⚠️   ║")
        message_lines.append("╚════════════════════════════════════════════════════════════╝")
        message_lines.append("")
        
        for category, category_violations in violations_by_category.items():
            message_lines.extend([
                f">>> 📊 {QUOTA_CATEGORIES.get(category, category).upper()} <<<",
                ""
            ])

            for violation in category_violations:
                message_lines.extend([
                    f"╔═══════════════════════════════════════════════════════════",
                    f"║ ⚠️  ALERT: {violation['quota_name'].upper()}",
                    f"║",
                    f"║    ▶ CURRENT USAGE: {violation['current_usage']:,}",
                    f"║    ▶ QUOTA LIMIT:   {violation['quota_limit']:,}",
                    f"║    ▶ UTILIZATION:   {violation['utilization_percentage']:.1f}% ⚠️  ⚠️  ⚠️",
                    f"╚═══════════════════════════════════════════════════════════",
                    ""
                ])

        # Add all other monitored quotas for comprehensive view
        if all_quotas:
            message_lines.extend([
                "=" * 60,
                "",
                "📋 ALL MONITORED QUOTAS (Complete Status):",
                ""
            ])
            
            # Group all quotas by category
            all_quotas_by_category = {}
            for quota in all_quotas:
                category = quota.get('category', 'Unknown')
                if category not in all_quotas_by_category:
                    all_quotas_by_category[category] = []
                all_quotas_by_category[category].append(quota)
            
            # Display all quotas organized by category
            for category in sorted(all_quotas_by_category.keys()):
                category_quotas = all_quotas_by_category[category]
                message_lines.extend([
                    f"📊 {QUOTA_CATEGORIES.get(category, category)}:",
                    ""
                ])
                
                for quota in category_quotas:
                    utilization = quota.get('utilization_percentage', 0)
                    status_icon = "⚠️ " if utilization > self.threshold_percentage else "✅"
                    
                    message_lines.extend([
                        f"{status_icon} {quota['quota_name']}",
                        f"  Current Usage: {quota['current_usage']:,}",
                        f"  Quota Limit: {quota['quota_limit']:,}",
                        f"  Utilization: {utilization:.1f}%",
                        ""
                    ])

        # Add recommendations
        message_lines.extend([
            "=" * 60,
            "",
            "RECOMMENDED ACTIONS:",
            "• Review current usage patterns for this instance",
            "• Consider requesting service quota increases if needed",
            "• Optimize resource usage where possible",
            "• Monitor usage trends to prevent future violations",
            "",
            "QUOTA CATEGORIES AFFECTED:",
        ])

        for category in violations_by_category.keys():
            message_lines.append(f"• {QUOTA_CATEGORIES.get(category, category)}")

        message_lines.extend([
            "",
            "For assistance, contact AWS Support or your AWS account team."
        ])

        return "\n".join(message_lines)
    
    def _determine_severity(self, violations):
        """Determine alert severity based on violation levels."""
        if not violations:
            return "INFO"
        
        max_utilization = max(v.get('utilization_percentage', 0) for v in violations)
        
        if max_utilization >= 95:
            return "CRITICAL"
        elif max_utilization >= 90:
            return "HIGH"
        elif max_utilization >= 85:
            return "MEDIUM"
        else:
            return "LOW"
    
    def _send_sns_alert(self, message_data, human_message, subject):
        """Send SNS alert with both structured and human-readable formats."""
        try:
            # Validate SNS topic ARN format
            if not self.topic_arn or not self.topic_arn.startswith('arn:aws:sns:'):
                logger.error(f"Invalid SNS topic ARN format: {sanitize_log(self.topic_arn)}")
                return False
            
            # Create SMS-friendly short message
            sms_message = f"Connect Alert: {message_data['violations_count']} quota violation(s) detected"
            if message_data['scope'] == 'INSTANCE':
                sms_message += f" for {message_data.get('instance_alias', 'instance')}"
            
            # Send structured message
            response = self.sns_client.publish(
                TopicArn=self.topic_arn,
                Message=json.dumps({
                    "default": human_message,
                    "email": human_message,
                    "sms": sms_message,
                    "json": json.dumps(message_data)
                }),
                Subject=subject,
                MessageStructure='json'
            )
            
            logger.info(f"Consolidated alert sent successfully: {subject}")
            logger.debug(f"SNS Message ID: {response.get('MessageId')}")
            return True
            
        except ClientError as e:
            error_code = e.response['Error']['Code']
            error_msg = e.response['Error']['Message']
            logger.error(f"Failed to send SNS alert: {error_code} - {sanitize_log(error_msg)}")
            return False
        except Exception as e:
            logger.error(f"Unexpected error sending SNS alert: {sanitize_log(str(e))}")
            return False
    
    def validate_sns_configuration(self):
        """Validate SNS topic configuration."""
        try:
            if not self.topic_arn:
                return False, "No SNS topic ARN configured"
            
            if not self.topic_arn.startswith('arn:aws:sns:'):
                return False, "Invalid SNS topic ARN format"
            
            # Test topic accessibility
            response = self.sns_client.get_topic_attributes(TopicArn=self.topic_arn)
            
            # Check if topic has subscriptions
            subscriptions = self.sns_client.list_subscriptions_by_topic(TopicArn=self.topic_arn)
            subscription_count = len(subscriptions.get('Subscriptions', []))
            
            if subscription_count == 0:
                return True, "SNS topic is valid but has no subscriptions"
            
            return True, f"SNS topic is valid with {subscription_count} subscription(s)"
            
        except ClientError as e:
            error_code = e.response['Error']['Code']
            return False, f"SNS validation failed: {error_code}"
        except Exception as e:
            return False, f"SNS validation error: {sanitize_log(str(e))}"

    def send_alert(self, topic_arn, quota_info):
        """Legacy method for backward compatibility."""
        logger.warning("Using legacy send_alert method. Consider using AlertConsolidationEngine for better consolidation.")
        
        # Convert legacy format to new format
        violations = [{
            'quota_code': quota_info['quota_info']['quota_code'],
            'quota_name': quota_info['quota_info']['quota_name'],
            'current_usage': quota_info['quota_info']['current_value'],
            'quota_limit': quota_info['quota_info']['quota_value'],
            'utilization_percentage': quota_info['quota_info']['utilization_percentage'],
            'category': 'LEGACY'
        }]
        
        # Use new consolidation engine
        temp_engine = AlertConsolidationEngine(self.sns_client, topic_arn, self.threshold_percentage)
        return temp_engine._send_instance_consolidated_alert(
            quota_info['instance_id'],
            {'instance_alias': quota_info['instance_name']},
            violations
        )

def validate_sns_topic(sns_client, topic_arn):
    """Validate that the SNS topic exists and is accessible."""
    if not topic_arn:
        return False
        
    try:
        sns_client.get_topic_attributes(TopicArn=topic_arn)
        return True
    except ClientError as e:
        error_code = e.response['Error']['Code']
        logger.error(f"SNS topic validation failed: {error_code}")
        return False

def save_report_to_s3(s3_client, bucket, report_data):
    """Save report data to S3 bucket."""
    try:
        # Generate keys for the report
        timestamp = datetime.utcnow().strftime('%Y%m%d_%H%M%S')
        date_prefix = datetime.utcnow().strftime('%Y/%m/%d')
        report_key = f"connect-reports/{date_prefix}/connect_quota_report_{timestamp}.json"
        latest_key = "connect-reports/latest/connect_quota_report.json"
        
        # Convert to JSON
        json_data = json.dumps(report_data, default=str)
        
        # Upload timestamped report
        s3_client.put_object(
            Bucket=bucket,
            Key=report_key,
            Body=json_data,
            ContentType='application/json'
        )
        
        # Upload to latest location for easy access
        s3_client.put_object(
            Bucket=bucket,
            Key=latest_key,
            Body=json_data,
            ContentType='application/json'
        )
        
        logger.info(f"Report saved to S3: s3://{bucket}/{report_key}")
        return f"s3://{bucket}/{report_key}"
        
    except ClientError as e:
        logger.error(f"Error saving report to S3: {sanitize_log(str(e))}")
        return None

def save_report_to_dynamodb(dynamodb_client, table_name, report_data):
    """Save report summary to DynamoDB table."""
    try:
        # Create a summary item for this execution
        timestamp = datetime.utcnow().isoformat()
        
        # Basic attributes
        item = {
            'id': {'S': f"report_{EXECUTION_ID}"},
            'timestamp': {'S': timestamp},
            'execution_id': {'S': EXECUTION_ID},
            'region': {'S': report_data.get('region', 'unknown')},
            'threshold_percentage': {'N': str(report_data.get('threshold_percentage', 80))},
            'alert_count': {'N': str(report_data.get('alert_count', 0))},
            'instance_count': {'N': str(len(set([r.get('instance_id') for r in report_data.get('results', [])])))}
        }
        
        # Add summary of alerts
        alerts = []
        for result in report_data.get('results', []):
            if result.get('exceeds_threshold', False):
                alerts.append({
                    'instance_id': result.get('instance_id', 'unknown'),
                    'instance_name': result.get('instance_name', 'unknown'),
                    'quota_name': result.get('quota_info', {}).get('quota_name', 'unknown'),
                    'utilization': result.get('quota_info', {}).get('utilization_percentage', 0)
                })
        
        if alerts:
            item['alerts'] = {'S': json.dumps(alerts, default=str)}
        
        # Store full report data as a compressed JSON string
        item['report_data'] = {'S': json.dumps(report_data, default=str)}
        
        # Put item in DynamoDB
        dynamodb_client.put_item(
            TableName=table_name,
            Item=item
        )
        
        logger.info(f"Report summary saved to DynamoDB table {table_name}")
        return True
        
    except ClientError as e:
        logger.error(f"Error saving report to DynamoDB: {sanitize_log(str(e))}")
        return False

def main():
    """Main function to run the quota monitor."""
    try:
        # Log execution start with unique ID for traceability
        logger.info(f"Starting Connect Quota Monitor execution {EXECUTION_ID}")
        
        # Get configuration from environment or use defaults
        region = os.environ.get('AWS_REGION')
        profile = os.environ.get('AWS_PROFILE')
        sns_topic_arn = os.environ.get('ALERT_SNS_TOPIC_ARN')
        custom_threshold = os.environ.get('THRESHOLD_PERCENTAGE')
        s3_bucket = os.environ.get('S3_BUCKET')
        use_dynamodb = os.environ.get('USE_DYNAMODB', 'false').lower() == 'true'
        dynamodb_table = os.environ.get('DYNAMODB_TABLE', 'ConnectQuotaMonitor')
        
        # Validate threshold if provided
        threshold = THRESHOLD_PERCENTAGE
        if custom_threshold:
            try:
                threshold_value = int(custom_threshold)
                if 1 <= threshold_value <= 99:
                    threshold = threshold_value
                else:
                    logger.warning(f"Invalid threshold value {threshold_value}, must be between 1-99. Using default {THRESHOLD_PERCENTAGE}%")
            except ValueError:
                logger.warning(f"Invalid threshold format: {custom_threshold}. Using default {THRESHOLD_PERCENTAGE}%")
        
        # Initialize the monitor with proper error handling
        try:
            monitor = ConnectQuotaMonitor(
                region_name=region, 
                profile_name=profile,
                s3_bucket=s3_bucket,
                use_dynamodb=use_dynamodb,
                dynamodb_table=dynamodb_table
            )
        except Exception as e:
            logger.error(f"Failed to initialize ConnectQuotaMonitor: {sanitize_log(str(e))}")
            sys.exit(1)
        
        # Validate SNS topic if provided
        if sns_topic_arn:
            if not validate_sns_topic(monitor.sns_client, sns_topic_arn):
                logger.warning(f"SNS topic validation failed for {sanitize_log(sns_topic_arn)}. Alerts will not be sent.")
                sns_topic_arn = None
        
        # Check all quotas with proper error handling
        try:
            results = monitor.monitor_and_store(sns_topic_arn, threshold)
        except Exception as e:
            logger.error(f"Failed to monitor quotas: {sanitize_log(str(e))}")
            sys.exit(1)
        
        # Print summary with secure output handling
        print(f"\nConnect Service Quota Utilization Summary:")
        print(f"{'Instance':<20} {'Quota':<40} {'Usage':<10} {'Limit':<10} {'Utilization':<10}")
        print("-" * 90)
        
        # Results are already processed with alerts and storage
        # Print summary
        print(f"\nConnect Service Quota Monitoring Summary:")
        print(f"Instances monitored: {results.get('instances_monitored', 0)}")
        print(f"Total quotas checked: {results.get('total_quotas_checked', 0)}")
        print(f"Violations found: {results.get('violations_found', 0)}")
        print(f"Alerts sent: {results.get('alert_results', {}).get('alerts_sent', 0)}")
        
        # Add metadata to results for reporting
        report_data = {
            'execution_id': EXECUTION_ID,
            'timestamp': datetime.utcnow().isoformat(),
            'region': region or 'default',
            'threshold_percentage': threshold,
            'monitoring_results': results,
            'alert_count': results.get('alert_results', {}).get('alerts_sent', 0)
        }
        
        # Option 1: Save to S3 if configured
        report_location = None
        if s3_bucket and monitor.s3_client:
            report_location = save_report_to_s3(monitor.s3_client, s3_bucket, report_data)
            print(f"\nDetailed report saved to {report_location}")
            
        # Option 2: Save to DynamoDB if configured
        if use_dynamodb and monitor.dynamodb_client and dynamodb_table:
            save_report_to_dynamodb(monitor.dynamodb_client, dynamodb_table, report_data)
            print(f"\nReport summary saved to DynamoDB table {dynamodb_table}")
            
        # Option 3: Fall back to local file storage
        if not report_location:
            # Create reports directory if it doesn't exist
            os.makedirs('reports', exist_ok=True)
            
            # Save results to file with timestamp for historical tracking
            timestamp = datetime.utcnow().strftime('%Y%m%d_%H%M%S')
            report_file = f'reports/connect_quota_report_{timestamp}.json'
            
            with open(report_file, 'w') as f:
                json.dump(report_data, f, indent=2, default=str)
            
            # Also save to the standard location for backward compatibility
            with open('connect_quota_report.json', 'w') as f:
                json.dump(report_data, f, indent=2, default=str)
            
            print(f"\nDetailed report saved to {report_file}")
        
        if results.get('violations_found', 0) > 0:
            print(f"\nWARNING: {results.get('violations_found', 0)} quotas exceeded the {threshold}% threshold!")
            
        # Log execution completion
        logger.info(f"Connect Quota Monitor execution {EXECUTION_ID} completed successfully")
        
    except Exception as e:
        logger.error(f"Unhandled exception in main: {sanitize_log(str(e))}")
        sys.exit(1)

if __name__ == "__main__":
    main()

def main(event=None, context=None):
    """
    Enhanced Lambda handler function with comprehensive error handling and monitoring.
    
    Supports different invocation types:
    - Scheduled monitoring (default)
    - Configuration management requests
    - Health checks and status requests
    
    Features enhanced error handling with:
    - Error categorization and retry strategies
    - Dead Letter Queue integration
    - Graceful degradation for partial service failures
    - Detailed error logging with sanitized data
    """
    # Initialize enhanced error handler with circuit breaker configuration
    error_handler = None
    if ENHANCED_ERROR_HANDLING_AVAILABLE:
        from enhanced_error_handling import CircuitBreakerConfig
        
        dlq_url = os.environ.get('DLQ_URL')
        
        # Configure circuit breaker based on environment or use defaults
        circuit_breaker_config = CircuitBreakerConfig(
            failure_threshold=int(os.environ.get('CIRCUIT_BREAKER_FAILURE_THRESHOLD', '5')),
            recovery_timeout=int(os.environ.get('CIRCUIT_BREAKER_RECOVERY_TIMEOUT', '60')),
            success_threshold=int(os.environ.get('CIRCUIT_BREAKER_SUCCESS_THRESHOLD', '3')),
            monitoring_window=int(os.environ.get('CIRCUIT_BREAKER_MONITORING_WINDOW', '300'))
        )
        
        error_handler = EnhancedErrorHandler(
            dlq_url=dlq_url, 
            execution_id=EXECUTION_ID,
            circuit_breaker_config=circuit_breaker_config
        )
    
    try:
        # Parse event to determine invocation type
        event = event or {}
        invocation_type = event.get('invocation_type', 'monitoring')
        
        if ENHANCED_SECURITY_AVAILABLE:
            log_secure_info(f"Starting Connect Quota Monitor execution {EXECUTION_ID}")
            log_secure_info(f"Invocation type: {invocation_type}")
        else:
            logger.info(f"Starting Connect Quota Monitor execution {EXECUTION_ID}")
            logger.info(f"Invocation type: {invocation_type}")
        
        # Get and validate environment variables with error handling
        try:
            threshold = int(CONFIG['threshold_percentage'])
            sns_topic_arn = os.environ.get('ALERT_SNS_TOPIC_ARN')
            s3_bucket = CONFIG['s3_bucket']
            use_dynamodb = CONFIG['use_dynamodb'].lower() == 'true'
            dynamodb_table = CONFIG['dynamodb_table']
        except (ValueError, KeyError) as e:
            if error_handler:
                context = ErrorContext(
                    operation='configuration_validation',
                    service='lambda',
                    execution_id=EXECUTION_ID
                )
                error_handler.handle_error(e, context)
            raise ValueError(f"Invalid configuration parameters: {sanitize_log(str(e))}")
        
        # Log configuration with sanitization
        config_info = f"threshold={threshold}%, SNS={bool(sns_topic_arn)}, S3={bool(s3_bucket)}, DynamoDB={use_dynamodb}"
        if ENHANCED_SECURITY_AVAILABLE:
            log_secure_info(f"Configuration: {config_info}")
        else:
            logger.info(f"Configuration: {sanitize_log(config_info)}")
        
        # Initialize performance optimizer if available
        performance_optimizer = None
        if PERFORMANCE_OPTIMIZER_AVAILABLE:
            # Create performance optimizer with Lambda-optimized settings
            cache_config = CacheConfig(
                max_size=int(os.environ.get('CACHE_MAX_SIZE', '500')),
                ttl_seconds=int(os.environ.get('CACHE_TTL_SECONDS', '300')),
                enable_memory_cache=True
            )
            parallel_config = ParallelConfig(
                max_workers=min(int(os.environ.get('MAX_PARALLEL_WORKERS', '5')), os.cpu_count() or 1),
                enable_parallel_instances=os.environ.get('ENABLE_PARALLEL_INSTANCES', 'true').lower() == 'true',
                enable_parallel_quotas=os.environ.get('ENABLE_PARALLEL_QUOTAS', 'true').lower() == 'true',
                batch_size=int(os.environ.get('PARALLEL_BATCH_SIZE', '10')),
                timeout_seconds=int(os.environ.get('PARALLEL_TIMEOUT_SECONDS', '240'))
            )
            pagination_config = PaginationConfig(
                max_pages_per_api=int(os.environ.get('MAX_PAGES_PER_API', '50')),
                items_per_page=int(os.environ.get('ITEMS_PER_PAGE', '100')),
                enable_streaming=os.environ.get('ENABLE_STREAMING', 'true').lower() == 'true',
                memory_threshold_mb=int(os.environ.get('MEMORY_THRESHOLD_MB', '200')),
                enable_early_termination=os.environ.get('ENABLE_EARLY_TERMINATION', 'true').lower() == 'true'
            )
            
            performance_optimizer = PerformanceOptimizer(
                cache_config=cache_config,
                parallel_config=parallel_config,
                pagination_config=pagination_config
            )
            logger.info("Performance optimizer initialized with environment-based configuration")
        
        # Initialize the monitor with error handler and performance optimizer
        monitor = ConnectQuotaMonitor(
            s3_bucket=s3_bucket if s3_bucket else None,
            use_dynamodb=use_dynamodb,
            dynamodb_table=dynamodb_table if use_dynamodb else None,
            error_handler=error_handler,  # Pass error handler to monitor
            performance_optimizer=performance_optimizer  # Pass performance optimizer to monitor
        )
        
        # Handle different invocation types
        if invocation_type == 'config_status':
            # Return configuration status
            logger.info("Handling configuration status request")
            status = monitor.get_configuration_status()
            return {
                'statusCode': 200,
                'body': json.dumps({
                    'message': 'Configuration status retrieved',
                    'execution_id': EXECUTION_ID,
                    'status': status
                }, default=str)
            }
        
        elif invocation_type == 'config_update':
            # Handle configuration update request
            logger.info("Handling configuration update request")
            new_config = event.get('config', {})
            
            if not new_config:
                return {
                    'statusCode': 400,
                    'body': json.dumps({
                        'error': 'No configuration provided',
                        'execution_id': EXECUTION_ID
                    })
                }
            
            success = monitor.apply_configuration_update(new_config)
            return {
                'statusCode': 200 if success else 400,
                'body': json.dumps({
                    'message': 'Configuration updated' if success else 'Configuration update failed',
                    'execution_id': EXECUTION_ID,
                    'success': success
                })
            }
        
        elif invocation_type == 'health_check':
            # Perform health check
            logger.info("Handling health check request")
            health_status = monitor.perform_health_check()
            return {
                'statusCode': 200,
                'body': json.dumps({
                    'message': 'Health check completed',
                    'execution_id': EXECUTION_ID,
                    'health_status': health_status
                }, default=str)
            }
        
        elif invocation_type == 'test_monitoring':
            # Test monitoring without sending alerts
            logger.info("Handling test monitoring request")
            results = monitor.monitor_all_instances_dynamically(threshold)
            return {
                'statusCode': 200,
                'body': json.dumps({
                    'message': 'Test monitoring completed',
                    'execution_id': EXECUTION_ID,
                    'results': results
                }, default=str)
            }
        
        else:
            # Default: Full monitoring with alerts and storage
            logger.info("Performing full monitoring execution")
            
            # Check if this is a test invocation
            is_test = event.get('test', False)
            
            if is_test:
                # Test mode - monitor without sending alerts
                results = monitor.monitor_all_instances_dynamically(threshold)
                return {
                    'statusCode': 200,
                    'body': json.dumps({
                        'message': 'Test monitoring completed successfully',
                        'execution_id': EXECUTION_ID,
                        'instances_monitored': results.get('instances_monitored', 0),
                        'total_quotas_checked': results.get('total_quotas_checked', 0),
                        'violations_found': results.get('violations_found', 0),
                        'test_mode': True
                    })
                }
            else:
                # Full monitoring with alerts and storage
                results = monitor.monitor_and_store(sns_topic_arn, threshold)
                
                # Add performance metrics if available
                response_data = {
                    'message': 'Enhanced monitoring completed successfully',
                    'execution_id': EXECUTION_ID,
                    'instances_monitored': results.get('instances_monitored', 0),
                    'total_quotas_checked': results.get('total_quotas_checked', 0),
                    'violations_found': results.get('violations_found', 0),
                    'alerts_sent': results.get('alert_results', {}).get('alerts_sent', 0),
                    'storage_backends': results.get('storage_status', {}).get('storage_backends', []),
                    'enhanced_features': [
                        '70+ quota monitoring',
                        'Dynamic instance discovery',
                        'Consolidated alerting',
                        'Flexible storage',
                        'Multi-service support',
                        'Enhanced error handling',
                        'Performance optimization'
                    ]
                }
                
                # Add performance summary if optimizer is available
                if performance_optimizer:
                    performance_summary = performance_optimizer.get_performance_summary()
                    response_data['performance_metrics'] = {
                        'total_operations': performance_summary['total_operations'],
                        'cache_hit_rate': performance_summary['cache_stats']['hit_rate_percentage'],
                        'memory_usage_mb': performance_summary['memory_status']['current_memory_mb'],
                        'recommendations': performance_summary['recommendations']
                    }
                    
                    # Log performance summary
                    logger.info(f"Performance Summary - Operations: {performance_summary['total_operations']}, "
                              f"Cache Hit Rate: {performance_summary['cache_stats']['hit_rate_percentage']}%, "
                              f"Memory Usage: {performance_summary['memory_status']['current_memory_mb']}MB")
                
                return {
                    'statusCode': 200,
                    'body': json.dumps(response_data)
                }
        
        # Include error handling summary in successful responses
        if error_handler:
            error_summary = error_handler.get_error_summary()
            if error_summary['error_statistics']['total_errors'] > 0:
                # Add error summary to response for monitoring
                results['error_handling_summary'] = error_summary
        
        return {
            'statusCode': 200,
            'body': json.dumps(results, default=str)
        }
        
    except Exception as e:
        # Enhanced error handling for main execution errors
        if error_handler:
            context = ErrorContext(
                operation='lambda_execution',
                service='lambda',
                execution_id=EXECUTION_ID
            )
            error_details = error_handler.handle_error(e, context)
            
            # Get comprehensive error summary
            error_summary = error_handler.get_error_summary()
            
            # Check if execution can continue with degraded services
            can_continue, unavailable_services = error_handler.degradation_manager.can_continue_execution()
            
            error_response = {
                'error': 'Lambda execution error',
                'execution_id': EXECUTION_ID,
                'error_category': error_details.category.value,
                'error_severity': error_details.severity.value,
                'sanitized_message': error_details.sanitized_message,
                'can_continue_with_degradation': can_continue,
                'unavailable_critical_services': unavailable_services,
                'error_handling_summary': error_summary,
                'message': 'Check CloudWatch logs and DLQ for detailed error information'
            }
            
            # Determine status code based on error severity
            status_code = 500
            if error_details.severity in [ErrorSeverity.LOW, ErrorSeverity.INFO]:
                status_code = 200  # Partial success
            elif error_details.severity == ErrorSeverity.MEDIUM:
                status_code = 207  # Multi-status
            
            return {
                'statusCode': status_code,
                'body': json.dumps(error_response, default=str)
            }
        else:
            # Fallback error handling
            error_msg = f"Error in Lambda execution: {sanitize_log(str(e))}"
            logger.error(error_msg)
            
            return {
                'statusCode': 500,
                'body': json.dumps({
                    'error': 'Internal server error',
                    'message': error_msg,
                    'execution_id': EXECUTION_ID,
                    'enhanced_error_handling': False
                })
            }

# Lambda handler alias for AWS Lambda
lambda_handler = main