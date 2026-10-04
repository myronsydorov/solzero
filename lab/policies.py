"""Role permissions, mirrored experiment budget, and mission approval policy."""
import math

from lab.tool_functions import current

ROLE_TOOLS = {
    "pi": {"plan_shot", "commit_mission"},
    "theorist": {"fit_law", "set_laws"},
    "experimentalist": {"predict", "disagreement", "preregister", "record_candidates"},
    "operator": {"weigh", "drop", "launch"},
    "analyst": {"fit_law", "predict", "nominate", "record_verdicts", "coverage"},
}
ROLE_TOOLS["single"] = set().union(*ROLE_TOOLS.values())


def role_policy(role: str, auto_approve: bool = False, min_experiments: int = 0):
    allowed = ROLE_TOOLS[role]

    def check(event: dict) -> dict:
        if event.get("type") != "tool_call":
            return {"result": "ALLOW"}
        name = str(event.get("target", "")).removeprefix("mcp__omnigent__")
        if name not in allowed:
            return {"result": "DENY", "reason": "Tool is not assigned to this role"}
        try:
            session = current()
            session._active()
        except RuntimeError as error:
            return {"result": "DENY", "reason": str(error)}
        if name in {"weigh", "drop", "launch"}:
            if session.budget_left <= 0:
                return {"result": "DENY", "reason": "Experiment budget exhausted"}
            arguments = event.get("data", {}).get("arguments", {})
            sample = next((sample for sample in session.info.samples
                           if sample.sample_id == arguments.get("sample_id")), None)
            if sample is None or (name != "weigh" and not sample.launchable):
                return {"result": "DENY", "reason": "Sample is not permitted for this operation"}
            for field, (lower, upper) in session.info.ranges[name].items():
                value = arguments.get(field)
                if not isinstance(value, (int, float)) or not math.isfinite(value) or not lower <= value <= upper:
                    return {"result": "DENY", "reason": "Experiment setting outside safe envelope"}
        if name == "commit_mission":
            if len(session.results) < min_experiments:
                return {"result": "DENY", "reason": "The host's minimum experiment count has not been reached"}
            from schemas import Commit
            try:
                record = Commit.model_validate(event["data"]["arguments"]["commit"])
                if len(record.shots) != 5 or {shot.target_id for shot in record.shots} != {target.target_id for target in session.info.targets}:
                    raise ValueError("Exactly one shot per target is required")
                for shot in record.shots:
                    for field, (lower, upper) in session.info.ranges["mission"].items():
                        if not lower <= getattr(shot, field) <= upper:
                            raise ValueError("Launcher setting outside safe envelope")
            except (ValueError, KeyError, TypeError) as error:
                return {"result": "DENY", "reason": str(error)}
            return {"result": "ALLOW" if auto_approve else "ASK",
                    "reason": "Host dev/batch approval" if auto_approve else "Human approval is required for this five-shot firing table"}
        return {"result": "ALLOW"}

    return check
