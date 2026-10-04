"""Fetch the Franka Panda model from MuJoCo Menagerie (Apache-2.0) into sim/assets/.

    .venv/bin/python -m sim.fetch_assets [--from <dir with franka_emika_panda/ or its parent>]

The assets are 34 MB of meshes, so they are fetched at a pinned commit, not committed.
--from copies an existing copy instead (another worktree, or a machine without GitHub access).
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import tempfile
from pathlib import Path

REPO = "https://github.com/google-deepmind/mujoco_menagerie.git"
COMMIT = "4d038b3feae26ec82b46a4d586379114012a8ac7"
DEST = Path(__file__).with_name("assets") / "franka_emika_panda"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--from", dest="src", type=Path, help="copy from a local franka_emika_panda directory")
    args = ap.parse_args()
    if (DEST / "panda.xml").exists():
        print(f"{DEST} already present")
        return
    if args.src:
        src = args.src if (args.src / "panda.xml").exists() else args.src / "franka_emika_panda"
        if not (src / "panda.xml").exists():
            raise SystemExit(f"no panda.xml under {args.src}")
        DEST.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(src, DEST)
        print(f"copied {src} into {DEST}")
        return
    with tempfile.TemporaryDirectory() as tmp:
        run = lambda *a: subprocess.run(a, cwd=tmp, check=True)
        run("git", "init", "-q", "menagerie")
        cwd = Path(tmp) / "menagerie"
        git = lambda *a: subprocess.run(("git", *a), cwd=cwd, check=True)
        git("remote", "add", "origin", REPO)
        git("sparse-checkout", "set", "franka_emika_panda")
        git("fetch", "-q", "--depth", "1", "--filter=blob:none", "origin", COMMIT)
        git("checkout", "-q", "FETCH_HEAD")
        DEST.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(cwd / "franka_emika_panda", DEST)
    print(f"fetched Menagerie {COMMIT[:8]} franka_emika_panda into {DEST}")


if __name__ == "__main__":
    main()
