"""Shared random experiment sampler for the random condition (SPEC.md section 7).

Uniform over experiment type, then uniform over that type's allowed parameters. The stream
for a world depends only on SAMPLER_SEED and the world's seed, so the scripted-random
reference and the agent random condition run exactly the same experiments on a world.

    python -m eval.sampler --seed 1000 > specs.json
"""

from __future__ import annotations

import argparse
import json

import numpy as np

from schemas import DropSpec, LaunchSpec, WeighSpec
from tools.defaults import RANGES, SAMPLES

SAMPLER_SEED = 20261004
REF_IDS = [s.sample_id for s in SAMPLES if s.launchable]
ALL_IDS = [s.sample_id for s in SAMPLES]


def random_spec(rng: np.random.Generator):
    kind = rng.choice(["weigh", "drop", "launch"])
    if kind == "weigh":
        return WeighSpec(sample_id=str(rng.choice(ALL_IDS)),
                         height_m=float(rng.uniform(*RANGES["weigh"]["height_m"])))
    if kind == "drop":
        return DropSpec(sample_id=str(rng.choice(REF_IDS)),
                        height_m=float(rng.uniform(*RANGES["drop"]["height_m"])))
    return LaunchSpec(sample_id=str(rng.choice(REF_IDS)),
                      speed_mps=float(rng.uniform(*RANGES["launch"]["speed_mps"])),
                      elevation_deg=float(rng.uniform(*RANGES["launch"]["elevation_deg"])))


def random_specs(world_seed: int, budget: int = 12) -> list:
    rng = np.random.default_rng([SAMPLER_SEED, world_seed])
    return [random_spec(rng) for _ in range(budget)]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, required=True)
    ap.add_argument("--budget", type=int, default=12)
    args = ap.parse_args()
    print(json.dumps([s.model_dump() for s in random_specs(args.seed, args.budget)], indent=1))


if __name__ == "__main__":
    main()
