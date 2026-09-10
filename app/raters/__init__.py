"""Semantic rating and LLM-assisted extraction adapters."""

from app.raters.fake import FakeClaimExtractor, FakeRater
from app.raters.openai import OpenAIRater

__all__ = ["FakeClaimExtractor", "FakeRater", "OpenAIRater"]
