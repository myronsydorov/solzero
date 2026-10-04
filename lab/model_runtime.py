"""Provider error classification and per-response accounting for the pinned SDK."""
from omnigent import ClaudeSDKExecutor


class ModelFailure(RuntimeError):
    pass


def limit_kind(message: str) -> str | None:
    text = message.lower()
    if any(term in text for term in ("session limit", "daily limit", "quota exceeded", "insufficient_quota")):
        return "quota"
    if any(term in text for term in ("429", "rate limit", "rate_limit", "overloaded", "529", "503", "timed out", "timeout")):
        return "transient"
    return None


def backoff_seconds(attempt: int, rng) -> float:
    """Full jitter, exponential ceiling, bounded at sixty seconds."""
    return rng.uniform(0, min(60, 2 ** (attempt + 1)))


class ObservedExecutor(ClaudeSDKExecutor):
    """Observe SDK response boundaries without adding tools or changing routing."""

    def __init__(self, *, observer, **kwargs):
        super().__init__(**kwargs)
        self.observer = observer

    async def _get_or_create_client(self, *args, **kwargs):
        client = await super()._get_or_create_client(*args, **kwargs)
        if getattr(client, "_lab_observed", False):
            return client
        original = client.receive_response

        async def receive_response():
            async for message in original():
                event = getattr(message, "event", None)
                if isinstance(event, dict) and event.get("type") in {"message_start", "message_delta", "message_stop"}:
                    self.observer(event)
                yield message

        client.receive_response = receive_response
        client._lab_observed = True
        return client
