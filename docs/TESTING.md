# Verification and reproducibility

## Validation layers

1. **Kernel invariants:** queue links are acyclic and consistent; runnable
   membership is unique and complete; running ownership is unique; affinity
   and budget ranges are valid; CPU totals agree across priorities and CPUs;
   finalized process state times conserve lifetime.
2. **Targeted guest assertions:** invalid API requests, error atomicity, safe
   reaping, short-burst accounting, quanta versus allotments, demotion, blocking,
   early yield, affinity, migration, parallel execution, fairness, preemption,
   repeated boosts, inheritance, exec, slot reuse, and configuration races.
3. **Full upstream suite:** both quick and slow xv6 tests, with an independent
   writable disk image for each boot.
4. **Seeded concurrent stress:** deterministic per-worker operation sequences
   combine fork, exec, kill/sleep races, pipe payload/EOF handling, snapshots,
   affinity changes, yield, blocking, and global reconfiguration.
5. **Independent trace replay:** a host model checks decisions and accounting
   against recorded events rather than accepting a guest success message alone.
6. **Validator negative tests:** deliberately incorrect priority, FIFO, CPU
   ownership, affinity, execution intervals, demotion, reinsertion, missing
   events, and incomplete initial snapshots must be rejected.
7. **Trace capacity/error checks:** overflow is visible, invalid capacities and
   future cursors fail, and bounded reads avoid kernel-stack overflow.

## Complete matrix

```bash
python3 scripts/run_tests.py
```

Defaults: policies DEFAULT and MLFQ; CPU counts 1, 2, 4, and 8; seeds
`1,7,19,73,101`; 12 workers; 100 top-level actions per worker. Every policy/CPU
combination gets nine fresh guest boots: rules, full upstream tests, five stress
runs, replay, and overflow. With all cases passing, this is 72 guest boots and
48,000 seeded top-level actions, plus the host model's negative/positive tests.
Forked children perform additional checked operations.

Results are recorded incrementally in `results/v2/validation/results.json`.
A failing assertion, kernel panic, timeout, missing completion marker, malformed
trace, or replay failure makes the runner fail. A returned shell prompt alone
is not a passing test. Logs and the kernel binary hash are retained per case.

For a shorter local run, use a different output directory:

```bash
python3 scripts/run_tests.py --cpus 4 --seeds 7 --quick --output results/local-check
python3 scripts/check_trace.py --cpus 4
python3 scripts/xv6.py --cpus 8 --command 'schedstress 73 300 16'
```

## Benchmark protocol

```bash
python3 scripts/benchmark.py
.venv/bin/python scripts/analyze.py results/v2/benchmarks
```

The default performance matrix has six workloads, both policies, four CPU
counts, and five repetitions: 240 independent boots. Worker count is
`max(4,2*CPUs)`, so policy comparisons at one CPU count share the same offered
work, while the main CPU-count plots represent varying load levels.

Use fixed workers for a separate strong-scaling comparison:

```bash
python3 scripts/benchmark.py --workers 8 --modes cpu --repeats 3 --output results/v2/scaling
.venv/bin/python scripts/analyze.py results/v2/scaling
```

Each guest has independent kernel/disk copies. Policy order alternates by
repetition. A start barrier records one release timestamp. Completed workers
park until all timed work ends; result records are then serialized to avoid
xv6 pipe-write interleaving. Performance runs disable trace recording. Temporary guest images use native
Linux temporary storage; editable sources can remain on the Windows mount.

Raw results must satisfy exact CPU-by-level, CPU-by-core, and timed-state
conservation. Fixed-work checksums must match across policies and repetitions.
Fair mode intentionally performs variable work in a fixed observation window,
so its checksum is not compared as if its instruction count were fixed. Workers
check the common deadline between work chunks. Service is measured through
each stop checkpoint; final-chunk and scheduling delays can extend the end.
The analysis retains maximum finish-checkpoint overshoot per run. This fairness
measurement is distinct from the exact trace clipping in the boost diagnostic.

Analysis groups workers into independent runs before calculating medians and
observed min/max ranges. It does not treat workers in the same guest as
independent repetitions or infer unrecorded latency percentiles.

## Controlled boost ablation

```bash
python3 scripts/diagnostics.py
.venv/bin/python scripts/plot_trace.py
```

Both configurations pre-create the same 40 CPU-bound jobs behind a barrier.
Each arrival requests at least 40 ms of accounted CPU time on CPU0. A preexisting
Q3 worker competes with that burst. Boosting is either disabled or set to 64
ticks. Trace enabling preserves the preexisting priority and deadline.

The host integrates the old worker's exact execution intervals clipped to one
fixed 1-second window. Observation/reaping can finish later without changing
the measured interval. Both arrival count and observation duration are fixed;
this replaces the earlier generator-dependent demonstration.

All arrivals must be sleeping at the barrier. Their observed runnable
admission spread is checked from the trace. Dumps freeze capture before
printing so observation cannot overwrite unread events.

The diagnostic checks that the trace is complete, replays it, verifies the
service bound, and checks whether the old worker makes progress with boosts.
The empirical outcome applies to this bounded offered load and is not a
universal starvation or deadline guarantee.

## Platform and source provenance

`python3 scripts/check_platform.py` reads QEMU's generated device tree and
verifies the 10 MHz timebase used for conversions. Metadata records compiler,
QEMU, CPU count, timer, policy parameters, invariant setting, source hashes,
and UTC collection timestamps. Event and measurement units remain raw guest
counter units until analysis.

The source snapshot used for a validation run is recorded separately from
individual compiled kernel hashes. Historical single-core results remain in
their old directories and are excluded from current performance conclusions.

## Source export verification

`python3 scripts/verify_release.py` requires complete current-source evidence,
checks the other CPU counts (3/5/6/7), validates local document links, and builds
and runs the scheduler tests from a clean source archive. It records the clean
binary hash and whether it matches the validated four-core kernel. The final
archive and its SHA-256 sidecar are regenerated after those checks.
