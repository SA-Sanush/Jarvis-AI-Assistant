"""
JARVIS Brain patch — adds _last_provider / _last_model tracking.
Applied automatically in _chat_with_fallback.
"""
# This patch is already integrated into brain.py below.
# The Brain._chat_with_fallback method sets self._last_provider and self._last_model
# after a successful response so the server.py can report it to the UI.
