"""Plot diagnostic priority and cumulative CPU service; these are not benchmarks."""

import csv

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

from xv6 import ROOT


def main():
    directory = ROOT / "results/diagnostics"
    fig, axes = plt.subplots(2, 1, figsize=(9, 6), sharex=True, layout="constrained")
    for boost, color in [(0, "#607d8b"), (64, "#1976d2")]:
        with (directory / f"trace-{boost}.csv").open() as file:
            rows = list(csv.DictReader(file))
        ticks = [int(r["tick"]) for r in rows]
        levels = [int(r["level"]) for r in rows]
        cpu = [int(r["cpu"]) for r in rows]
        axes[0].step(ticks, levels, where="post", label=f"boost={boost}", color=color)
        axes[1].step(ticks, cpu, where="post", label=f"boost={boost}", color=color)
    axes[0].set_yticks(range(4), ["Q0 (highest)", "Q1", "Q2", "Q3 (lowest)"])
    axes[0].invert_yaxis()
    axes[0].set_ylabel("Priority")
    axes[0].legend()
    axes[1].set_ylabel("Cumulative CPU ticks")
    axes[1].set_xlabel("Guest tick since boot (approximately 100 ms per tick)")
    for ax in axes:
        ax.spines[["top", "right"]].set_visible(False)
        ax.grid(alpha=0.2)
    axes[0].set_title("One CPU-bound process: demotion and global priority recovery")
    fig.savefig(directory / "priority.png", dpi=180)
    fig.savefig(directory / "priority.pdf")


if __name__ == "__main__":
    main()
