#!/usr/bin/env python3
"""
Create detailed coverage report showing which Excel quotas are monitored and why.
Outputs an enhanced Excel file with monitoring status.
"""

import sys
import os
import pandas as pd

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lambda_function import ENHANCED_CONNECT_QUOTA_METRICS

# Load the reference Excel file
EXCEL_PATH = "/Users/svguru/Documents/Connect Quota_REAL_USE THIS.xlsx"
OUTPUT_PATH = "/Users/svguru/FirstAccount/sample-amazon-connect-service-quota-monitor/QUOTA_MONITORING_STATUS.xlsx"

def determine_monitoring_status(row):
    """Determine if a quota is monitored and why/why not."""
    quota_code = row.get('Quota code', '').strip()
    quota_name = row.get('Quota name', '').strip()
    adjustability = row.get('Adjustability', '').strip()
    
    if not quota_code or not quota_code.startswith('L-'):
        return 'NOT TESTED', 'Invalid quota code format'
    
    # Check if quota is in our monitoring code
    if quota_code in ENHANCED_CONNECT_QUOTA_METRICS:
        config = ENHANCED_CONNECT_QUOTA_METRICS[quota_code]
        method = config.get('method', 'unknown')
        
        if method == 'api_count':
            api_method = config.get('api_method', 'unknown')
            return 'TESTED', f'Monitored by counting resources via {api_method} API'
        elif method == 'service_quotas':
            return 'TESTED', f'Monitored via Service Quotas API ({adjustability})'
        elif method == 'throttle_detection':
            return 'TESTED', 'Monitored via CloudWatch throttle detection'
        elif method == 'cloudwatch_usage':
            return 'TESTED', 'Monitored via CloudWatch AWS/Usage metrics (pre-throttle)'
        elif method == 'cloudwatch':
            return 'TESTED', 'Monitored via CloudWatch metrics'
        elif method == 'cloudwatch_api':
            return 'TESTED', 'Monitored via CloudWatch API metrics'
        else:
            return 'TESTED', f'Monitored via {method}'
    
    # Not in our code - determine why
    # Check if it's an API rate limit
    if 'Rate of' in quota_name and 'API requests' in quota_name:
        return 'TESTED', 'Monitored dynamically via api_throttling_monitor.py (fetches all rate limits from Service Quotas)'
    
    # Check if it's a capacity planning/forecasting quota
    if any(keyword in quota_name.lower() for keyword in ['capacity plan', 'forecast', 'staffing', 'schedule', 'shift']):
        return 'NOT TESTED', 'Capacity planning/WFM quota - specialized feature, not core Connect monitoring'
    
    # Check if it's a file size/upload limit
    if any(keyword in quota_name.lower() for keyword in ['file size', 'upload', 'aggregated']):
        return 'NOT TESTED', 'File size/upload limit - static configuration limit, not runtime usage'
    
    # Check if it's workspace related  
    if 'workspace' in quota_name.lower():
        return 'NOT TESTED', 'Workspace quota - new feature with limited adoption'
    
    # Check if it's an agent/supervisor/group limit for WFM
    if any(keyword in quota_name.lower() for keyword in ['agents per', 'supervisors per', 'per staffing']):
        return 'NOT TESTED', 'Workforce management limit - specialized WFM feature'
    
    # Default for capacity quotas
    if 'per instance' in quota_name or adjustability in ['Resource level', 'Account level']:
        return 'NOT TESTED', 'Capacity quota - not yet added (may be new or low-priority)'
    
    # Default
    return 'NOT TESTED', 'Not yet added to monitoring solution'

def create_coverage_report():
    """Create enhanced Excel report with monitoring status."""
    print("="*80)
    print("CREATING QUOTA MONITORING COVERAGE REPORT")
    print("="*80)
    print()
    
    # Load Excel
    if not os.path.exists(EXCEL_PATH):
        print(f"ERROR: Excel file not found: {EXCEL_PATH}")
        sys.exit(1)
    
    df = pd.read_excel(EXCEL_PATH)
    print(f"Loaded {len(df)} quotas from Excel")
    
    # Get list of monitored quotas
    monitored_codes = set(ENHANCED_CONNECT_QUOTA_METRICS.keys())
    print(f"Currently monitoring {len(monitored_codes)} quotas in code")
    print()
    
    # Determine status for each quota
    statuses = []
    reasons = []
    
    for _, row in df.iterrows():
        status, reason = determine_monitoring_status(row)
        statuses.append(status)
        reasons.append(reason)
    
    # Add new columns
    df['Monitoring Status'] = statuses
    df['Monitoring Details'] = reasons
    
    # Reorder columns to put new ones after Quota code
    cols = df.columns.tolist()
    quota_code_idx = cols.index('Quota code')
    new_cols = cols[:quota_code_idx+1] + ['Monitoring Status', 'Monitoring Details'] + cols[quota_code_idx+1:-2]
    df = df[new_cols]
    
    # Save enhanced Excel
    df.to_excel(OUTPUT_PATH, index=False, engine='openpyxl')
    print(f"✅ Enhanced Excel saved to: {OUTPUT_PATH}")
    print()
    
    # Print summary statistics
    tested_count = (df['Monitoring Status'] == 'TESTED').sum()
    not_tested_count = (df['Monitoring Status'] == 'NOT TESTED').sum()
    
    print("="*80)
    print("SUMMARY STATISTICS")
    print("="*80)
    print(f"Total quotas in Excel: {len(df)}")
    print(f"TESTED (monitored): {tested_count} ({tested_count/len(df)*100:.1f}%)")
    print(f"NOT TESTED: {not_tested_count} ({not_tested_count/len(df)*100:.1f}%)")
    print()
    
    # Breakdown of not tested reasons
    print("NOT TESTED BREAKDOWN:")
    not_tested_df = df[df['Monitoring Status'] == 'NOT TESTED']
    reason_counts = not_tested_df['Monitoring Details'].value_counts()
    for reason, count in reason_counts.items():
        print(f"  • {reason}: {count} quotas")
    print()
    
    # Breakdown of tested methods
    print("TESTED BREAKDOWN:")
    tested_df = df[df['Monitoring Status'] == 'TESTED']
    method_counts = tested_df['Monitoring Details'].value_counts()
    for method, count in method_counts.items():
        print(f"  • {method}: {count} quotas")
    print()
    
    print("="*80)
    print("OUTPUT FILE")
    print("="*80)
    print(f"Enhanced Excel with monitoring status:")
    print(f"  {OUTPUT_PATH}")
    print()
    print("This file contains all Excel quotas with two new columns:")
    print("  • Monitoring Status: TESTED or NOT TESTED")
    print("  • Monitoring Details: Explanation of monitoring method or reason not tested")
    print()
    print("="*80)

if __name__ == '__main__':
    create_coverage_report()
