import numpy
import qdrant_client

print("numpy=" + numpy.__version__)
print("qdrant_client=" + qdrant_client.__version__ if hasattr(qdrant_client, "__version__") else "qdrant_client=imported")
print("PROBE_OK")
