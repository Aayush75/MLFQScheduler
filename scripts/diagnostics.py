"""Additional configurations, priority timelines, and bounded arrival pressure."""

import csv

from xv6 import ROOT, Qemu, build


def records(output, prefix):
    lines = [line[len(prefix) + 1:] for line in output.splitlines()
             if line.startswith(prefix + ",")]
    return list(csv.DictReader(lines))


def save(path, rows):
    with path.open("w", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def main():
    directory = ROOT / "results/diagnostics"
    directory.mkdir(parents=True, exist_ok=True)
    for boost, quanta in [(0, "1,2,4,8"), (128, "2,4,8,16")]:
        build("MLFQ", boost, quanta)
        with Qemu(directory / f"rules-{boost}.log") as qemu:
            output = qemu.command("schedtest")
        print(output, flush=True)

    pressure = []
    for boost in [0, 64]:
        build("MLFQ", boost)
        with Qemu(directory / f"trace-{boost}.log") as qemu:
            output = qemu.command("schedbench trace 75")
        save(directory / f"trace-{boost}.csv", records(output, "TRACE"))
        with Qemu(directory / f"pressure-{boost}.log") as qemu:
            output = qemu.command("schedbench starve 40")
        pressure.extend(records(output, "PRESSURE"))
        print(output, flush=True)
    save(directory / "pressure.csv", pressure)
    # Leave the manual demonstration at its documented default configuration.
    build("MLFQ", 64)


if __name__ == "__main__":
    main()
