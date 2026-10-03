# Development world API

Start with Python 3.12 after `schemas/` is available:

```sh
.venv/bin/python -m mock.server --seed 1000 --port 8000
```

The opaque default world identifier is `mock-dev`. There is one in-memory world
per server process; all conditions receive identical starting metadata and seeded
noise. Restarting loses server state. Use a new runs directory on each restart.
This is a software-development fixture, not calibration or evaluation evidence.

The mock implements the six agent-facing routes from SPEC section 5.2 and imports
all record/request/response models from `schemas`. It uses closed-form Earth
ballistics without air resistance, instrument noise and launcher actuation noise.
A target crossing is the descending crossing of the target's horizontal plane.
Angles arrive in degrees and are converted to radians for computation.

The server counts twelve experiments atomically, including instrument failures.
It rejects missing, mismatched, reused, stale and cross-session prediction tables,
invalid ranges, duplicate law predictions and forbidden sample operations. Random
sessions may execute without a prediction table. Empty predictions are allowed
only before a law has been proposed, before the first experiment. Commit requires
exactly five distinct known targets and safe launcher settings, and is final.

`SOLZERO_ADMIN_TOKEN` enables bearer-token-protected admin routes. Without a token,
both are disabled. Admin responses identify themselves as mock data. Mission
outcomes are computed once on commit, retained on the server, and never returned
to agents. Nominations are scored on forty deterministic dev probe launches (twenty within and twenty beyond the tested speed interval)
using the shared `tools.predict` function when installed. Missing analysis tools
or a numerical failure are explicitly recorded as an unavailable score; no score
is fabricated. Installing schemas alone is sufficient for the HTTP fixture.

`POST /laws` replaces up to four live laws and invalidates existing tables.
Prediction IDs must cover exactly that set. Duplicate, missing and extra IDs are
rejected. Targets use the shared `Target.hit_radius_m` calculation and launcher error
comes from shared instrument defaults. No parallel record models are defined
in this lane.

```sh
.venv/bin/python -m pytest mock/tests tests/test_no_leak.py -q
.venv/bin/python -m mock.check --seed 1000 --output runs/mock-check-new
```

The latter command launches a real HTTP process and saves raw evidence. It refuses
to overwrite previous output. Tests cover response records, range and sample
rejection, table ownership/reuse, concurrent budget exhaustion, failed measurements,
commit finality, admin authentication, wrapper routing, policy decisions, seeded
analysis forwarding, and concurrent ledger writes.
