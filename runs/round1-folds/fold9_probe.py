"""The FOLD-9 import probe, as a script file (inline -c is blocked unattended).

Equivalent to: /tmp/fold-sem/bin/python -c 'import qdrant_client; print("ok")'
"""

from importlib.metadata import version

import numpy

import qdrant_client

print("ok")
print(f"numpy={numpy.__version__} qdrant_client={version('qdrant-client')}")
