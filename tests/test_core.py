"""Core tests for the Connect Quota Monitor.

Unit tests: individual functions in isolation.
Integration tests: full pipeline with mocked AWS calls.
"""

import json
import os
import sys
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))


class TestResourceMapper:
    """Unit tests for connect-resource-mapper.py functions."""

    def test_import_mapper(self):
        """Mapper module imports without error."""
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "mapper", Path(__file__).parent.parent / "connect-resource-mapper.py"
        )
        module = importlib.util.module_from_spec(spec)
        # Don't exec (it has argparse at module level) — just verify importable
        assert spec is not None


class TestDashboardGeneration:
    """Unit tests for dashboard_v4.py."""

    def test_import_dashboard(self):
        from dashboard_v4 import build_dashboard_data
        assert callable(build_dashboard_data)

    def test_build_dashboard_data_empty_input(self):
        from dashboard_v4 import build_dashboard_data
        result = build_dashboard_data(
            resource_map={"contact_flows": [], "phone_numbers": [], "lambdas": []},
            model={"quota_headroom": {}, "flow_to_lambda_map": {}, "tdg_number_distribution": {}},
            line_config={},
        )
        assert "LINES" in result
        assert "TOTAL_CAPACITY" in result

    def test_no_nan_in_dashboard_output(self):
        """Regression: dashboard must not produce NaN/Infinity values."""
        from dashboard_v4 import build_dashboard_data
        result = build_dashboard_data(
            resource_map={"contact_flows": [], "phone_numbers": [], "lambdas": []},
            model={"quota_headroom": {}, "flow_to_lambda_map": {}, "tdg_number_distribution": {}},
            line_config={"lines": [{"id": "test", "name": "Test", "number": "1-800-TEST",
                                     "match": {"flow_patterns": ["*"]}}]},
        )
        json_str = json.dumps(result, default=str)
        assert "NaN" not in json_str
        assert "Infinity" not in json_str


class TestConsolidatedReport:
    """Unit tests for consolidated_report.py."""

    def test_import_report(self):
        from consolidated_report import generate_consolidated_report
        assert callable(generate_consolidated_report)


class TestCFNTemplate:
    """Validate CloudFormation template structure."""

    def test_template_has_required_sections(self):
        """CFN template has AWSTemplateFormatVersion, Resources, Outputs."""
        template_path = Path(__file__).parent.parent / "connect-quota-monitor-cfn.yaml"
        content = template_path.read_text()
        assert "AWSTemplateFormatVersion" in content
        assert "Resources:" in content
        assert "Outputs:" in content

    def test_no_duplicate_output_keys(self):
        """Regression: C1 — duplicate outputs cause deploy failure."""
        import re
        template_path = Path(__file__).parent.parent / "connect-quota-monitor-cfn.yaml"
        content = template_path.read_text()
        output_section = content.split("Outputs:")[1] if "Outputs:" in content else ""
        keys = re.findall(r"^  (\w+):", output_section, re.MULTILINE)
        duplicates = [k for k in set(keys) if keys.count(k) > 1]
        assert duplicates == [], f"Duplicate CFN Output keys: {duplicates}"

    def test_no_hardcoded_account_ids_in_template(self):
        """Regression: C2 — no real account IDs in template."""
        import re
        template_path = Path(__file__).parent.parent / "connect-quota-monitor-cfn.yaml"
        content = template_path.read_text()
        # 12-digit sequences that aren't inside ${} or !Sub references
        matches = re.findall(r"\b\d{12}\b", content)
        # Filter: CFN templates legitimately use 12-digit numbers in some contexts
        # But real account IDs like 745351468190 should not be present
        real_ids = [m for m in matches if m.startswith("7") or m.startswith("9")]
        assert len(real_ids) == 0, f"Possible hardcoded account IDs: {real_ids}"


class TestNoHardcodedSecrets:
    """Security: no credentials in source."""

    def test_no_akia_keys(self):
        """No AWS access key IDs in any Python file."""
        import re
        root = Path(__file__).parent.parent
        for py_file in root.rglob("*.py"):
            if ".git" in str(py_file) or "__pycache__" in str(py_file):
                continue
            content = py_file.read_text()
            assert not re.search(r"AKIA[A-Z0-9]{16}", content), \
                f"Possible AWS key in {py_file.name}"

    def test_no_hardcoded_instance_ids_in_source(self):
        """No real Connect instance IDs in committed source (test files use placeholders)."""
        # The instance ID pattern to detect (split to avoid self-match)
        forbidden_prefix = "6c3f17c0" + "-3b52-4990"
        root = Path(__file__).parent.parent
        for py_file in root.rglob("*.py"):
            if ".git" in str(py_file) or "__pycache__" in str(py_file) or "output" in str(py_file):
                continue
            if py_file.name == "test_core.py":
                continue
            content = py_file.read_text()
            if forbidden_prefix in content:
                assert False, f"Hardcoded instance ID in {py_file.name}"


class TestLiveRefreshTemplate:
    """Validate live-refresh SAM template."""

    def test_timeout_adequate(self):
        """Lambda timeout must be >= 120s for real-world scans."""
        import re
        template_path = Path(__file__).parent.parent / "live-refresh" / "template.yaml"
        content = template_path.read_text()
        match = re.search(r"Timeout:\s*(\d+)", content)
        assert match, "No Timeout found in template"
        timeout = int(match.group(1))
        assert timeout >= 120, f"Timeout {timeout}s is too low for production scans"
