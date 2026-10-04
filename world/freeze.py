"""Test-seed freeze (SPEC.md section 7). Run by the human only, once:

    python -m world.freeze --entropy "<string chosen at the freeze>" --confirm-human-freeze

Selects --per-family seeds from 9000-9999 for each family (family = seed % 4, as in
world/generator.py) and writes them to world/test_seeds.lock. It only picks seed numbers:
it never generates, runs or inspects a world.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import sys
from pathlib import Path

import numpy as np

from .generator import FAMILIES, TEST_SEEDS

LOCK = Path(__file__).with_name("test_seeds.lock")


def choose_seeds(entropy: str, seed_range, per_family: int) -> list[int]:
    """Pure selection: per_family seeds from each family residue class of seed_range,
    drawn with an rng seeded from sha256(entropy). Sorted, no duplicates."""
    rng = np.random.default_rng(int(hashlib.sha256(entropy.encode()).hexdigest(), 16) % 2**63)
    pool = list(seed_range)
    chosen = []
    for k in range(len(FAMILIES)):
        cls = [s for s in pool if s % len(FAMILIES) == k]
        if len(cls) < per_family:
            raise ValueError(f"family {FAMILIES[k]} has only {len(cls)} seeds in range")
        chosen += [int(s) for s in rng.choice(cls, size=per_family, replace=False)]
    return sorted(chosen)


def write_lock(entropy: str, per_family: int, out: Path, confirm: bool, seed_range=TEST_SEEDS) -> str:
    """Write the lock file; returns its sha256. Refuses without confirmation or over an
    existing file."""
    if not confirm:
        raise PermissionError("the freeze is the human's call: pass --confirm-human-freeze")
    out = Path(out)
    if out.exists():
        raise FileExistsError(f"{out} already exists; the freeze happens once")
    seeds = choose_seeds(entropy, seed_range, per_family)
    header = [
        "# Sol Zero test seeds. Frozen by the human; do not edit.",
        f"# entropy_sha256 {hashlib.sha256(entropy.encode()).hexdigest()}",
        f"# date {dt.datetime.now(dt.timezone.utc).isoformat(timespec='seconds')}",
        f"# per_family {per_family} (family = seed % {len(FAMILIES)})",
    ]
    text = "\n".join(header + [str(s) for s in seeds]) + "\n"
    out.write_text(text)
    return hashlib.sha256(text.encode()).hexdigest()


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--entropy", required=True, help="a string chosen by the human at the freeze")
    ap.add_argument("--per-family", type=int, default=10)
    ap.add_argument("--out", default=str(LOCK))
    ap.add_argument("--confirm-human-freeze", action="store_true")
    args = ap.parse_args(argv)
    try:
        digest = write_lock(args.entropy, args.per_family, Path(args.out), args.confirm_human_freeze)
    except (PermissionError, FileExistsError) as exc:
        sys.exit(str(exc))
    print(f"wrote {args.out}  sha256 {digest}")


if __name__ == "__main__":
    main()
