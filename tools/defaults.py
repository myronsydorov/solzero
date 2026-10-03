"""Facts from SPEC.md section 3 that every lane may rely on (all visible to agents)."""

from schemas import Launcher, Sample

LAUNCHER = Launcher(x_m=0.0, z_m=0.20)

SAMPLES = [
    Sample(sample_id=f"ref_{g:03d}", mass_kg=g / 1000, launchable=True)
    for g in (20, 50, 100, 200, 400, 800)
] + [Sample(sample_id="mission_300", mass_kg=0.3, launchable=False)]

RANGES = {
    "weigh": {"height_m": (0.0, 1.2)},
    "drop": {"height_m": (0.1, 1.2)},
    "launch": {"speed_mps": (1.0, 4.0), "elevation_deg": (15.0, 75.0)},
    "mission": {"speed_mps": (1.0, 7.0), "elevation_deg": (15.0, 75.0)},
}

# Sensor noise (1 sd) plus launcher actuation error, which applies to every launch.
NOISE_SD = {
    "force_frac": 0.02,
    "fall_time_s": 0.005,
    "landing_x_m": 0.01,
    "flight_time_s": 0.005,
    "speed_frac": 0.02,
    "elevation_deg": 0.5,
}

HIT_RADIUS_M = 0.05
