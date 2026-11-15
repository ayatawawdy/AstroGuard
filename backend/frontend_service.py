"""Stable Python imports for Streamlit; delegates to existing Backend services.

Retrieval returns lists of stored row dictionaries. Analysis returns the existing
AI report dictionary unchanged. See backend/README.md for errors and examples.
"""

from backend.services import get_astronauts, get_measurements, get_missions
from backend.ai_service import analyze_astronaut_day, analyze_astronaut_range

__all__ = [
    "get_astronauts", "get_missions", "get_measurements",
    "analyze_astronaut_day", "analyze_astronaut_range",
]
