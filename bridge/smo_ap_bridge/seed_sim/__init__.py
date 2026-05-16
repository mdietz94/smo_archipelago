"""Monte Carlo seed simulator for the SMO apworld.

Drives `scripts/ap_generate.py` to produce real Archipelago spoilers, then
runs discrete-event timeline simulations to estimate per-kingdom dwell time,
soft-BK ("100%-ing one kingdom while waiting on a coplayer") risk, and
completion-percentage-at-kingdom-exit. Outputs matplotlib PNG charts.

Entry point: `scripts/simulate_seeds.py`.
"""
