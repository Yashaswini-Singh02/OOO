import logging

# Unit tests run offline and never call neatlogs.init(), so the SDK warns that spans
# will not be exported. That is intended here; keep the test output clean.
logging.getLogger("neatlogs").setLevel(logging.ERROR)
