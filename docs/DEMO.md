# Short demonstration

Run commands from Ubuntu, with the terminal visible to the professor.

## 1. Explain the design

Open `xv6/kernel/sched.h`: four quanta, one boost interval, one statistics
record. Open `proc.c`: `runnable`, `schedtick`, `schedpreempt`, and `scheduler`.
Explain logical FIFO queues and the deliberate single-CPU scope.

## 2. Boot and test

```bash
cd '/mnt/c/Users/Aayush/IITGN/CS 330/MLFQScheduler/xv6'
make SCHED=MLFQ BOOST=64 QUANTA=1,2,4,8 qemu
```

At the xv6 prompt:

```text
schedstat
schedtest
```

Explain that the test output comes from assertions, not a hard-coded success
message. It checks budget persistence, demotion, multi-tick turns, contention,
priority preemption, and boosting sleepers.

## 3. Show priority changes

```text
schedbench trace 75
```

Point out Q0→Q1→Q2→Q3, cumulative per-queue CPU service, and the next global
boost. The boost is tied to time since boot, not time since this command began.
Trace output is a diagnostic and is not used in performance measurements.

## 4. Show mixed workloads

```text
schedbench mixed 400 4
schedbench pipe 400 4
```

Explain the CPU workers, the periodic blocker, and the actual pipe reader.
`wake_delay / wakeups / 10000` gives mean wakeup delay in milliseconds on this
QEMU machine. Zero sampled CPU does not mean zero execution.

## 5. Switch policies and change a parameter

Exit QEMU with Ctrl-a, x. In Ubuntu:

```bash
make SCHED=DEFAULT qemu
```

Run the same mixed workload, then show the pre-generated comparison plots.
After exiting, try `make SCHED=MLFQ QUANTA=2,4,8,16 BOOST=128 qemu` and show
the changed `schedstat` configuration. The Makefile notices changed settings.

## 6. Present results honestly

Use `docs/REPORT.md` and `results/main/figures/`. Explain a measured improvement,
a tradeoff, and the single-CPU/tick-sampling limitations. Keep raw CSV and test
logs available if asked how a number was obtained.
