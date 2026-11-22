# Multicore MLFQ scheduler for xv6-riscv

A preemptive four-level feedback scheduler with shared FIFO run queues,
multicore CPU ownership, elapsed-time accounting, independent quanta and
allotments, periodic boosts, runtime configuration, affinity, and kernel traces.

Base: MIT xv6-riscv commit `fa2ddcb7ca87a77af579380a6eafd5aff163b44f`.
The default build runs four CPUs; builds with one through eight CPUs are supported.

## Build and run

Inside Ubuntu / WSL:

```bash
cd '/mnt/c/Users/Aayush/IITGN/CS 330/MLFQScheduler/xv6'
make qemu
```

For an extracted archive, enter its `xv6` directory before running `make qemu`.

At the xv6 prompt:

```text
schedstat
schedtest
schedstress 7 100 12
schedbench mixed 1200 8
schedbench pipe 1200 8
```

Exit QEMU with **Ctrl-a, then x**. Change build parameters in the Ubuntu shell:

```bash
make qemu CPUS=8
make qemu SCHED=DEFAULT CPUS=4
make qemu SCHED=MLFQ CPUS=4 QUANTA=1,2,4,8 ALLOTMENTS=4,8,16,32 BOOST=64
```

| Setting | Default | Meaning |
| --- | --- | --- |
| `SCHED` | MLFQ | MLFQ or instrumented DEFAULT policy |
| `CPUS` | 4 | 1 through 8 emulated CPUs |
| `QUANTA` | 1,2,4,8 | Peer rotation intervals, in ticks |
| `ALLOTMENTS` | 2,4,8,16 | Cumulative CPU budget before demotion |
| `BOOST` | 100 | Global boost interval; zero disables it |
| `TIMER` | 100000 | Time-counter units per interrupt: 10 ms on QEMU virt |
| `CHECKS` | 1 | Full scheduler invariant checks; zero omits automatic scans |

A configuration stamp forces a rebuild when any setting changes. The command
runner makes an independent kernel and disk-image copy for every guest boot.

## Runtime controls

These commands run inside xv6 and take effect without rebuilding:

```text
schedctl
schedctl boost 64
schedctl inherit 1
schedctl 1 2 4 8 4 8 16 32 100
schedctl trace 1
schedstress 19 20 6
schedtrace
schedctl trace 0
```

The nine numeric parameters are four quanta, four allotments, and a boost
interval. Allotments must be positive multiples of their quanta. A policy
configuration update resets live priorities/budgets atomically and preserves
lifetime counters. Trace toggling is observational: it preserves budgets and
the boost deadline. Trace dumps freeze capture before console output; re-enable it explicitly to
continue recording. Overwritten records are detected.

`setaffinity(mask)` is also available to user programs. A mask change excluding
the current CPU migrates the caller before returning to user space.

## Scheduling rules

- Q0 is highest. Choose the oldest eligible process in the highest nonempty queue.
- A quantum rotates equal-priority peers; a cumulative allotment demotes a process.
- Execution is charged on timers, blocking, yield, exit, and snapshots. Short
  bursts retain their consumed time rather than escaping timer sampling.
- Blocking and early yield preserve debt. Higher-priority preemption preserves
  the remaining turn and returns it to the front; voluntary yield joins the tail.
- Q3 rotates without further demotion. Excess execution time is carried forward.
- Global boosts charge running intervals before resetting live priorities.
  Sleeping processes remain asleep; old queues merge in Q0 through Q3 FIFO order.
- Fork inherits affinity and, by default, consumed scheduling budgets. Exec
  retains history. Independent benchmark jobs explicitly start fresh at Q0.
- Every CPU checks preemption on its local timer. This version uses periodic
  timer response rather than cross-hart rescheduling interrupts.

## Source map

| File | Responsibility |
| --- | --- |
| `xv6/kernel/sched.c` | Queues, locking, budgets, accounting, controls, traces, invariants |
| `xv6/kernel/sched.h` | Shared configuration/statistics/event ABI |
| `xv6/kernel/proc.c` | Lifecycle hooks and context-switch integration |
| `xv6/kernel/trap.c` | Per-hart timer checks and global boost clock |
| `xv6/user/schedtest.c` | Targeted correctness and race tests |
| `xv6/user/schedstress.c` | Seeded lifecycle, IPC, affinity, and configuration stress |
| `scripts/replay_trace.py` | Independent policy/ownership/budget replay model |
| `scripts/run_tests.py` | Full validation matrix and recorded results |

Read [DESIGN.md](docs/DESIGN.md), [WALKTHROUGH.md](docs/WALKTHROUGH.md), and
[TESTING.md](docs/TESTING.md) for the implementation and verification details.
[REPORT.md](docs/REPORT.md) records measurements and limitations.

## Reproduce verification and measurements

From the outer project folder inside Ubuntu:

```bash
python3 scripts/xv6.py --cpus 4 --command schedtest
python3 scripts/run_tests.py
python3 scripts/evaluate.py
python3 scripts/verify_release.py
```

Release verification also checks CPU counts 3, 5, 6, and 7 and rebuilds/tests
the exported source in a clean temporary directory.

The full test matrix uses both policies and 1, 2, 4, and 8 CPUs. It includes the
complete upstream suite, seeded stress, trace replay, and overflow validation.
Use a separate `--output` directory to preserve an earlier run. For a shorter
local check:

```bash
python3 scripts/run_tests.py --cpus 4 --seeds 1 --quick --output results/local-check
```

Collection and testing require only Python's standard library. Plotting uses:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
```

Current results are under `results/v2/`. Earlier single-core results under
`results/main/` and `results/diagnostics/` are historical, not measurements of
the multicore implementation. The working directory also retains the prior
source in `reference/single-core.zip`. Historical data and development runs are
excluded from the source archive, which includes canonical current validation
and evaluation results. It excludes
Git internals, virtual environments,
generated build objects, and temporary guest images.

## Scope

This is a global-queue scheduler for xv6's bounded process table, not a per-CPU
work-stealing design. A shared lock serializes policy decisions. Priority
response is bounded by the next safe timer point plus lock/kernel delays;
it is not a real-time guarantee. Fork inheritance removes a fresh-budget reset
but does not implement per-user or process-family scheduling groups.

MIT's original license is retained in `xv6/LICENSE`.
