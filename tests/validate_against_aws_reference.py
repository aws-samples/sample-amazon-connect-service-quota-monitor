#!/usr/bin/env python3
"""
Validate quota codes against official AWS Service Quotas reference document.

This script compares our configured quotas with the official AWS Connect quotas
from the Service Quotas console to identify:
1. Valid quota codes that exist in AWS
2. Quotas that exist but may not support API retrieval with context
3. Invalid/obsolete quota codes
4. Quotas we should add
"""

import sys
import os
import pandas as pd
from collections import defaultdict

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lambda_function import ENHANCED_CONNECT_QUOTA_METRICS

# Load the reference Excel file
EXCEL_PATH = "/Users/svguru/Documents/Connect Quota_REAL_USE THIS.xlsx"

def load_aws_reference_quotas():
    """Load quota codes from the AWS reference Excel file."""
    try:
        df = pd.read_excel(EXCEL_PATH)
        
        # Extract quota codes from the DataFrame
        # The Quota code column should contain L-codes
        quota_codes = set()
        
        for _, row in df.iterrows():
            quota_code = row.get('Quota code', '').strip()
            if quota_code and quota_code.startswith('L-'):
                quota_name = row.get('Quota name', '').strip()
                adjustability = row.get('Adjustability', '').strip()
                applied_value = str(row.get('Applied account-level quota value', '')).strip()
                
                quota_codes.add(quota_code)
                
        print(f"✅ Loaded {len(quota_codes)} quota codes from AWS reference document")
        return df, quota_codes
        
    except Exception as e:
        print(f"❌ Error loading Excel file: {e}")
        sys.exit(1)

def analyze_quota_differences(df, aws_quota_codes):
    """Analyze differences between our config and AWS reference."""
    
    our_quota_codes = set(ENHANCED_CONNECT_QUOTA_METRICS.keys())
    
    # Remove pseudo quota codes (API rate limits, custom codes)
    our_real_quotas = {
        code for code in our_quota_codes 
        if not code.startswith('L-API-') 
        and code not in ['L-EMAIL-ADDR', 'L-EMAIL-CONCURRENT', 'L-CL-PCAJ', 
                         'L-CL-CAJ', 'L-CL-AIAJ', 'L-CL-PCSJ', 'L-CL-ACSJ']
    }
    
    print("\n" + "="*80)
    print("QUOTA VALIDATION AGAINST AWS REFERENCE DOCUMENT")
    print("="*80)
    
    # 1. Quotas in our config that match AWS
    valid_quotas = our_real_quotas & aws_quota_codes
    print(f"\n✅ VALID QUOTAS (in our config AND in AWS): {len(valid_quotas)}")
    
    # 2. Quotas in our config but NOT in AWS (invalid/obsolete)
    invalid_quotas = our_real_quotas - aws_quota_codes
    print(f"\n❌ INVALID/OBSOLETE QUOTAS (in our config but NOT in AWS): {len(invalid_quotas)}")
    if invalid_quotas:
        print("These quota codes don't exist in AWS and should be removed:")
        for code in sorted(invalid_quotas):
            quota_name = ENHANCED_CONNECT_QUOTA_METRICS[code].get('name', 'Unknown')
            print(f"   • {code}: {quota_name}")
    
    # 3. Quotas in AWS but not in our config (missing)
    missing_quotas = aws_quota_codes - our_real_quotas
    print(f"\n📋 MISSING QUOTAS (in AWS but NOT in our config): {len(missing_quotas)}")
    if missing_quotas:
        print("Consider adding these quotas:")
        for code in sorted(list(missing_quotas)[:20]):  # Show first 20
            row = df[df['Quota code'] == code].iloc[0]
            quota_name = row.get('Quota name', 'Unknown')
            print(f"   • {code}: {quota_name}")
        if len(missing_quotas) > 20:
            print(f"   ... and {len(missing_quotas) - 20} more")
    
    # 4. Analyze valid quotas by adjustability
    print(f"\n" + "="*80)
    print("VALID QUOTAS ANALYSIS BY ADJUSTABILITY")
    print("="*80)
    
    adjustability_counts = defaultdict(list)
    
    for code in valid_quotas:
        row = df[df['Quota code'] == code].iloc[0]
        adjustability = row.get('Adjustability', 'Unknown')
        quota_name = row.get('Quota name', 'Unknown')
        adjustability_counts[adjustability].append((code, quota_name))
    
    for adj_type in sorted(adjustability_counts.keys()):
        quotas = adjustability_counts[adj_type]
        print(f"\n{adj_type}: {len(quotas)} quotas")
        
        if adj_type == "Resource level":
            print("⚠️  WARNING: Resource-level quotas may not support Service Quotas API with context")
            print("   These require instance-specific context that may not work via API")
        elif adj_type == "Not adjustable":
            print("ℹ️  These are fixed limits, not adjustable")
        
        for code, name in sorted(quotas)[:5]:  # Show first 5
            print(f"   • {code}: {name}")
        if len(quotas) > 5:
            print(f"   ... and {len(quotas) - 5} more")
    
    # 5. Identify quotas that need context but are resource-level
    print(f"\n" + "="*80)
    print("QUOTAS WITH POTENTIAL API ACCESS ISSUES")
    print("="*80)
    
    problematic_quotas = []
    
    for code in valid_quotas:
        our_config = ENHANCED_CONNECT_QUOTA_METRICS[code]
        row = df[df['Quota code'] == code].iloc[0]
        adjustability = row.get('Adjustability', '')
        
        # Check if we're trying to use context_required for resource-level quotas
        if our_config.get('context_required', False) and adjustability == "Resource level":
            quota_name = row.get('Quota name', 'Unknown')
            problematic_quotas.append((code, quota_name, adjustability))
    
    if problematic_quotas:
        print(f"\n⚠️  {len(problematic_quotas)} quotas have context_required=True but are Resource level:")
        print("   These may fail with 'NoSuchResourceException' - context not supported")
        for code, name, adj in problematic_quotas[:10]:
            print(f"   • {code}: {name}")
            print(f"     Config: context_required=True, AWS: {adj}")
        if len(problematic_quotas) > 10:
            print(f"   ... and {len(problematic_quotas) - 10} more")
    
    return {
        'valid': valid_quotas,
        'invalid': invalid_quotas,
        'missing': missing_quotas,
        'problematic': problematic_quotas
    }

def generate_recommendations(analysis_results, df):
    """Generate actionable recommendations."""
    
    print(f"\n" + "="*80)
    print("RECOMMENDATIONS")
    print("="*80)
    
    invalid_quotas = analysis_results['invalid']
    problematic_quotas = analysis_results['problematic']
    
    if invalid_quotas:
        print(f"\n1️⃣  REMOVE {len(invalid_quotas)} INVALID QUOTA CODES")
        print("   These don't exist in AWS Service Quotas and cause errors:")
        print("   Action: Delete from ENHANCED_CONNECT_QUOTA_METRICS")
        print()
    
    if problematic_quotas:
        print(f"\n2️⃣  FIX {len(problematic_quotas)} RESOURCE-LEVEL QUOTAS")
        print("   These are valid quota codes but 'context_required=True' doesn't work")
        print("   Resource-level quotas don't support instance context via API")
        print("   ")
        print("   Options:")
        print("   A) Set context_required=False (retrieve account-level default)")
        print("   B) Change method to 'api_count' to count actual resources")
        print("   C) Remove if not monitorable via API")
        print()
    
    print(f"\n3️⃣  SERVICE NAME FIX")
    print("   Change 'connect-campaigns' → 'connectcampaigns' (no hyphen)")
    print()
    
    print(f"\n4️⃣  ACCOUNT-LEVEL VS RESOURCE-LEVEL")
    print("   AWS Console shows:")
    print("   • 'Account level' = Can get via Service Quotas API without context")
    print("   • 'Resource level' = May need different approach (API counting)")
    print("   • 'Not adjustable' = Fixed limits")
    print()

def main():
    """Main execution."""
    print("CONNECT QUOTA VALIDATION AGAINST AWS REFERENCE")
    print("="*80)
    
    # Check if Excel file exists
    if not os.path.exists(EXCEL_PATH):
        print(f"❌ Reference file not found: {EXCEL_PATH}")
        print("   Please ensure the Excel file is in the correct location")
        sys.exit(1)
    
    # Load AWS reference quotas
    df, aws_quota_codes = load_aws_reference_quotas()
    
    # Analyze differences
    analysis_results = analyze_quota_differences(df, aws_quota_codes)
    
    # Generate recommendations
    generate_recommendations(analysis_results, df)
    
    print("\n" + "="*80)
    print("NEXT STEPS:")
    print("="*80)
    print("1. Review the invalid quota codes above")
    print("2. Remove invalid quotas from lambda_function.py")
    print("3. Fix resource-level quotas (remove context_required)")
    print("4. Re-run validation script to confirm fixes")
    print("="*80)

if __name__ == '__main__':
    main()
