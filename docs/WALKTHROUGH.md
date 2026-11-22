# Execution walkthrough

## Admission and dispatch

`allocproc()` initializes a process while holding its existing process lock.
`sched_alloc()` creates a fresh statistics record under the scheduler lock.
A fork then copies affinity and, when enabled, scheduling debt from its parent.
It never copies lifetime counters. `sched_ready()` inserts the runnable child.

Each CPU's scheduler peeks at the highest eligible queue entry, releases the
queue lock, and takes the candidate's process lock. `sched_claim()` then
reacquires the queue lock and validates the candidate again. Another CPU may
have claimed it, or higher-priority work may have arrived in the meantime.
An unsuccessful claim retries; it never dispatches a stale selection.

A successful claim removes the process, publishes its CPU owner, records its
run-start time, and releases the queue lock. The process lock alone crosses
`swtch()`, preserving xv6's context-switch contract.

## Execution budgets

The default quanta are 1, 2, 4, and 8 ticks. Default allotments are twice those
values. With no blocking or boosts, a fresh CPU-bound process receives 2 ticks
at Q0, 4 at Q1, and 8 at Q2 before reaching Q3. It can rotate among peers several
times before demotion.

`charge()` computes elapsed time since the last accounting point. It updates
total CPU time and the per-level/per-CPU breakdown, then consumes that interval
against the current allotment. If the interval crosses a demotion boundary,
its remaining portion is charged at the next level. At Q3, modulo arithmetic
bounds the work needed even after a long delayed interrupt.

A quantum expiration requests rotation. An allotment expiration requests
rotation and demotion. These decisions are separate. An early block or yield
never erases the consumed portion of either budget.

## Returning to the scheduler

Timer paths call `schedpreempt()` on the CPU that owns the running process.
It charges execution and requests a switch on expiry, on a pending reset, or
when higher-priority eligible work is ready. Other processes pinned to a
different CPU do not cause an irrelevant preemption.

`sched_stop()` closes the running interval, clears CPU ownership, and changes
state while holding both locks. A voluntary yield joins the queue's tail.
Priority preemption with a partial turn joins the front. Blocking leaves the
process off the queues. `wakeup()` reinserts a sleeper at its current level.

## Boosts and configuration

Only CPU0 advances the global boost deadline. Under the scheduler lock it
closes all running CPU intervals at one timestamp, resets live budgets, and
merges the old queues. It never acquires a process lock while holding the
scheduler lock. Each owning CPU observes the reset at its next timer point.

Runtime policy configuration uses the same atomic reset mechanism. Disabling
boosts does not erase accumulated statistics. Enabling or disabling trace
capture uses a separate observational command so measurement cannot reset
scheduling history.

## Statistics and traces

Snapshots and final-child statistics share the same exported structure.
`waitstats()` copies a child's record before freeing its slot. Live snapshots
close an executing interval and include any open waiting/sleeping interval in
the returned copy. CPU time equals both the per-level and per-CPU sums.
Setup, runnable wait, execution, and sleep exactly partition a process's lifetime.

The trace contains an initial snapshot followed by queue, execution, ownership,
and lifecycle events. Sequence numbers and a loss counter expose overwritten
records. The host replay model reconstructs queues and budgets independently.
It checks each dispatch and verifies that every charged interval matches its
run-start/end timestamps.

## Configuration changes

| Change | Location |
| --- | --- |
| Quantum or allotment values | Build arguments or `schedctl` runtime configuration |
| Boost frequency | `BOOST` or `schedctl boost` |
| Fork budget inheritance | `schedctl inherit` / configuration flag |
| Timer resolution | `TIMER`; applied to initial and recurring per-hart timers |
| CPU affinity | `setaffinity(mask)` syscall |
| Automatic invariant scans | `CHECKS`; explicit `schedcheck()` remains available |
| Queue count | Shared ABI, default arrays, queue operations, tools, and tests must all change together |
| Per-CPU queues or immediate rescheduling IPIs | Requires a different synchronization/preemption design |
