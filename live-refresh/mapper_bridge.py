"""Bridge module for importing connect-resource-mapper.py (which has a hyphen in its name).

Lambda cannot import hyphenated modules directly. This bridge provides
clean access to the mapper's functions without sys.path manipulation.
"""

import importlib.util
from pathlib import Path

# In the Lambda package the mapper is bundled alongside this file (flat
# /var/task), so resolve it in the same directory. Falls back to the repo-root
# layout (one level up) for local/CLI use where the files are not co-located.
_SAME_DIR = Path(__file__).parent / "connect-resource-mapper.py"
_REPO_ROOT = Path(__file__).parent.parent / "connect-resource-mapper.py"
_MAPPER_PATH = _SAME_DIR if _SAME_DIR.exists() else _REPO_ROOT

_spec = importlib.util.spec_from_file_location("connect_resource_mapper", _MAPPER_PATH)
_module = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_module)

# Export the functions Lambda needs
collect_all = _module.collect_all
