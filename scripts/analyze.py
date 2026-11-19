"""Summarize independent runs and plot medians with observed min/max ranges."""

import argparse
from collections import defaultdict
import csv
from pathlib import Path
from statistics import median


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    args = parser.parse_args()
    with (args.directory / "samples.csv").open() as file:
        rows = list(csv.DictReader(file))
    runs = defaultdict(list)
    for row in rows:
        runs[row["mode"], row["policy"], row["run"]].append(row)
    metrics = defaultdict(list)
    for (mode, policy, run), records in runs.items():
        # One independent sample per run. Do not pretend workers from the same
        # run are independent experimental repetitions.
        turnaround = [int(r["exit"]) - int(r["release"]) for r in records]
        response = [int(r["response_time"]) / 10000 for r in records]  # ms
        metrics[mode, policy, "mean_turnaround_ticks"].append(sum(turnaround) / len(records))
        metrics[mode, policy, "mean_response_ms"].append(sum(response) / len(records))
        metrics[mode, policy, "dispatches"].append(sum(int(r["dispatches"]) for r in records))
        elapsed = max(int(r["exit"]) for r in records) - min(int(r["release"]) for r in records)
        metrics[mode, policy, "jobs_per_second"].append(len(records) / (elapsed / 10))
        # Last worker is the blocking, short, or pipe-reader job of interest.
        special = max(records, key=lambda r: int(r["worker"]))
        metrics[mode, policy, "last_worker_turnaround_ticks"].append(
            int(special["exit"]) - int(special["release"]))
        if mode in {"mixed", "pipe"}:
            count = int(special["wakeups"])
            if not count:
                raise RuntimeError("Missing blocking-worker wakeup samples")
            metrics[mode, policy, "wake_delay_ms"].append(int(special["wake_delay"]) / count / 10000)
            metrics[mode, policy, "max_wake_delay_ms"].append(int(special["max_wake_delay"]) / 10000)
    summaries = []
    for (mode, policy, metric), values in sorted(metrics.items()):
        summaries.append(dict(mode=mode, policy=policy, metric=metric, runs=len(values),
                              median=median(values), minimum=min(values), maximum=max(values)))
    with (args.directory / "summary.csv").open("w", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=list(summaries[0]))
        writer.writeheader()
        writer.writerows(summaries)
    print(f"Summarized {len(rows)} worker records from {len(runs)} independent runs.")

    # Only plotting needs an extra package; collection uses the standard library.
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    plt.rcParams.update({"font.size": 11, "axes.spines.top": False, "axes.spines.right": False})
    plots = [
        ("mean_turnaround_ticks", "Mean turnaround (guest ticks)", ["cpu", "mixed", "short", "pipe"]),
        ("wake_delay_ms", "Mean wakeup-to-dispatch delay (ms)", ["mixed", "pipe"]),
        ("dispatches", "Total worker dispatches", ["cpu", "mixed", "short", "pipe"]),
        ("last_worker_turnaround_ticks", "Special worker turnaround (guest ticks)", ["mixed", "short", "pipe"]),
    ]
    figure_dir = args.directory / "figures"
    figure_dir.mkdir(exist_ok=True)
    for metric, label, requested_modes in plots:
        modes = [m for m in requested_modes if (m, "DEFAULT", metric) in metrics]
        if not modes:
            continue
        fig, ax = plt.subplots(figsize=(8, 4.7), layout="constrained")
        for offset, policy, color in [(-0.19, "DEFAULT", "#607d8b"), (0.19, "MLFQ", "#1976d2")]:
            values = [metrics[m, policy, metric] for m in modes]
            centers = [median(v) for v in values]
            ranges = [[c - min(v) for c, v in zip(centers, values)],
                      [max(v) - c for c, v in zip(centers, values)]]
            positions = [i + offset for i in range(len(modes))]
            if metric == "wake_delay_ms":
                ax.errorbar(positions, centers, yerr=ranges, fmt="o",
                            capsize=4, label=policy, color=color)
            else:
                ax.bar(positions, centers, 0.36, yerr=ranges,
                       capsize=4, label=policy, color=color)
        ax.set_xticks(range(len(modes)), modes)
        ax.set_ylabel(label)
        if metric == "wake_delay_ms":
            ax.set_yscale("log")
            ax.set_ylabel(label + " — log scale")
        else:
            ax.set_ylim(bottom=0)
        ax.legend()
        ax.set_title("Median across runs; error bars show observed min–max", fontsize=11)
        fig.savefig(figure_dir / f"{metric}.png", dpi=180)
        fig.savefig(figure_dir / f"{metric}.pdf")
        plt.close(fig)


if __name__ == "__main__":
    main()
