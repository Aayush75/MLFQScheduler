"""Run matched workloads on fresh single-CPU boots; retain raw serial logs."""

import argparse
import csv
import hashlib
import json
from pathlib import Path
import platform
import subprocess
from datetime import datetime, timezone

from xv6 import ROOT, XV6, Qemu, build


def version(*command):
    return subprocess.check_output(command, text=True).splitlines()[0]


def upstream_commit():
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=XV6,
                                       stderr=subprocess.DEVNULL, text=True).strip()
    except subprocess.CalledProcessError:
        # The hand-in ZIP excludes Git internals, but still records its base.
        return (XV6 / "UPSTREAM").read_text().strip()


def source_hash():
    digest = hashlib.sha256()
    for path in sorted(XV6.rglob("*")):
        if path.is_file() and (path.suffix in {".c", ".h", ".S", ".pl", ".ld"}
                               or path.name == "Makefile") and path.name != "usys.S":
            digest.update(path.relative_to(XV6).as_posix().encode())
            digest.update(path.read_bytes())
    return digest.hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repeats", type=int, default=10)
    parser.add_argument("--units", type=int, default=400)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--boost", type=int, default=64)
    parser.add_argument("--quanta", default="1,2,4,8")
    parser.add_argument("--modes", nargs="+", default=["cpu", "mixed", "short", "pipe"])
    parser.add_argument("--output", type=Path, default=ROOT / "results/main")
    args = parser.parse_args()
    if args.repeats < 1:
        parser.error("repeats must be positive")
    args.output.mkdir(parents=True, exist_ok=True)
    metadata = {
        "started_utc": datetime.now(timezone.utc).isoformat(),
        "upstream_commit": upstream_commit(),
        "source_sha256": source_hash(),
        "host": platform.platform(), "python": platform.python_version(),
        "qemu": version("qemu-system-riscv64", "--version"),
        "compiler": version("riscv64-linux-gnu-gcc", "--version"),
        "cpus": 1, "timer_interval": 1000000, "timebase_hz": 10000000,
        "repeats": args.repeats, "units": args.units, "workers": args.workers,
        "boost": args.boost, "quanta": args.quanta, "modes": args.modes,
        "ordering": "alternate DEFAULT/MLFQ order by repetition; fresh boot per workload",
    }
    meta_path = args.output / "metadata.json"
    meta_path.write_text(json.dumps(metadata, indent=2) + "\n")
    checksums = {}
    with (args.output / "samples.csv").open("w", newline="") as file:
        writer = None
        for repeat in range(args.repeats):
            policies = ["DEFAULT", "MLFQ"] if repeat % 2 == 0 else ["MLFQ", "DEFAULT"]
            for policy in policies:
                build(policy, args.boost, args.quanta)
                for mode in args.modes:
                    log = args.output / "logs" / f"{repeat:02d}-{policy}-{mode}.log"
                    with Qemu(log) as qemu:
                        output = qemu.command(f"schedbench {mode} {args.units} {args.workers}")
                    lines = [line[len("RESULT,"):] for line in output.splitlines()
                             if line.startswith("RESULT,")]
                    rows = list(csv.DictReader(lines))
                    if len(rows) != args.workers or len({r["worker"] for r in rows}) != args.workers:
                        raise RuntimeError(f"Incomplete result set: {log}")
                    for row in rows:
                        if row["policy"] != policy or row["mode"] != mode:
                            raise RuntimeError(f"Wrong build/workload: {log}")
                        if sum(int(row[f"q{q}"]) for q in range(4)) != int(row["cpu"]):
                            raise RuntimeError(f"CPU accounting mismatch: {log}")
                        if int(row["exit"]) < int(row["release"]):
                            raise RuntimeError(f"Invalid timestamps: {log}")
                        # Identical work must produce the same checksum across
                        # policies and repetitions, regardless of scheduling.
                        key = mode, row["worker"]
                        if checksums.setdefault(key, row["checksum"]) != row["checksum"]:
                            raise RuntimeError(f"Workload checksum mismatch: {log}")
                        row.update(run=repeat, units=args.units, workers=args.workers,
                                   boost=args.boost, quanta=args.quanta)
                        if writer is None:
                            writer = csv.DictWriter(file, fieldnames=list(row))
                            writer.writeheader()
                        writer.writerow(row)
                    file.flush()
                    print(f"{repeat + 1}/{args.repeats}: {policy} {mode} OK", flush=True)
    metadata["finished_utc"] = datetime.now(timezone.utc).isoformat()
    meta_path.write_text(json.dumps(metadata, indent=2) + "\n")


if __name__ == "__main__":
    main()
