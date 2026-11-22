# Scheduler design

## Shared state and locking

Four doubly linked FIFO queues contain exactly the RUNNABLE processes. Queue
links, scheduling budgets, statistics, CPU owners, configuration, and trace
records are protected by one scheduler lock. Process lifecycle transitions
also hold the existing process lock.

The lock order is:

```text
existing wait/ticks locks -> process lock -> scheduler lock
```

The scheduler lock never acquires a process lock, allocates memory, copies user
memory, or crosses a context switch. CPU0's boost operation changes scheduling
metadata under that lock without taking process locks. It does not change
process states. Active state changes hold both locks, so a synchronized reader
can inspect state under either lock. Allocation/freeing handles inactive slots
under their process locks before exposing or after removing scheduler metadata.

### Claim protocol

Each CPU selects the highest-priority queue entry eligible for its affinity:

1. Peek under the scheduler lock, then release it.
2. Acquire the candidate's process lock.
3. Reacquire the scheduler lock and revalidate the candidate.
4. Remove it, publish RUNNING and its unique CPU owner, and release the queue lock.
5. Switch with only the process lock held, as required by xv6.

A stale candidate is retried. If higher-priority work appeared while the CPU
waited for the process lock, revalidation rejects the old choice. A second CPU
cannot dispatch the same process because it must acquire that process lock.
When a process stops, its lock remains held until the old CPU has switched
away; publishing a runnable entry does not allow premature stack reuse.

## Scheduling policy

Q0 is highest. Quanta default to `[1,2,4,8]` ticks; cumulative allotments default
to `[2,4,8,16]` ticks. A quantum controls one turn among peers. An allotment
controls total execution at a level. Several turns can therefore precede a
demotion. Q3 rotates but does not demote further.

The kernel retains elapsed time across blocking, yielding, and preemption.
Early voluntary yield joins the tail without restoring debt. Higher-priority
preemption with a partial turn joins the front; an expired or reset turn joins
the tail. Waking inserts at the tail of the retained priority.

Fork inherits affinity and, by default, the parent's current priority and debt
at the inheritance hook. Lifetime counters start fresh. Exec changes the image
without resetting history. The inheritance flag is configurable; benchmark
admission disables it so all independent workers start at Q0. This is not a
per-user or process-family scheduler, and creating additional runnable children
can still increase a family's aggregate CPU share.

## Elapsed-time accounting

The QEMU virt time counter is shared across harts and runs at 10 MHz in the
verified platform configuration. `TIMER=100000` gives a nominal 10 ms interrupt
period. The accounting unit remains one time-counter unit, not one interrupt.

Every dispatch records a start timestamp. Timer checks, snapshots, blocking,
yield, and exit close elapsed intervals. Charging occurs under the scheduler
lock, using a timestamp captured after acquiring it. This avoids an older
cross-CPU timestamp subtracting from a more recent accounting point.

A delayed interrupt can cross more than one allotment. `charge()` splits the
interval at each demotion boundary and carries the excess forward. At Q3,
modulo arithmetic handles remaining execution without an unbounded loop.
Short bursts between interrupts are charged when they block or yield.

CPU residency includes user execution and kernel execution while dispatched.
It includes time spent waiting for kernel locks while the process owns a CPU.
It excludes the scheduler interval after stopping and before the next claim.
It is guest logical CPU residency, not host OS thread CPU time.

### Conservation properties

For every live or finalized process snapshot:

```text
CPU time = sum(per-priority CPU time) = sum(per-CPU CPU time)
setup + runnable wait + CPU time + sleeping time = measured lifetime
```

For a zombie, measured lifetime ends at exit rather than at the later reap.
For a live process, an open waiting/sleeping/setup interval is included in the
returned snapshot. Finalized accounting is checked in the kernel and by user
tests. Per-CPU aggregate busy time is bounded by that CPU's online lifetime.

## Global boosts and runtime configuration

CPU0 checks the global deadline. On a boost it first charges all running
intervals at one shared timestamp, then resets live priorities/budgets. Old
ready queues merge in Q0–Q3 order, preserving each queue's FIFO order. Running
processes observe a pending rotation on their next safe timer point. Sleeping
processes stay asleep. Zombies retain finalized scheduling metadata.

A configuration update validates the complete request before changing any
state. Quanta are positive and bounded; allotments are bounded multiples of
quanta; the boost interval and flags are bounded. A successful policy update
atomically resets live budgets and advances the configuration generation.
It preserves all lifetime counters.

`SCHED_TRACE` changes only observation. It preserves policy budgets and the
boost deadline. Starting a trace records a complete initial state, including
pending rotations and existing queue order.

## Multicore preemption and affinity

Each CPU charges its current process and checks preemption on its own timer.
Higher-priority work pinned to a different CPU is not an eligible preemption
candidate. A global boost marks running processes for rotation but does not
switch a remote stack directly.

This version uses periodic local timer response rather than rescheduling IPIs.
A new eligible task is considered at the next safe timer point; disabled
interrupts and lock contention can delay it. Idle CPUs use `wfi` without locks
and wake on their periodic timers. These are correctness and responsiveness
rules, not hard real-time bounds.

Affinity is a nonempty subset of online CPUs. If a new mask excludes the
current CPU, the syscall stops the caller while retaining its process lock,
then schedules it on an allowed CPU before returning. Statistics record CPU
residency and migrations separately.

## Shared APIs

| API | Behavior |
| --- | --- |
| `getpinfo(slot, out)` | Snapshot one slot; −1 selects the caller |
| `waitstats(status, out)` | Reap a child and copy finalized statistics first |
| `schedinfo(out)` | Configuration, online CPUs, queue sizes, and aggregate counters |
| `schedctl(command, config)` | Get/set policy or toggle observational tracing |
| `setaffinity(mask)` | Change the caller's eligible CPUs |
| `schedyield()` | Voluntary yield without restoring budgets |
| `schedcheck()` | Explicitly check scheduler invariants |
| `schedtrace(cursor, events, capacity)` | Read a bounded trace batch and advance a loss-aware cursor |

User memory copying happens outside the scheduler lock. Trace batches use a
bounded page allocated outside that lock, avoiding a large kernel-stack array.
An invalid waitstats copyout does not consume the zombie. A table enumeration
is a sequence of individually consistent records, not an atomic global snapshot.

## Tracing and independent replay

The trace records allocation, inheritance, readiness, dispatch, elapsed charge,
stop, free, affinity changes, configuration, and boosts. Sequence numbers expose
missing/reordered records. The ring has finite capacity; the reader reports
lost records relative to its cursor. A full replay also requires the initial
snapshot, so an overwritten prefix cannot silently pass.

The host model reconstructs logical queues and running CPU owners. Each
dispatch must select the first eligible entry at the highest priority. Each
charged interval must equal the elapsed run timestamps and produce the expected
budget state. Duplicate ownership, duplicate queue entries, invalid affinity,
wrong reinsertion, lost debt, and lifecycle misuse are rejected.

Trace mode is for correctness/diagnostics. Performance runs keep it disabled.
Automatic invariant scans are configurable, but explicit checks and basic
ownership assertions remain available in all builds.

## Baseline and boundaries

The DEFAULT build preserves xv6's table-order per-CPU selection and yields on
every timer interrupt. It shares the same CPU count, timer, lifecycle hooks,
accounting, affinity checks, and invariant setting as MLFQ. The comparison is
between two instrumented policies, not against an unmodified historical binary.

Shared queues serialize decisions and target xv6's 64 slots and up to eight
CPUs. Per-CPU queues, work stealing, scheduling groups, and real-time guarantees
are outside this implementation. The measured configurations and tests provide
specific evidence; they do not constitute an exhaustive concurrency proof.
