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
    # "langgraph" alone can trace nothing; it sits on langchain-core, so name all three.
    instrumentations=["langgraph", "langchain", "openai"],
    tags=["env:hackathon"],
)
