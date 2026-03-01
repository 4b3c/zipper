class BreakLoop(Exception):
    """Raised by a tool to stop the LLM loop immediately without another API call."""
    pass
