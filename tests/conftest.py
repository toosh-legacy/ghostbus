"""Tests never call paid or network LLMs, whatever the developer's .env says."""

import os

os.environ["COPILOT_PROVIDER"] = "offline"
os.environ["OPENAI_API_KEY"] = ""
