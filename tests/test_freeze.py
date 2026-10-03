"""world.freeze, exercised on the dev range only: no test ever selects from 9000-9999."""

import hashlib

import pytest

from world.freeze import choose_seeds, write_lock
from world.generator import DEV_SEEDS
from world.server import read_seed_lock

DEV = range(1000, 2000)


def test_choose_seeds_balanced_deterministic_unique():
    a = choose_seeds("entropy-a", DEV, 10)
    assert len(a) == 40 and len(set(a)) == 40
    assert all(s in DEV for s in a)
    assert sorted(sum(s % 4 == k for s in a) for k in range(4)) == [10, 10, 10, 10]
    assert a == choose_seeds("entropy-a", DEV, 10)
    assert a != choose_seeds("entropy-b", DEV, 10)


def test_write_lock_guards(tmp_path):
    out = tmp_path / "seeds.lock"
    with pytest.raises(PermissionError):
        write_lock("e", 10, out, confirm=False, seed_range=DEV_SEEDS)
    assert not out.exists()
    digest = write_lock("e", 10, out, confirm=True, seed_range=DEV_SEEDS)
    assert digest == hashlib.sha256(out.read_bytes()).hexdigest()
    seeds = read_seed_lock(out)  # the server's loader skips '#' header lines
    assert seeds == choose_seeds("e", DEV_SEEDS, 10)
    with pytest.raises(FileExistsError):
        write_lock("e", 10, out, confirm=True, seed_range=DEV_SEEDS)

