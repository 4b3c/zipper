"""Mock anthropic client that replays pre-recorded gold conversation turns."""


class _MockFinalMessage:
    def __init__(self, turn: dict):
        self.content = turn["content"]
        self.stop_reason = turn["stop_reason"]


class _MockStream:
    """Async context manager that returns one pre-recorded turn."""

    def __init__(self, turn: dict):
        self._message = _MockFinalMessage(turn)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass

    def __aiter__(self):
        return self

    async def __anext__(self):
        raise StopAsyncIteration

    async def get_final_message(self):
        return self._message


class MockClient:
    """Drop-in replacement for the anthropic AsyncAnthropic client.

    Replays turns from a gold conversation in order. Each call to
    messages.stream() consumes the next turn.
    """

    def __init__(self, turns: list):
        self._turns = list(turns)
        self._index = 0
        self.messages = self

    def stream(self, **kwargs):
        if self._index >= len(self._turns):
            raise RuntimeError(
                f"MockClient: exhausted turns after {self._index} calls "
                f"(gold has {len(self._turns)} turns)"
            )
        turn = self._turns[self._index]
        self._index += 1
        return _MockStream(turn)
