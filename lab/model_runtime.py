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
            stream = original()
            try:
                async for message in stream:
                    event = getattr(message, "event", None)
                    if isinstance(event, dict) and event.get("type") in {"message_start", "message_delta", "message_stop"}:
                        self.observer(event)
                    if getattr(message, "subtype", None) == "api_retry":
                        self.observer({"type": "api_retry", "details": dict(message.data)})
                    yield message
            finally:
                await stream.aclose()

        client.receive_response = receive_response
        client._lab_observed = True
        return client


def observed_usage(path):
    """Count visible responses, keeping interrupted usage explicitly incomplete."""
    import json
    if not path.exists():
        return {}
    agents, active = {}, None
    keys = ("input_tokens", "output_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")

    def finish(complete):
        if active is None:
            return
        totals = agents.setdefault(active["agent"], {"calls": 0, "tokens_observed": 0,
                                                    "incomplete_responses": 0, "models": []})
        totals["calls"] += 1
        totals["tokens_observed"] += sum(active["usage"].get(key, 0) for key in keys)
        totals["incomplete_responses"] += not complete
        if active["model"] not in totals["models"]:
            totals["models"].append(active["model"])

    for line in path.read_text().splitlines():
        record = json.loads(line)
        event = record["event"]
        if event["type"] == "message_start":
            finish(False)
            active = {"agent": record["agent"], "model": event["message"].get("model"),
                      "usage": dict(event["message"].get("usage", {}))}
        elif event["type"] == "message_delta" and active is not None:
            active["usage"].update(event.get("usage", {}))
        elif event["type"] == "message_stop":
            finish(True)
            active = None
    finish(False)
    return agents
