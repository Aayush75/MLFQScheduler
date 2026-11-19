# MLFQ scheduling in xv6-riscv

Aayush Kuloor — CS 330 Operating Systems project.

This implementation adds four logical priority queues, CPU-based demotion,
configurable quanta, periodic global boosts, and process scheduling statistics.
It uses one CPU and xv6's existing process table to keep the kernel changes
small and explainable. No linked lists, dynamic run queues, or new locks.

Upstream: MIT xv6-riscv, commit
`fa2ddcb7ca87a77af579380a6eafd5aff163b44f`.

## Run inside Ubuntu / WSL

```bash
cd '/mnt/c/Users/Aayush/IITGN/CS 330/MLFQScheduler/xv6'
make qemu
```

At the xv6 `$` prompt:

```text
schedstat
schedtest
schedbench mixed 400 4
schedbench pipe 400 4
schedbench trace 75
```

Exit QEMU: **Ctrl-a, then x**. `trace 75` takes about 7.5 seconds and prints a
diagnostic priority timeline; trace printing is excluded from performance runs.

Change the policy or parameters from the Ubuntu shell:

```bash
make qemu SCHED=DEFAULT
make qemu SCHED=MLFQ QUANTA=2,4,8,16 BOOST=128
make qemu SCHED=MLFQ BOOST=0
```

The Makefile rebuilds automatically when these settings change. `BOOST=0`
disables boosting. Only `CPUS=1` is supported. Default quanta are 1,2,4,8 ticks,
with boosting every 64 ticks. Upstream's timer is unchanged: approximately
100 ms per tick on QEMU virt.

## Where to read the code

1. `xv6/kernel/sched.h`: configuration and the statistics record.
2. `xv6/kernel/proc.h`: three ordering/timestamp fields and the statistics record.
3. `xv6/kernel/proc.c`: `runnable`, `schedtick`, `schedpreempt`, and the MLFQ
   branch of `scheduler`. These are the main scheduling changes.
4. `xv6/kernel/trap.c`: one accounting call and conditional timer preemption.
5. `getpinfo` and `kwaitstats` in `proc.c`: safely export live/final statistics.
6. `xv6/user/schedtest.c` and `schedbench.c`: tests and workloads.

Comments beginning with **Viva**, **Why**, or **Common** explain likely questions
and changes beside the relevant code. [The walkthrough](docs/WALKTHROUGH.md)
explains the control flow and common modifications in more detail.

## Rules

- Q0 is highest. A new process starts there.
- Choose the highest nonempty level, then its oldest FIFO position.
- Use an entire quantum across timer ticks unless higher-priority work arrives.
- Consuming the allotment demotes a process; Q3 rotates without further demotion.
- Blocking preserves the level and consumed CPU budget. Waking joins its level's tail.
- Higher-priority preemption preserves the remaining budget and FIFO position.
- Global boosts reset priorities/budgets, including sleepers, without waking them.
- At a boost, runnable FIFO positions retain their relative order; the running
  process moves to the tail. Lifetime statistics are never reset by a boost.
- `exec` preserves history; a forked child starts fresh. Reused slots start fresh.

[Design and limitations](docs/DESIGN.md) defines the exact behavior and metrics.

## Reproduce tests and evaluation

From the outer project folder, inside Ubuntu:

```bash
python3 scripts/xv6.py --policy MLFQ --command schedtest --command forktest --command 'usertests -q'
python3 scripts/xv6.py --policy DEFAULT --command schedtest --command forktest --command 'usertests -q'
python3 scripts/xv6.py --boost 0 --command schedtest
python3 scripts/xv6.py --quanta 2,4,8,16 --boost 128 --command schedtest

python3 scripts/benchmark.py --repeats 10 --units 400 --output results/main
.venv/bin/python scripts/analyze.py results/main
python3 scripts/diagnostics.py
.venv/bin/python scripts/plot_trace.py
python3 scripts/package.py
```

Use `--log results/raw/name.log` to keep individual test logs. The console
driver and benchmark runner use only Python's standard library. Only plotting
requires matplotlib, installed in the project-local `.venv`:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install matplotlib
```

`results/main/samples.csv` contains the worker-level measurements;
`summary.csv` contains run-level summaries. `metadata.json` records upstream
commit, a source hash, compiler, QEMU, parameters, and run order. Raw serial logs
are retained. Use a different `--output` directory to preserve a previous run.

Read [the report](docs/REPORT.md) for the measured results and
[the demo guide](docs/DEMO.md) for a short presentation sequence.

## Important limits

This is a single-CPU teaching scheduler. CPU service uses tick sampling, so
short bursts can be undercounted; preserving budgets prevents resets across
sleep, but does not provide perfect protection against sub-tick gaming or fork
gaming. Wakeup delay uses the finer RISC-V time counter. Dispatch counts are
not a measurement of scheduler execution time or distinct-process switches.
MLFQ is expected to trade CPU-job fairness/latency for interactive responsiveness;
it need not outperform the default policy on every workload.

MIT's original license remains in `xv6/LICENSE`. The initial proposal is in
`PROJECT_PLAN.md`; the implementation follows the later simplicity requirement.
