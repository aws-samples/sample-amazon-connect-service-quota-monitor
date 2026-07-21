#!/usr/bin/env python3
"""
Apply fixes to lambda_function.py based on validation results.

This script will:
1. Remove 14 invalid quota codes that don't exist in AWS
2. Fix 29 resource-level quotas by setting context_required=False
3. Fix service name from 'connect-campaigns' to 'connectcampaigns'
"""

import re
import sys
import os

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

LAMBDA_FUNCTION_PATH = "lambda_function.py"

# Invalid quotas to remove (don't exist in AWS)
INVALID_QUOTAS_TO_REMOVE = [
    # Customer Profiles quotas (wrong service)
    'L-6603B252',  # Customer Profiles domains per account
    'L-A7ED412C',  # Keys per object type
    'L-3217D1F1',  # Maximum expiration in days
    'L-0A1E1791',  # Event triggers per domain
    'L-4A5ECB8E',  # Integrations per domain
    'L-DFAEAED3',  # Profile history records per profile
    'L-B6E9F054',  # Recommenders per domain
    'L-B59352A0',  # Segment snapshots per day
    'L-63975AF3',  # Size of all objects for a profile (KB)
    'L-14092FF4',  # Object types per domain
    'L-E17DC7C3',  # Objects per profile
    # Lex V2 quotas (don't exist in Connect service)
    'L-36FA8BD2',  # Bots per account (Lex V2)
    'L-ED50DA7C',  # Sample utterances per intent (Lex V2)
    'L-77D6C60C',  # Sample utterances per slot (Lex V2)
]

# Resource-level quotas that need context_required=False
QUOTAS_TO_FIX_CONTEXT = [
    'L-986AE5E3',  # Scheduled reports per instance
    'L-19755C7E',  # Flow modules per instance
    'L-6402A996',  # Workspaces per instance
    'L-D68AAAE4',  # User hierarchy groups per instance
    'L-D4BA6F6E',  # Concurrent active chats per instance
    'L-F4C86B27',  # Email addresses per instance
    'L-20CD02F7',  # Hours of operation per instance
    'L-22922690',  # Contact flows per instance
    'L-0865B754',  # Prompts per instance
    'L-E3D2F503',  # AWS Lambda functions per instance
    'L-FC6A5030',  # Application integration associations per instance
    'L-B93A6612',  # Amazon Lex bots per instance
    'L-68BBE2E8',  # Quick connects per instance
    'L-19A87C94',  # Queues per instance
    'L-F325A715',  # Security profiles per instance
    'L-9A46857E',  # Users per instance
    'L-D3E7BE26',  # Routing profiles per instance
    'L-79564E52',  # Reports per instance
    'L-DA88F710',  # Maximum active recording sessions
    'L-516BC0EB',  # Queues per routing profile per instance
    'L-790F20B4',  # Event integration associations per instance
    'L-CCEA7427',  # Amazon Lex V2 bot aliases per instance
    'L-C7548958',  # Amazon Pinpoint application integration associations
    'L-0AA82C05',  # Cases domain integration associations per instance
    'L-FFE16A0F',  # Connect AI agent assistant integration associations
    'L-2D7CA70C',  # Connect AI agent knowledge base integration associations
    'L-D55E707F',  # Connect AI agent message templates integration associations
    'L-C8F22860',  # Connect AI agent quick responses integration associations
    'L-02421311',  # File scanner integration associations per instance
    'L-50375162',  # Proficiencies per agent
    'L-60553137',  # Concurrent active tasks per instance
    'L-E908C3A1',  # Concurrent campaign active calls per instance
    'L-B117F12F',  # Concurrent active emails per instance
    'L-12AB7C57',  # Concurrent active calls per instance (but this one actually worked!)
    'L-8F812903',  # Phone numbers per instance
    'L-3828FBF0',  # Predefined Attributes
]

def read_file(filepath):
    """Read file contents."""
    with open(filepath, 'r') as f:
        return f.read()

def write_file(filepath, content):
    """Write content to file."""
    with open(filepath, 'w') as f:
        f.write(content)

def backup_file(filepath):
    """Create backup of the file."""
    backup_path = f"{filepath}.backup"
    content = read_file(filepath)
    write_file(backup_path, content)
    print(f"✅ Created backup: {backup_path}")
    return backup_path

def remove_quota_definition(content, quota_code):
    """Remove a complete quota definition from the file."""
    # Pattern to match the entire quota definition including all its configuration
    # This matches from the quota code line through the closing brace and comma
    pattern = rf"    '{quota_code}':\s*\{{[^}}]*?\n    \}},?\n"
    
    # Try to find and remove the quota
    matches = list(re.finditer(pattern, content, re.MULTILINE | re.DOTALL))
    
    if matches:
        # Remove the quota definition
        content = re.sub(pattern, '', content, flags=re.MULTILINE | re.DOTALL)
        print(f"✅ Removed quota: {quota_code}")
        return content, True
    else:
        print(f"⚠️  Could not find quota: {quota_code}")
        return content, False

def fix_context_required(content, quota_code):
    """Fix context_required value for a quota."""
    # Pattern to find context_required: True within this quota's definition
    # We need to be more specific to ensure we're in the right quota block
    
    # First, find the quota block
    quota_pattern = rf"('{quota_code}':\s*\{{[^}}]*?)'context_required':\s*True([^}}]*?\n    \}})"
    
    matches = list(re.finditer(quota_pattern, content, re.MULTILINE | re.DOTALL))
    
    if matches:
        # Replace context_required: True with False
        content = re.sub(
            quota_pattern,
            r"\1'context_required': False\2",
            content,
            flags=re.MULTILINE | re.DOTALL
        )
        print(f"✅ Fixed context_required for: {quota_code}")
        return content, True
    else:
        # Check if it already has False or doesn't exist
        if f"'{quota_code}'" in content:
            print(f"ℹ️  Quota {quota_code} already has context_required=False or not found")
        else:
            print(f"⚠️  Could not find quota: {quota_code}")
        return content, False

def fix_service_name(content):
    """Fix service name from 'connect-campaigns' to 'connectcampaigns'."""
    # Count occurrences
    count = content.count("'connect-campaigns'")
    
    if count > 0:
        content = content.replace("'connect-campaigns'", "'connectcampaigns'")
        print(f"✅ Fixed service name: 'connect-campaigns' → 'connectcampaigns' ({count} occurrences)")
        return content, True
    else:
        print(f"ℹ️  No occurrences of 'connect-campaigns' found")
        return content, False

def main():
    """Main execution."""
    print("="*80)
    print("APPLYING QUOTA FIXES TO lambda_function.py")
    print("="*80)
    print()
    
    # Check if file exists
    if not os.path.exists(LAMBDA_FUNCTION_PATH):
        print(f"❌ Error: {LAMBDA_FUNCTION_PATH} not found")
        sys.exit(1)
    
    # Create backup
    backup_path = backup_file(LAMBDA_FUNCTION_PATH)
    
    # Read original content
    content = read_file(LAMBDA_FUNCTION_PATH)
    original_size = len(content)
    
    print()
    print("="*80)
    print("STEP 1: REMOVING INVALID QUOTAS")
    print("="*80)
    print()
    
    removed_count = 0
    for quota_code in INVALID_QUOTAS_TO_REMOVE:
        content, success = remove_quota_definition(content, quota_code)
        if success:
            removed_count += 1
    
    print()
    print(f"Summary: Removed {removed_count} out of {len(INVALID_QUOTAS_TO_REMOVE)} invalid quotas")
    
    print()
    print("="*80)
    print("STEP 2: FIXING CONTEXT_REQUIRED FOR RESOURCE-LEVEL QUOTAS")
    print("="*80)
    print()
    
    fixed_context_count = 0
    for quota_code in QUOTAS_TO_FIX_CONTEXT:
        content, success = fix_context_required(content, quota_code)
        if success:
            fixed_context_count += 1
    
    print()
    print(f"Summary: Fixed context_required for {fixed_context_count} out of {len(QUOTAS_TO_FIX_CONTEXT)} quotas")
    
    print()
    print("="*80)
    print("STEP 3: FIXING SERVICE NAME")
    print("="*80)
    print()
    
    content, service_fixed = fix_service_name(content)
    
    # Write updated content
    write_file(LAMBDA_FUNCTION_PATH, content)
    
    new_size = len(content)
    size_reduction = original_size - new_size
    
    print()
    print("="*80)
    print("FIX APPLICATION SUMMARY")
    print("="*80)
    print(f"✅ Removed invalid quotas: {removed_count}/{len(INVALID_QUOTAS_TO_REMOVE)}")
    print(f"✅ Fixed context_required: {fixed_context_count}/{len(QUOTAS_TO_FIX_CONTEXT)}")
    print(f"✅ Fixed service name: {'Yes' if service_fixed else 'No'}")
    print(f"")
    print(f"File size: {original_size:,} → {new_size:,} bytes ({size_reduction:,} bytes removed)")
    print(f"Backup saved: {backup_path}")
    print(f"")
    print("="*80)
    print()
    
    if removed_count + fixed_context_count > 0:
        print("✅ Fixes applied successfully!")
        print()
        print("NEXT STEPS:")
        print("1. Review the changes in lambda_function.py")
        print("2. Run the validation script again to confirm fixes")
        print("3. Test the Lambda function")
        print()
        print("To revert changes if needed:")
        print(f"  cp {backup_path} {LAMBDA_FUNCTION_PATH}")
    else:
        print("⚠️  No changes were made. The file may already be fixed or patterns don't match.")
        print("   You may need to manually review the file.")
    
    print()
    print("="*80)

if __name__ == '__main__':
    main()
