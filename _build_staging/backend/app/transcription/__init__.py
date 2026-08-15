"""Call transcript/recording transcription (Azure AI Speech).

Converts uploaded audio/video call recordings into text so downstream
agents (Requirements Analyst, Discovery Agent, etc.) can analyze them - the
"transcription" module named in the Modules list of
``.github/copilot-instructions.md``. Contains no agent reasoning: it only
turns audio bytes into a transcript string.
"""
from __future__ import annotations
