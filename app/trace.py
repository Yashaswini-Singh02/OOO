"""Tracing setup. Import this before anything else in every entry point:
neatlogs patches langgraph/langchain/openai at import time, so libraries
imported before init() are not traced.
"""
import os

from dotenv import load_dotenv

load_dotenv()

import neatlogs

neatlogs.init(
    api_key=os.environ["NEATLOGS_API_KEY"],
    workflow_name="outofoffice",
    # Only the model client is auto-instrumented. With "langgraph"/"langchain" on as well,
    # every tool, guardrail and model call inside a node became its own separate trace
    # (22 traces for one run): neatlogs keeps those instrumentors' spans apart from its own,
    # so nothing inside a node could find the run it belonged to. The graph's nodes are
    # traced by our own spans instead (app/pipeline.py), which keeps one run in one trace.
    instrumentations=["openai"],
    # OO_TAG labels a batch of runs, e.g. phase:before, so two batches can be compared
    tags=["env:hackathon"] + ([os.environ["OO_TAG"]] if os.getenv("OO_TAG") else []),
)
