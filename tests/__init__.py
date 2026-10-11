import logging

from neatlogs.core.logger import get_logger

# Unit tests run offline and never call neatlogs.init(), so the SDK warns that spans
# will not be exported. That is intended here; keep the test output clean.
# get_logger() sets its own level on first use, so it has to be created before lowering it.
get_logger().setLevel(logging.ERROR)
