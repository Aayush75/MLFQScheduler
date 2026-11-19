# Reading and explaining the code

Read the kernel changes first. The benchmark and plotting scripts are supporting
tools; they are not part of the scheduling mechanism.

## A process's path through MLFQ

1. `allocproc` clears `ss`, so a child begins at Q0 with `used=0`.
2. `runnable` sets its state and gives it the next FIFO position.
3. `scheduler` chooses the runnable process with the smallest level, then the
   smallest FIFO position. `dispatch` updates statistics and sets RUNNING.
4. Each timer interrupt calls `schedtick` once. The running process receives
   one CPU tick in its current level. `used` increases.
5. At quantum expiry, `schedtick` demotes it (unless already at Q3), clears
   `used`, assigns a new FIFO position, and asks for preemption.
6. `schedpreempt` also asks for preemption if a higher-priority process is ready.
   Otherwise the process continues; it does not context-switch on every tick.
7. A sleeping process keeps its budget. `wakeup` makes it runnable at its
   level's tail and starts the wakeup-delay timestamp.
8. At a global boost, eligible processes return to Q0. Sleeping processes
   remain asleep. Lifetime counters remain intact.
9. On exit, `waitstats` lets the parent retrieve the final record before it
   frees the process slot. Ordinary wait uses the same path without a record.

## Worked example

With `[1,2,4,8]` and no boost, one CPU-bound process receives:

```text
charged CPU tick  0   1     3         7                15
queue            Q0  Q1    Q2        Q3               Q3
used             0   0     0         0                0
```

These are charged CPU ticks, not necessarily consecutive wall-clock ticks.
Other processes and sleeping can stretch the timeline. At a sampled CPU count
of 5, the process is in Q2 with `used=2`. If it sleeps and wakes, it is still
in Q2 with `used=2`, leaving 2 ticks before demotion.

## Likely questions

**Where are the queues?** The process table is the storage. Each level defines
a logical queue and `ready_order` defines its FIFO ordering. This is not merely
scanning by array index: the timestamp tie-breaker provides round-robin order.

**Why do we need `ready_order`?** Without it, repeatedly choosing the first
matching table entry could favor low-index processes. After using its quantum,
a process receives a new, larger sequence number and goes behind its peers.

**Why does sleeping not reset `used`?** Otherwise a CPU-heavy process could
sleep just before expiry and repeatedly buy a fresh high-priority quantum.
Cumulative charging prevents that reset trick. It does not fix sub-tick sampling.

**Is a quantum different from an allotment?** Yes. A quantum limits one turn;
an allotment limits total service at a priority. This simple version makes the
allotment one quantum. A general version would track both separately.

**Why not always call yield on a timer tick?** Q1/Q2/Q3 have multi-tick slices.
They continue until expiry or higher-priority readiness. Timer interrupts still
occur to update counters and check whether preemption is needed.

**Why does an I/O-bound process stay high?** Its CPU budget grows slowly while
it blocks. It is not automatically promoted on every I/O operation. Eventually
it can still be demoted after accumulating enough charged CPU time.

**Why boost sleeping processes?** A program's behavior may change. Resetting
its budget lets a formerly CPU-heavy process recover priority when it wakes.
Boosting must not change SLEEPING to RUNNABLE.

**How does boosting help starvation?** It periodically puts old jobs at Q0,
alongside newer jobs. FIFO order and bounded quanta then give them service.
The finite admission-pressure experiment shows this under sustained arrivals.
There is no real-time guarantee for arbitrary kernel/arrival conditions.

**Does a boost always help interactive latency?** No. Promoting many CPU jobs
can temporarily put them ahead of an interactive job. The interval is a tradeoff.

**Why use one CPU?** Interrupt disabling plus process locks makes selection and
sequence updates straightforward. Multiple CPUs require global synchronization,
candidate revalidation, and cross-core preemption; changing CPUS alone is unsafe.

**Does a process with zero CPU ticks do no work?** No. It may execute entirely
between sample interrupts. Wakeup latency is measured at a finer resolution.

**Is `dispatches` the number of context switches?** It counts scheduler entries
into RUNNING. A process can be chosen again without another PID running in between.

**How is the baseline fair?** It keeps xv6's table-order selection and one-tick
timer yielding. It shares the same instrumentation, build settings, and workloads.
Work checksums are compared across policies.

## Common modifications

| Requested change | Where and what to change |
| --- | --- |
| Longer slices | `make QUANTA=2,4,8,16 qemu` |
| More/less frequent boosting | `make BOOST=32 qemu` or `BOOST=128` |
| Disable boosts | `make BOOST=0 qemu` |
| Start children at parent's priority | In `kfork`, copy only `ss.level` and possibly `ss.used`; leave lifetime counters zero |
| Reset priority on exec | Add a budget/level reset in `kexec`; explain the new exec-gaming loophole |
| Promote on every wakeup | In `wakeup`, reset level/budget; explain how this changes the anti-gaming rule |
| Several turns before demotion | Introduce a separate allotment counter; slice expiry rotates but allotment expiry demotes |
| Bottom queue uses FCFS | Change bottom-level expiry behavior; explain loss of bounded peer service |
| More queues | Change NQUEUE, quanta, and the four-column user output/tests; do not change only NQUEUE |
| Faster timer | Change both initial and recurring timer intervals in `start.c`/`trap.c`, rebuild both policies, rerun experiments |
| SMP | Redesign synchronization; the current single-CPU assumptions do not carry over |

Use `git -C xv6 diff` to review changes to upstream files and
`git -C xv6 status --short` to include newly added files in that review.
