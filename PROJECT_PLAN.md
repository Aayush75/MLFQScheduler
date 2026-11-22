# Multicore MLFQ implementation roadmap

## Implementation scope

- Shared FIFO priority queues with atomic multicore ownership.
- Exact elapsed-time budget accounting across dispatch, block, yield, and exit.
- Independent quanta and allotments with excess-time carry-forward.
- Periodic global boosts, including sleeping processes without waking them.
- Runtime configuration with bounds and atomic rejection of invalid requests.
- CPU affinity, migration tracking, and final-child scheduling statistics.
- Bounded observational tracing and independent event replay.

## Verification sequence

1. Boot the new scheduler with automatic invariant scans enabled.
2. Check policy rules, state-time conservation, affinity, and lifecycle handling.
3. Run the complete upstream suite for both policies on 1/2/4/8 CPUs.
4. Exercise deterministic seeded fork/exec/kill/pipe/configuration stress.
5. Replay real kernel traces and test the validator using corrupted inputs.
6. Check trace overflow and invalid user-pointer/error paths.
7. Run repeated fixed-work, blocking, pipe, phase-change, and fairness workloads.
8. Compare boosts under identical pre-created load and a fixed observation window.
9. Retain raw logs, source/binary hashes, CSV summaries, and generated figures.
10. Package and verify a clean source-only rebuild.

The current source is under `xv6/`. Earlier single-core source is archived in
`reference/single-core.zip`; its measurements remain under the historical
results directories. Current validation and performance results use `results/v2/`.

## Design boundaries

The implementation targets xv6's 64 process slots and up to eight CPUs. Shared
queues favor a clear synchronization model over per-CPU queue scalability.
Preemption uses periodic local timers and safe kernel scheduling points. Runtime
measurements use QEMU's shared time counter and include dispatched kernel time.
Formal real-time guarantees and process-family/group fairness are outside scope.

See `docs/DESIGN.md`, `docs/TESTING.md`, and `docs/REPORT.md` for the implemented
rules, reproducible checks, and measured outcomes.
