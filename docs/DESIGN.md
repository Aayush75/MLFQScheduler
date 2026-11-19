# Design

## Scope and representation

The kernel runs with one CPU. There are four logical FIFO queues. A process's
`ss.level` identifies its queue and `ready_order` identifies its place in that
queue. A process belongs to a run queue exactly when its state is `RUNNABLE`.

The scheduler scans at most `NPROC` processes and selects the minimum
`(level, ready_order)` pair. This is a queue representation using the process
table rather than separately allocated queue objects. It gives the same
highest-priority/FIFO selection rule and removes duplicate-entry and unlinking
bugs. Its cost is O(NPROC) per selection rather than O(1) queue operations.
At xv6's limit of 64 processes, this is a reasonable teaching tradeoff.

## Policy

Q0, Q1, Q2, Q3 have default quanta 1, 2, 4, and 8 ticks. The allotment before
demotion equals one quantum. The separate `used` field counts cumulative CPU
ticks at the current level. It is reset on demotion, bottom-queue rotation, and
global boost. It is retained across sleep and higher-priority preemption.

One CPU-bound process without boosts first receives 1 tick in Q0, 2 ticks in
Q1, and 4 ticks in Q2. It then stays in Q3, rotating every 8 ticks. An early
block does not buy a fresh allotment. Waking joins the current queue's tail;
preemption by a higher-priority process preserves the old FIFO position so the
interrupted process can finish its remaining quantum before newer peers.

New children start at Q0 with fresh budgets and statistics. `exec` changes the
program image but retains the process's scheduling history. These are explicit
policy choices, not requirements of xv6.

## Timer path

```text
user/kernel timer interrupt
    devintr -> clockintr
        schedtick: sample states, charge CPU, demote/rotate, optionally boost
        increment upstream ticks and wake timer sleepers
    usertrap/kerneltrap
        schedpreempt: expired slice, boost, or higher-priority runnable job?
            yes -> yield -> scheduler
            no  -> continue the current process
```

Accounting happens once in `clockintr`, before timer sleepers are woken. This
counts both user execution and execution in the kernel on behalf of a running
process. The two trap paths decide whether to yield; neither charges CPU again.
The stock policy still yields on every timer interrupt and uses the original
table-order scheduler loop.

The original timer is unchanged: 1,000,000 time-counter units per interrupt,
approximately 100 ms with QEMU virt's 10 MHz clock. A quantum can span several
timer interrupts. This distinction is essential: unconditional yielding on each
tick would not implement the configured multi-tick quantum.

Higher-priority wakeups are acted on at the next timer interrupt and safe
scheduling point. No interprocessor interrupts or immediate device-interrupt
preemption are implemented. Kernel sections with interrupts disabled can delay
the next scheduling point.

## Global boosts

Every 64 ticks, after charging the tick to the old queue, the kernel resets all
RUNNING, RUNNABLE, and SLEEPING processes to Q0 with `used=0`. A sleeper's state
does not change. RUNNABLE processes retain their previous FIFO order values;
the running process receives a new tail position. When queues merge, ordering
is therefore by last enqueue/rotation order across levels, not by old level.

A simultaneous quantum expiry may increment the historical demotion counter,
but the boost determines the priority/budget for the next dispatch. All live
eligible processes count the boost, even if already at Q0. Boosts occur while
idle too. They do not clear CPU, waiting, or other lifetime counters.

Boosting gives old runnable jobs periodic opportunities at Q0. Under a finite
set of processes and bounded kernel critical sections, FIFO ordering and finite
quanta support progress. The finite admission-pressure diagnostic demonstrates
the intended effect; it is not a universal real-time latency proof. In
particular, global boosts can temporarily increase interactive waiting by
putting many jobs at the same priority.

## Synchronization

Process changes use the existing `p->lock`, which disables local interrupts.
Global ordering and the scheduling clock rely on the single CPU. During the
selection scan, interrupts stay disabled, so another process cannot invalidate
the selected candidate between releasing and reacquiring its process lock.
Only the selected process lock crosses `swtch`, preserving xv6's contract.

`schedtick` runs with interrupts disabled and before acquiring `tickslock`.
It never holds two process locks simultaneously. No new lock is introduced.
The existing `wait_lock` protects child relationships and precedes child locks
in wait/exit paths. Statistics copyout uses the pinned version's normal pointer
validation; a bad waitstats copyout leaves the zombie available for retry.

This design is intentionally not SMP-safe. Supporting multiple CPUs requires
synchronizing the global sequence/clock, revalidating candidate selection,
coordinating boosts, and defining cross-core priority/preemption behavior.
Removing the Makefile's CPU check alone is insufficient.

## Statistics API

```c
int getpinfo(int slot, struct schedstat *out);
int waitstats(int *status, struct schedstat *out);
```

`getpinfo(-1, ...)` snapshots the caller. Slots 0 through NPROC−1 enumerate
processes; unused or invalid slots return −1. Snapshots are individually
consistent, not one atomic view of the entire table. Exported records contain
no kernel pointers. A small stack record avoids an NPROC-sized kernel buffer.

`waitstats` shares the original wait implementation and returns the reaped
child's PID. It copies finalized counters before freeing the process slot.
Ordinary `wait` remains supported. A null status pointer is allowed; a null
statistics pointer is rejected by the new syscall.

| Field | Meaning |
| --- | --- |
| `level`, `used` | Current scheduling level and consumed budget |
| `cpu_ticks`, `queue_ticks` | Execution samples, total and by level |
| `wait_ticks` | Samples in RUNNABLE state, excluding sleep |
| `sleep_ticks` | Samples in SLEEPING state |
| `born`, `first_run`, `exited` | Creation, first dispatch, and exit ticks |
| `dispatches` | Entries into RUNNING; may resume the same PID |
| `demotions`, `boosts` | Cumulative policy events |
| `max_wait` | Longest continuous runnable interval, in guest ticks |
| `wakeups` | Completed sleeping-to-runnable-to-running observations |
| `wake_delay`, `max_wake_delay` | Sum and maximum wakeup-to-dispatch delay |
| `now`, `now_time` | Tick and finer time-counter values at snapshot |

Never-dispatched `first_run` is `~0UL`. A live process's `exited` is zero.
`max_wait` includes an ongoing runnable wait in a snapshot. Waiting is reset at
dispatch/preemption/wakeup boundaries, but a global boost does not erase an
already-running wait. A new allocation clears the full statistics record.

The finer delay counter uses time-counter units, not timer ticks. On this QEMU
machine, divide by 10,000 for milliseconds. Wakeup statistics cover all sleep
channels during the process lifetime, including a start-barrier wait if one
occurred. The code does not identify a specific pipe channel in exported data.

## Evaluation controls and limitations

Each workload starts behind a pipe barrier. The benchmark reports
release-to-first-work response time separately from process-lifetime first
dispatch. Turnaround is exit tick minus shared release tick. CPU/wait/sleep
counters are lifetime samples and may include setup before that release.
Workers emit fixed-size result records and are reaped after timed work; console
printing happens in the parent after the worker records have been collected.

The arithmetic workloads perform the same fixed work under each policy, and
the host runner checks checksums across policies and repetitions. A timed
blocking worker models I/O-like behavior; a separate pipe workload performs
actual blocking reads from a periodic producer. Each workload gets a fresh
single-CPU boot. Both policies use the same instrumentation and compiler.

Statistics based on ticks can miss CPU bursts that finish between interrupts.
A process may report zero sampled CPU while doing real work. Keeping budgets
across blocking closes the budget-reset loophole at this resolution, but does
not prevent arbitrary sub-tick gaming. Fresh-Q0 fork also permits fork-based
gaming. These limits should be explained rather than hidden.

The comparison does not measure scheduler instruction overhead directly.
Dispatch counts are only an overhead-related proxy. Fixed-work service totals
are not a fixed-window fairness experiment. The rule tests separately compare
three identical continuously runnable workers over a common observation window.

## Differences from the first proposal

The original plan described explicit run queues, extra tracing machinery, and
more elaborate configuration APIs. Following the request for small, explainable
code, the implementation uses logical FIFO queues, two small statistics
syscalls, build-time parameters, and a user-space diagnostic trace. This document
describes the implemented design; the initial plan is historical context.
