"""Records from SPEC.md section 5.3. SI units; angles in degrees."""

from __future__ import annotations

from typing import Annotated, Any, Literal, Union

import sympy
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, field_validator, model_validator

# Variables a law expression may use, besides its own named parameters (SPEC 5.1).
LAW_VARIABLES = ("m", "z", "vx", "vz", "speed")


class Record(BaseModel):
    model_config = ConfigDict(extra="forbid")


# --- Experiment specs -------------------------------------------------------


class WeighSpec(Record):
    type: Literal["weigh"] = "weigh"
    sample_id: str
    height_m: float


class DropSpec(Record):
    type: Literal["drop"] = "drop"
    sample_id: str
    height_m: float


class LaunchSpec(Record):
    type: Literal["launch"] = "launch"
    sample_id: str
    speed_mps: float
    elevation_deg: float


ExperimentSpec = Annotated[Union[WeighSpec, DropSpec, LaunchSpec], Field(discriminator="type")]
_spec_adapter: TypeAdapter = TypeAdapter(ExperimentSpec)


def parse_spec(data: dict[str, Any] | str) -> WeighSpec | DropSpec | LaunchSpec:
    """Parse an ExperimentSpec from a dict or a JSON string."""
    if isinstance(data, str):
        return _spec_adapter.validate_json(data)
    return _spec_adapter.validate_python(data)


# Observable names per experiment type.
OBSERVABLES = {
    "weigh": ("force_n",),
    "drop": ("fall_time_s",),
    "launch": ("landing_x_m", "flight_time_s"),
}


# --- Results and session ----------------------------------------------------


class Result(Record):
    experiment_id: str
    index: int
    spec: ExperimentSpec
    observables: dict[str, float]
    noise_sd: dict[str, float]
    status: Literal["ok", "failed"] = "ok"
    budget_left: int


class Sample(Record):
    sample_id: str
    mass_kg: float
    launchable: bool


# A shot hits when it comes down within max(5 cm, 2% of the target's horizontal distance
# from the launcher). The world server sets hit_radius_m on every target it publishes.
HIT_RADIUS_MIN_M = 0.05
HIT_RADIUS_FRAC = 0.02


def hit_radius(x_m: float, frac: float = HIT_RADIUS_FRAC, launcher_x_m: float = 0.0) -> float:
    return max(HIT_RADIUS_MIN_M, frac * abs(x_m - launcher_x_m))


class Target(Record):
    target_id: str
    x_m: float
    z_m: float
    hit_radius_m: float | None = None  # filled from hit_radius(x_m) when omitted

    @model_validator(mode="after")
    def _radius(self) -> Target:
        if self.hit_radius_m is None:
            self.hit_radius_m = hit_radius(self.x_m)
        return self


class Launcher(Record):
    x_m: float = 0.0
    z_m: float = 0.2


class ShotZero(Record):
    spec: ExperimentSpec
    target_id: str
    landing_x_m: float
    miss_m: float


class SessionInfo(Record):
    session_id: str
    budget: int
    samples: list[Sample]
    ranges: dict[str, dict[str, tuple[float, float]]]
    noise_sd: dict[str, float]
    launcher: Launcher
    targets: list[Target]
    shot_zero: ShotZero


# --- Laws and fits ------------------------------------------------------------


class ParamSpec(Record):
    init: float
    lo: float
    hi: float

    @model_validator(mode="after")
    def _ordered(self) -> ParamSpec:
        if not (self.lo <= self.init <= self.hi):
            raise ValueError(f"need lo <= init <= hi, got {self.lo}, {self.init}, {self.hi}")
        return self


def law_expr_symbols(expr: str, param_names: list[str] | tuple[str, ...] = ()) -> set[str]:
    """Parse a law expression and return the names of its free symbols."""
    names = list(LAW_VARIABLES) + list(param_names)
    local = {n: sympy.Symbol(n) for n in names}
    parsed = sympy.parse_expr(expr, local_dict=local, evaluate=True)
    return {str(s) for s in parsed.free_symbols}


class Law(Record):
    law_id: str
    description: str = ""
    ax: str
    az: str
    params: dict[str, ParamSpec] = Field(default_factory=dict)

    @field_validator("params")
    @classmethod
    def _param_names(cls, v: dict[str, ParamSpec]) -> dict[str, ParamSpec]:
        for name in v:
            if name in LAW_VARIABLES or not name.isidentifier():
                raise ValueError(f"invalid parameter name {name!r}")
        return v

    @model_validator(mode="after")
    def _expressions(self) -> Law:
        allowed = set(LAW_VARIABLES) | set(self.params)
        for field in ("ax", "az"):
            try:
                used = law_expr_symbols(getattr(self, field), list(self.params))
            except Exception as exc:  # sympy raises many types
                raise ValueError(f"{field} is not a valid expression: {exc}") from exc
            unknown = used - allowed
            if unknown:
                raise ValueError(f"{field} uses unknown names {sorted(unknown)}")
        return self


class ParamEstimate(Record):
    value: float
    sd: float


class FitResult(Record):
    law_id: str
    params: dict[str, ParamEstimate]
    chi2_dof: float
    loo_error: float | None = None  # RMS leave-one-experiment-out error, in noise sd units
    n_experiments: int
    converged: bool
    # Parameter covariance, rows and columns in `params` order. Optional; predict uses it
    # to draw correlated parameters and falls back to independent sds without it.
    cov: list[list[float]] | None = None


class ObservableEstimate(Record):
    mean: float
    sd: float


class Prediction(Record):
    law_id: str
    observables: dict[str, ObservableEstimate]


class DisagreementPair(Record):
    a: str
    b: str
    gap_sigma: float


class Disagreement(Record):
    spec: ExperimentSpec
    pairs: list[DisagreementPair]
    max_gap_sigma: float


class ShotPlan(Record):
    law_id: str
    target_id: str
    sample_id: str
    speed_mps: float
    elevation_deg: float
    predicted_miss_sd_m: float
    reachable: bool


class Verdict(Record):
    law_id: str
    experiment_id: str
    z: float
    verdict: Literal["supported", "rejected", "insufficient_evidence"]
    note: str = ""


class Shot(Record):
    target_id: str
    speed_mps: float
    elevation_deg: float


Claim = Literal["law_identified", "predictive_only", "insufficient_evidence"]


class Commit(Record):
    law_id: str
    claim: Claim
    claims_non_ordinary: bool
    shots: list[Shot]


# --- Ledger (SPEC 5.5) --------------------------------------------------------


class DecisionDiff(Record):
    tentative: ExperimentSpec | None
    actual: ExperimentSpec
    changed: bool
    reason: str = ""


LedgerKind = Literal[
    "law_set", "candidates", "prediction_table", "result",
    "verdicts", "decision_diff", "nomination", "commit",
]


class LedgerEntry(Record):
    ts: str
    cycle: int
    agent: str
    kind: LedgerKind
    payload: dict[str, Any]
