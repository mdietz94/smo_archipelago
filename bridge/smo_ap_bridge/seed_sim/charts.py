"""Matplotlib chart renderers for SimResult lists.

All renderers use the Agg backend (no display) and write a PNG to the supplied
output directory. The public entry is `render_all(results, out_dir)`; individual
chart functions can also be called directly for ad-hoc analysis.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

from .sim import SimResult


def render_all(results: list[SimResult], out_dir: Path) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    written.append(render_reachable_over_time(results, out_dir))
    written.append(render_kingdom_dwell(results, out_dir))
    written.append(render_bk_heatmap(results, out_dir))
    written.append(render_completion_when_leaving(results, out_dir))
    written.append(render_coplayer_gap_hist(results, out_dir))
    return written


def render_reachable_over_time(results: list[SimResult], out_dir: Path) -> Path:
    fig, ax = plt.subplots(figsize=(10, 5))
    all_xs: list[float] = []
    all_ys: list[list[int]] = []
    for r in results:
        if not r.reachable_timeline:
            continue
        xs = [t / 60 for t, _ in r.reachable_timeline]
        ys = [c for _, c in r.reachable_timeline]
        ax.plot(xs, ys, color="#1f77b4", alpha=0.15, linewidth=1)
        all_xs.append(xs[-1])
        all_ys.append(ys)
    if all_ys:
        max_len = max(len(y) for y in all_ys)
        padded = np.full((len(all_ys), max_len), np.nan)
        for i, y in enumerate(all_ys):
            padded[i, : len(y)] = y
        median = np.nanmedian(padded, axis=0)
        ax.plot(np.arange(max_len) * 1, median, color="#d62728",
                linewidth=2.5, label="median")
        ax.legend(loc="upper right")
    ax.set_xlabel("simulated minutes")
    ax.set_ylabel("reachable un-checked locations")
    ax.set_title(f"SMO reachable-pool over time ({len(results)} runs)")
    ax.grid(alpha=0.3)
    return _save(fig, out_dir / "reachable_over_time.png")


def render_kingdom_dwell(results: list[SimResult], out_dir: Path) -> Path:
    # One stacked bar per run, kingdom = color.
    if not results:
        return _empty_placeholder(out_dir / "kingdom_dwell.png", "no results")
    all_kingdoms: list[str] = []
    seen: set[str] = set()
    for r in results:
        for k in r.kingdom_visit_order:
            if k not in seen:
                seen.add(k)
                all_kingdoms.append(k)

    fig, ax = plt.subplots(figsize=(max(8, len(results) * 0.2), 6))
    x = np.arange(len(results))
    bottom = np.zeros(len(results))
    cmap = plt.get_cmap("tab20")
    for i, k in enumerate(all_kingdoms):
        heights = np.array([r.kingdom_dwell_sec.get(k, 0.0) / 3600 for r in results])
        ax.bar(x, heights, bottom=bottom, label=k, color=cmap(i % 20))
        bottom += heights
    ax.set_xlabel("run index")
    ax.set_ylabel("hours")
    ax.set_title(f"per-kingdom dwell time ({len(results)} runs)")
    ax.legend(loc="upper right", fontsize=7, ncol=2)
    ax.grid(axis="y", alpha=0.3)
    return _save(fig, out_dir / "kingdom_dwell.png")


def render_bk_heatmap(results: list[SimResult], out_dir: Path) -> Path:
    if not results:
        return _empty_placeholder(out_dir / "bk_heatmap.png", "no results")
    bin_min = 5  # 5-minute bins
    max_t = max((r.final_time_sec for r in results), default=0)
    n_bins = max(1, int(max_t / 60 / bin_min) + 1)

    # cell value = kingdom index (1..N) if in soft-BK at that time, 0 otherwise.
    kingdoms: list[str] = []
    seen: set[str] = set()
    for r in results:
        for _, _, k in r.soft_bk_intervals:
            if k and k not in seen:
                seen.add(k)
                kingdoms.append(k)
    k_idx = {k: i + 1 for i, k in enumerate(kingdoms)}

    grid = np.zeros((len(results), n_bins))
    for ri, r in enumerate(results):
        for start, end, k in r.soft_bk_intervals:
            b0 = int(start / 60 / bin_min)
            b1 = int(end / 60 / bin_min) + 1
            grid[ri, b0:b1] = k_idx.get(k, 0.5) if k else 0.5

    fig, ax = plt.subplots(figsize=(12, max(3, len(results) * 0.15)))
    im = ax.imshow(grid, aspect="auto", cmap="hot_r",
                   extent=[0, n_bins * bin_min, len(results), 0],
                   vmin=0, vmax=max(1, len(kingdoms)))
    ax.set_xlabel("simulated minutes")
    ax.set_ylabel("run index")
    ax.set_title("soft-BK heatmap (color = kingdom being cleaned up)")
    if kingdoms:
        cbar = fig.colorbar(im, ax=ax)
        cbar.set_ticks([0] + [k_idx[k] for k in kingdoms])
        cbar.set_ticklabels(["none"] + kingdoms)
    return _save(fig, out_dir / "bk_heatmap.png")


def render_completion_when_leaving(results: list[SimResult], out_dir: Path) -> Path:
    """For each kingdom, distribution of completion% at the time the player
    first moved on. Headline metric — high boxes = bad pacing."""
    data: dict[str, list[float]] = defaultdict(list)
    for r in results:
        for k, frac in r.completion_at_exit.items():
            data[k].append(frac * 100)
    if not data:
        return _empty_placeholder(out_dir / "completion_when_leaving.png",
                                  "no kingdom exits recorded")
    kingdoms = sorted(data, key=lambda k: -np.median(data[k]))
    fig, ax = plt.subplots(figsize=(10, 5))
    ax.boxplot([data[k] for k in kingdoms], tick_labels=kingdoms,
               orientation="vertical", showfliers=False)
    ax.set_ylabel("% of kingdom's locations checked when player first left")
    ax.set_title("completion at first kingdom exit (lower = healthier pacing)")
    ax.axhline(70, color="red", linestyle="--", linewidth=1, alpha=0.5,
               label="70% (forced-clear threshold)")
    ax.legend()
    ax.grid(axis="y", alpha=0.3)
    plt.setp(ax.get_xticklabels(), rotation=30, ha="right")
    return _save(fig, out_dir / "completion_when_leaving.png")


def render_coplayer_gap_hist(results: list[SimResult], out_dir: Path) -> Path:
    gaps_min: list[float] = []
    for r in results:
        gaps_min.extend(g / 60 for g in r.coplayer_gaps_sec)
    if not gaps_min:
        return _empty_placeholder(out_dir / "coplayer_gap_hist.png",
                                  "no coplayer-sourced items recorded")
    fig, ax = plt.subplots(figsize=(10, 5))
    ax.hist(gaps_min, bins=40, color="#2ca02c", edgecolor="#1f5e1f")
    ax.set_xlabel("minutes between consecutive coplayer-sourced items")
    ax.set_ylabel("count")
    ax.set_title(f"coplayer item-delivery gap (n={len(gaps_min)})")
    p50 = float(np.percentile(gaps_min, 50))
    p95 = float(np.percentile(gaps_min, 95))
    ax.axvline(p50, color="black", linestyle="--", label=f"median {p50:.1f} min")
    ax.axvline(p95, color="red", linestyle="--", label=f"p95 {p95:.1f} min")
    ax.legend()
    ax.grid(axis="y", alpha=0.3)
    return _save(fig, out_dir / "coplayer_gap_hist.png")


def _save(fig, path: Path) -> Path:
    fig.tight_layout()
    fig.savefig(path, dpi=120)
    plt.close(fig)
    return path


def _empty_placeholder(path: Path, msg: str) -> Path:
    fig, ax = plt.subplots(figsize=(6, 3))
    ax.text(0.5, 0.5, msg, ha="center", va="center", fontsize=14)
    ax.axis("off")
    return _save(fig, path)
