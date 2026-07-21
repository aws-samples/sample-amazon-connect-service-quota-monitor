"""Bridge module for importing connect-resource-mapper.py (which has a hyphen in its name).

Lambda cannot import hyphenated modules directly. This bridge provides
clean access to the mapper's functions without sys.path manipulation.
"""

import importlib.util
from pathlib import Path

_MAPPER_PATH = Path(__file__).parent.parent / "connect-resource-mapper.py"

_spec = importlib.util.spec_from_file_location("connect_resource_mapper", _MAPPER_PATH)
_module = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_module)

# Export the functions Lambda needs
collect_all = _module.collect_all
