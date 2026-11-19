# xv6 MLFQ scheduler: project plan

Author: Aayush Kuloor  
Course: CS 330 / Operating Systems  
Purpose: A− to A grade improvement project  
Status: initial planning draft, 19 November 2026. The implemented design is in
[docs/DESIGN.md](docs/DESIGN.md). Following the later request for simpler code,
the final implementation uses logical FIFO queues in the process table,
build-time configuration, and a user-space diagnostic trace.

## 1. Objective and scope

Implement a Multilevel Feedback Queue (MLFQ) scheduler in MIT's xv6-riscv and evaluate its behavior against xv6's default scheduler. The submission should demonstrate correct scheduling decisions, explain the kernel changes, and provide reproducible measurements for CPU-bound and blocking workloads.

Use MIT's xv6-riscv repository, as confirmed by Aayush. Pin the exact upstream commit before making changes. The OSTEP kernel-hacking scheduling project supplies useful context for changing xv6 and exporting scheduling information; its published scheduling assignment is lottery scheduling. The MLFQ policy for this project will be our own implementation based on the OSTEP MLFQ chapter. See references [1–3].

### Required deliverables

- Four priority queues with round-robin service within each queue.
- Dynamic demotion based on cumulative CPU consumption.
- Configurable queue time slices and boost interval.
- Periodic global priority boosting.
- Process-level scheduling statistics, available to user programs.
- Automated correctness tests and workload execution.
- A controlled comparison against the default xv6 scheduler.
- Source, setup instructions, raw results, analysis plots, report, and demonstration instructions.

The required implementation and reported evaluation will use **one emulated CPU** (`CPUS=1`). This avoids adding CPU migration, cross-core priority enforcement, and interprocessor preemption to the core project. The MLFQ build should reject unsupported CPU counts clearly. Supporting multiple CPUs is a possible extension after the required work passes.

## 2. Environment and installation

### What is already available

The project directory was empty at inspection. Windows has Git 2.53.0, Python 3.14.4, and `qemu-system-riscv64` 11.1.0 on PATH. `make` and a RISC-V GCC were not found on the Windows PATH. `wsl.exe` exists, but querying distributions returned access denied from this session, so the Linux installation and its packages are unverified.

### Recommended environment

Use **WSL 2 with Ubuntu 24.04 LTS** and install the build and execution tools inside Ubuntu. MIT recommends WSL 2 for Windows and documents the relevant RISC-V toolchain packages [4]. Windows QEMU does not establish that the WSL environment has its own working QEMU.

First run this in Windows PowerShell:

```powershell
wsl --list --verbose
```

If Ubuntu 24.04 is not installed, run the following in an administrator PowerShell, then restart if requested and launch Ubuntu to finish creating the Linux account [5]:

```powershell
wsl --install -d Ubuntu-24.04
```

If Ubuntu is already present, use it after confirming it is WSL version 2. There is no need to reinstall a working distribution.

Inside Ubuntu:

```bash
sudo apt update
sudo apt install -y git build-essential perl gdb-multiarch \
  qemu-system-misc gcc-riscv64-linux-gnu binutils-riscv64-linux-gnu \
  python3 python3-venv

qemu-system-riscv64 --version
riscv64-linux-gnu-gcc --version
make --version
```

The upstream Makefile currently requires QEMU 7.2 or newer [6]. Booting the pinned revision is the actual compatibility check.

Use a Python virtual environment for experiment automation and plotting:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install pexpect matplotlib
```

`pexpect` will drive xv6's serial console; `matplotlib` will generate result figures. Python's standard `csv` and `statistics` modules are sufficient for the initial analysis. GDB is for kernel debugging. An editor is optional; no IDE extension is required.

### Repository setup and first boot

Keep the existing project folder as the outer directory and put upstream source in an `xv6/` subdirectory. From Ubuntu, that outer directory is:

```bash
cd '/mnt/c/Users/Aayush/IITGN/CS 330/MLFQScheduler'
git clone https://github.com/mit-pdos/xv6-riscv.git xv6
cd xv6
git rev-parse HEAD
make CPUS=1 qemu
```

Record the SHA in the project documentation. Check shell startup and basic commands, then run the upstream user test suite and `forktest`. Exit QEMU with Ctrl-a followed by x. If building on the mounted Windows directory is slow or causes filesystem issues, move the active checkout to the Linux filesystem and explicitly agree on that workspace before implementation continues.

No software needs to be installed merely to review this plan. The first practical dependency is a working WSL/Linux build environment.

## 3. Scheduling policy

These are proposed project defaults, not requirements imposed by upstream xv6. Keep the rules in a short specification next to the code so tests and documentation use the same semantics.

### Queue configuration

| Queue | Priority | Quantum | CPU allotment before demotion |
| --- | --- | --- | --- |
| Q0 | Highest | 1 tick | 1 tick |
| Q1 | High | 2 ticks | 2 ticks |
| Q2 | Medium | 4 ticks | 4 ticks |
| Q3 | Lowest | 8 ticks | No further demotion; rotate every 8 ticks |

Start with a global boost every **64 ticks**. A quantum controls rotation among peers; an allotment controls demotion. In the first version each demoting queue has one quantum per allotment, but keep those concepts separate in the implementation and explanation.

Use guest timer ticks as the policy unit. Verify and record the pinned kernel's timer interval instead of assuming a tick is 10 ms. The currently inspected upstream timer comments describe an interval of approximately 100 ms [7]; the proposed defaults therefore correspond to roughly 100/200/400/800 ms slices and a 6.4-second boost on that configuration. Keep the timer unchanged between policies. A shorter timer is an optional, separately evaluated configuration.

### Exact behavior

1. **Selection:** Always select from the highest-priority nonempty runnable queue. Serve equal-priority processes in FIFO round-robin order.
2. **Creation:** New processes enter Q0 with fresh scheduling budgets and zero lifetime statistics. A forked child follows this rule. Fork-based priority gaming is outside the core guarantee and must be disclosed.
3. **CPU charging:** Charge CPU ticks while a process is running, including execution in kernel mode on its behalf. Do not charge sleeping or merely runnable processes. Account for each timer interrupt exactly once.
4. **Demotion:** When a process exhausts its cumulative allotment at Q0, Q1, or Q2, move it to the tail of the next queue and initialize that level's budgets. At Q3, stay there and rotate to the tail on quantum expiry.
5. **Blocking:** Sleeping does not itself promote a process. Preserve consumed allotment and remaining quantum across sleep/wakeup; waking appends it to its current queue. Genuine blocking workloads consume CPU slowly and therefore tend to stay at high priority longer.
6. **Early yield:** Preserve consumed allotment and remaining quantum; place the process at its current queue's tail. Repeated yielding must not restore its CPU budget. This guarantee applies to the chosen tick accounting resolution; sub-tick execution can still be undercounted.
7. **Higher-priority arrival:** Check whether higher-priority work is ready on each timer interrupt and preempt at the next safe scheduling boundary. Preserve the interrupted process's remaining quantum and return it to the front of its level. External-device wakeups may therefore wait up to the next tick, plus interrupt-disabled kernel time. Do not promise immediate interrupt-level preemption.
8. **Boost:** Every 64 ticks, reset priority and scheduling budgets for all live schedulable processes, including sleeping processes and the running process. Sleeping processes remain asleep. Runnable processes move into Q0 in a documented stable order: existing Q0 order, then Q1, Q2, and Q3 order; append the previously running process after these at the boost scheduling point. Resume it with a fresh Q0 budget if no other work is ready. Do not reset lifetime statistics.
9. **Exec and cleanup:** `exec` retains scheduling history and budgets. Exit removes the process from scheduling eligibility. Reusing a process-table slot resets every scheduling field.

If a boost and quantum expiry occur on the same tick, first charge execution to the old level, then apply the boost; the fresh budget supersedes demotion. Use a pending boost/epoch mechanism serviced at a safe scheduler boundary. Complete it before the next dispatch. Keep the boost deadline advancing even when the system was idle.

Frequent boosts can impede demotion. A boost interval must be considered alongside the quantum table; the default is deliberately longer than the 7 ticks needed to reach Q3 and the 15 ticks needed for a complete first descent plus one bottom-level quantum.

## 4. Kernel implementation strategy

### Read the relevant code first

Trace process creation, runnable transitions, timer handling in both user and kernel mode, context switching, sleep/wakeup, kill, exit, and wait. Upstream xv6 has changed APIs over time: the current source, for example, uses split sleep preparation and sleeping functions [8]. Work against the pinned revision and do not paste older x86 lab code into RISC-V xv6.

### Baseline and policy selection

Preserve a clean upstream revision. Add a build-time policy selector, for example `SCHED=DEFAULT` and `SCHED=MLFQ`, with clean rebuilds or separate output directories when changing policies. The default build retains upstream process selection and timer-yield behavior. Both evaluated builds share the same statistics instrumentation and workload programs, with tracing disabled.

First verify that an instrumented default build passes the same basic tests as pristine upstream. This separates instrumentation mistakes from MLFQ mistakes.

### Process state

Add fields for:

- Priority, remaining quantum, cumulative CPU charged at the current level, and queue membership.
- Total charged CPU ticks and ticks at each priority level.
- Dispatches, actual process-to-process switches as a separately defined global counter, demotions, and boosts.
- Creation, first dispatch, exit, and most recent runnable timestamps.
- Accumulated runnable wait time, maximum continuous runnable wait, and sleeping time.
- Optional run-start timestamps if finer time accounting is added later.

Use wide counters for exported statistics. Keep lifetime statistics separate from resettable scheduling budgets. Define behavior for timestamp wraparound and for a process killed before its first dispatch.

### Queue representation

For the first policy prototype, logical queues selected by a process-table scan can verify charging and demotion. The final core implementation should have four explicit FIFO run queues, implemented with fixed-size storage or intrusive links; no dynamic allocation is needed in scheduler paths.

Centralize queue operations and runnable transitions. Audit every path that creates runnable work: initial user process, fork, timer preemption, yield, wakeup, and waking a killed sleeper. Only RUNNABLE processes belong to a run queue. RUNNING processes are removed until they return to runnable state.

Required invariants:

- Every runnable process appears exactly once in its assigned queue.
- No running, sleeping, zombie, unused, or partially initialized process is queued.
- Process priority matches queue membership.
- Budgets remain in range and never wrap below zero.
- Boosting and slot reuse cannot leave duplicate or stale entries.

### Locking and timer safety

Write down a lock order before introducing the queue lock. A viable design acquires a process lock before the queue lock for membership changes. Scheduler selection can peek under the queue lock, release it, acquire the process lock, reacquire the queue lock, and revalidate before removal. Do not hold a queue lock while waiting for a process lock.

Respect xv6's context-switch contract: enter `sched()` with only the appropriate process lock held. Never hold the queue lock across `swtch()`. Preserve interrupt state and the idle path. Audit interactions with `wait_lock` and `tickslock`; existing timer wakeups already acquire process locks, so acquiring `tickslock` while holding a process lock can introduce inversion. Pass captured timestamps into accounting hooks or use a documented safe clock-read mechanism.

Charge ticks in a single shared timer-accounting hook. Invoke it consistently from user-mode and kernel-mode timer paths without double charging. Separate deciding that rescheduling is needed from actually yielding at a safe point. Implement global boost work outside a timer path that holds unrelated locks.

### Likely file changes

| File or area | Planned responsibility |
| --- | --- |
| `kernel/proc.h`, `kernel/proc.c` | Scheduling state, lifecycle hooks, selection, queues, wait statistics |
| `kernel/trap.c` | Timer charging and preemption decisions |
| `kernel/sched.h` / `kernel/sched.c` | Proposed policy constants, helpers, and queue operations if useful |
| `kernel/schedstat.h` | Shared statistics structures and ABI version |
| `kernel/sysproc.c`, `kernel/syscall.c`, `kernel/syscall.h`, `kernel/defs.h` | Statistics syscall implementations and registration |
| `user/user.h`, `user/usys.pl` | User declarations and syscall stubs |
| `user/schedstat.c` | Human-readable process statistics |
| `user/schedtest.c`, `user/schedbench.c` | Correctness tests and workloads |
| `Makefile` | Build selector, policy parameters, new user programs |
| Host-side scripts | QEMU automation, CSV extraction, analysis |

Final helper names and file boundaries follow the pinned source rather than being fixed by this draft.

## 5. Configuration and observability

### Configuration

Use compile-time configuration first: four quantum values and a boost interval in one header, exposed through validated build options if useful. This satisfies configurable time slices while making each experiment's configuration reproducible. Record all values in boot or benchmark metadata. Reject zero/negative quanta, invalid queue counts, and unsupported CPU counts.

Changing policy or parameters requires a clean rebuild. A runtime `setschedconfig()` syscall is a stretch goal; it introduces live-budget update and synchronization questions that the core project does not need.

### Statistics API

Propose two additions, preserving existing `wait()` behavior:

- `getschedstat(buffer, capacity)`: copy bounded per-process records to user space and return the number copied. Records contain PID, state, level, charged ticks, wait time, dispatches, demotions, boosts, and timestamps. Include sampling time and an ABI version. A table-wide result is a sequence of individually consistent records, not a promise of one atomic global snapshot.
- `waitstats(status, stats)`: reap a child while returning its finalized scheduling summary, captured before its process slot is freed. Final exit statistics are necessary because polling can miss short-lived children or lose data after reaping. Invalid copyout must return an error without silently reaping the child.

Copy one bounded record at a time through a small kernel buffer. Avoid a large `NPROC`-sized array on xv6's small kernel stack. Follow the pinned version's user-pointer validation and `copyout` interfaces, handle malformed sizes and arithmetic overflow, and export no kernel pointers.

For precise ordering tests, use a bounded in-memory debug trace containing timestamp, PID, event, old/new queue, and budget. Report trace overflow explicitly. Printing on each tick would perturb scheduling substantially; dump traces after a test or in a separate debug build. Record observer/monitoring overhead and use the same instrumentation for both scheduler policies.

## 6. Correctness and regression tests

Automate QEMU boot, command execution, timeouts, panic detection, and exit. Separate finite rule tests from performance experiments.

| Test | Expected evidence |
| --- | --- |
| Clean boot, shell, basic utilities | Both policies remain usable |
| New child | First eligible queue is Q0; counters start at zero |
| One long CPU job, boosts disabled | Q0 → Q1 → Q2 → Q3 at the specified charged CPU thresholds |
| Multiple jobs at one level | FIFO round-robin rotation and configured quanta |
| Higher-priority wakeup | Lower-level execution gives way at the documented next safe tick boundary |
| Two timer paths | User and kernel execution are charged once, with no duplicated ticks |
| Sleep before budget exhaustion | Wakeup retains the remaining budget and used allotment |
| Frequent early blocking/yielding | Accumulated charged CPU still eventually causes demotion |
| Bottom queue | Processes keep running and rotate without budget underflow |
| Global boost | Runnable queues merge correctly; a sleeper is boosted without being awakened |
| Boost/expiry coincidence | Charged tick is counted once; boost precedence matches the specification |
| Sustained high-priority arrivals | A continuously runnable low-level process makes measurable progress across boosts |
| CPU-to-blocking phase change | Behavior adapts after subsequent boosts |
| Kill of sleeping/runnable child | Correct enqueue/cleanup and no duplicate queue membership |
| Fork, exec, exit, repeated PID-slot reuse | No stale queue entries or inherited lifetime counters |
| Invalid statistics arguments | Clean errors, no panic, no accidental child reaping |
| All processes blocked / idle | Timer wakeup works; no idle deadlock or busy-loop regression |
| Upstream `usertests` and `forktest` | No process, synchronization, or filesystem regressions |

For quantum tests, use a sufficiently long or disabled boost interval so boosts do not alter the expected trace. Timer-based observations allow documented boundary tolerances. A test-only yield syscall may be added behind a debug option if the pinned user ABI has no voluntary yield primitive.

Single-CPU accounting sanity checks should show that total process charged ticks do not exceed elapsed guest ticks, apart from explicitly defined start/end snapshot boundaries. Per-level CPU ticks should sum to total CPU ticks. Sleeping time must not appear as runnable wait. Dispatches must not be mislabeled as actual switches between distinct processes.

Boosting improves progress under the bounded workloads we test. State assumptions before claiming a starvation bound: a finite process table, FIFO ordering after boost, bounded quanta, and bounded nonpreemptible kernel sections. Test several boost periods; a single observed dispatch is weak evidence.

## 7. Evaluation design

### Workloads

1. **CPU only:** 1, 2, 4, and 8 workers performing fixed amounts of arithmetic with a consumed checksum so work is not optimized away. Compare completion, fairness, and dispatch overhead.
2. **Short and long CPU jobs:** Mix short jobs with longer jobs and include staggered arrivals. Measure first response and turnaround by job class.
3. **Periodic blocking:** Alternate a fixed CPU burst with a timed sleep/pause. Label this an I/O-like blocking proxy, not a disk benchmark. Calibrate bursts so at least some execution crosses tick boundaries and the accounting behavior is visible.
4. **Actual blocking I/O:** A pipe reader blocks awaiting periodic messages from a producer while background CPU jobs run. Measure reader wakeup-to-dispatch latency from kernel transition timestamps; record send-to-read latency separately because producer scheduling also affects it.
5. **Mixed workload:** Combine CPU workers, periodic blockers, and pipe activity. This is the main responsiveness comparison.
6. **Boost and starvation scenario:** Keep an older CPU worker at low priority while controlled high-priority work arrives; compare boosting enabled and disabled over a fixed observation window.
7. **Phase change:** Run CPU work first, then alternate CPU and blocking. Plot priority and service over time to show recovery.

Disk experiments are optional because caching and the virtual disk complicate reproducibility. Avoid console output inside timed workload loops.

### Metrics and definitions

| Metric | Definition |
| --- | --- |
| Initial response | First dispatch minus first runnable timestamp |
| Turnaround | Exit time minus workload release time; report process-lifetime turnaround separately if used |
| Runnable wait | Sum of time in RUNNABLE state |
| Wakeup latency | First dispatch after a sleeping-to-runnable transition minus that transition's timestamp |
| CPU service | Charged execution ticks, total and by queue |
| Throughput | Fixed-work jobs completed per guest-time interval |
| CPU fairness | Jain's index of CPU service among identical continuously runnable workers over a common window |
| Dispatch overhead proxy | Dispatch count per elapsed guest tick; does not by itself measure scheduler execution time |
| Progress under contention | CPU ticks received and maximum continuous runnable wait across boost epochs |

Use guest-time units for scheduler comparisons and label resolution. Sub-tick latencies may quantize to zero; if that masks the result, add higher-resolution guest timer timestamps before making latency claims. Host wall time is supplementary and depends on host load and QEMU.

### Experimental controls

- Compare default and MLFQ on the same pinned code, instrumentation, CPU count, timer interval, memory, toolchain, QEMU settings, and workload inputs.
- Use pipes as a start barrier so children begin the measured phase together. Record actual release timestamps.
- Use fixed computational work to compare completion time. Use a separate fixed-duration experiment for service-share fairness; do not conflate the two.
- Define what happens to blocked workers when a fixed-duration run ends. Mark intentionally terminated workers and retain their final statistics.
- Start comparable runs with a fresh boot and a recorded boost phase, or record time until next boost. Reset filesystem state for any stateful workload.
- Record all worker PIDs and exclude shell, monitor, and harness processes from per-workload aggregates while disclosing their presence.
- Start with at least 10 repeated runs per selected workload/policy configuration. Alternate policy order. Retain raw per-run samples and report median and spread; include a tail-latency percentile only with enough wakeup samples.
- Run a small default-vs-MLFQ smoke experiment before launching the full matrix.
- Explore quantum tables `[1,2,4,8]` and `[2,4,8,16]`, with boost intervals 32, 64, and 128 ticks on the mixed workload. Include a boost-disabled diagnostic. Keep the full workload matrix on the chosen default to control execution time.
- Do not tune parameters only on the final reported examples. Separate tuning runs from the final evaluation and explain the chosen configuration.

### Results to produce

- Priority timeline showing CPU-job demotion and periodic boosts.
- CPU service over time by process and/or queue.
- Turnaround comparison by workload and job class.
- Initial response and wakeup-latency distributions.
- Maximum runnable wait/progress with boosts enabled versus disabled.
- A small sensitivity table for quantum and boost choices.

The hypothesis is that MLFQ improves responsiveness for short or blocking work in mixed workloads, potentially at a cost to long CPU jobs and dispatch overhead. Results may show tradeoffs or regressions. Success requires correct implementation and sound evaluation, not a predetermined percentage speedup.

## 8. Implementation sequence and completion gates

| Stage | Work | Gate before proceeding |
| --- | --- | --- |
| 1. Establish baseline | Install/check tools, pin xv6, boot, run upstream tests, save configuration | Clean reproducible upstream boot and tests |
| 2. Specify and instrument | Finalize policy and lock order; add statistics and final-child reporting to default scheduler | Metrics survive process lifecycle; instrumented default passes tests |
| 3. Implement policy | Add priority selection, CPU budgets, timer decisions, and default/MLFQ build selector | Demotion and quantum traces match specification |
| 4. Complete queues and boosts | Explicit FIFOs, all runnable transitions, global boosts, lifecycle cleanup | Invariants and rule tests pass, including sleepers and slot reuse |
| 5. Automate workloads | Serial harness, CPU/blocking/pipe tests, CSV collection | One repeatable end-to-end comparison with complete metadata |
| 6. Evaluate | Repeated runs, boost ablation, small sensitivity study, plots | Raw data, analysis scripts, and figures agree |
| 7. Package and explain | Final regressions, README, report, short demo, limitations | Clean rebuild reproduces the documented demo and key results |

Planning estimate: roughly **25–40 focused hours** after a working toolchain, depending on familiarity with xv6 and synchronization issues. This is a budget estimate, not a deadline commitment. For the fastest credible delivery, complete the required single-CPU version and keep extensions out until the core gates pass.

Use small commits around these gates. Keep a known-good bootable revision while editing scheduler and timer code.

## 9. Planned project layout

```text
MLFQScheduler/
  PROJECT_PLAN.md
  README.md                 # setup, build, run, reproduce
  xv6/                      # pinned upstream source and implementation
  scripts/
    run_tests.py
    run_benchmarks.py
    analyze_results.py
  results/
    raw/                    # serial logs, CSV, run metadata
    figures/
  docs/
    design.md               # policy, state transitions, locking
    report.md
    demo.md
```

Decide the final Git arrangement when initializing source: one project repository with imported xv6 source is simplest for a course hand-in; a separately tracked xv6 checkout is also workable if its commit/patch and outer scripts are submitted together. Do not accidentally leave source changes only inside an untracked nested repository.

## 10. Report and demonstration

The report should contain the problem and scope; OSTEP policy basis; our exact rules; kernel data structures and synchronization; statistics definitions; correctness evidence; benchmark methodology; results and tradeoffs; limitations; reproduction steps; and references.

Suggested short demonstration:

1. Boot MLFQ and show its configuration.
2. Start CPU workers and show demotion and per-queue CPU statistics.
3. Add a blocking worker and show its service among the CPU workers.
4. Show a trace of a global boost and low-priority progress.
5. Show the same mixed-workload comparison under the default scheduler.
6. Present two or three plots and explain one tradeoff and one limitation.

The student should be able to explain why budgets survive sleeps, how timer ticks differ from a multi-tick quantum, how queues stay consistent, why boosting helps, what exactly each metric measures, and why one CPU was chosen.

## 11. Risks and optional extensions

| Risk | Mitigation |
| --- | --- |
| Kernel deadlock from new queue synchronization | Document lock order; preserve `sched()` contract; debug traces and short lifecycle stress tests |
| Reordering errors at sleep/wakeup or boost | Centralized queue operations, explicit membership flags, debug invariant checks |
| Incorrect multi-tick slice | Let a process continue across ticks until expiry or higher-priority readiness; test exact ordering |
| Tick accounting misses short bursts | Disclose resolution, calibrate workloads, consider higher-resolution accounting if needed |
| Boosts fail to improve progress | Test finite contention over multiple epochs and inspect stable FIFO boost ordering |
| Lost statistics after exit | Finalized summaries through `waitstats()` before reaping |
| Instrumentation distorts results | Shared instrumentation across policies; no hot-path printing; separate trace builds |
| Baseline silently changes | Preserve original default selection and verify against the pristine checkpoint |
| Architecture/API mismatch | Pin xv6-riscv and use its source and documentation |
| Time pressure | Complete required single-CPU project before optional features |

Optional extensions, in priority order: higher-resolution CPU accounting; configurable allotments spanning several quanta; runtime configuration; a comparison of global boosting with per-process aging; multiprocessor support. No claim of resistance to arbitrary fork-based or sub-tick scheduler gaming is part of the initial scope.

## 12. Immediate next steps

1. Confirm that Ubuntu launches under WSL 2 and install the Linux packages above if missing.
2. Clone and pin MIT xv6-riscv, then establish the clean baseline.
3. Freeze the proposed policy after checking the pinned timer and process APIs.
4. Implement statistics and the instrumented default baseline before changing selection.

## References

1. [MIT xv6-riscv source](https://github.com/mit-pdos/xv6-riscv).
2. [OSTEP chapter 8: Multilevel Feedback Queue](https://pages.cs.wisc.edu/~remzi/OSTEP/cpu-sched-mlfq.pdf).
3. [OSTEP xv6 scheduling project: lottery scheduler](https://github.com/remzi-arpacidusseau/ostep-projects/tree/master/scheduling-xv6-lottery).
4. [MIT toolchain and Windows/WSL setup guidance](https://pdos.csail.mit.edu/6.S081/2024/tools.html).
5. [Microsoft WSL installation instructions](https://learn.microsoft.com/en-us/windows/wsl/install).
6. [Upstream xv6-riscv Makefile](https://github.com/mit-pdos/xv6-riscv/blob/riscv/Makefile).
7. [Upstream xv6-riscv timer and trap handling](https://github.com/mit-pdos/xv6-riscv/blob/riscv/kernel/trap.c).
8. [Upstream xv6-riscv process and scheduler implementation](https://github.com/mit-pdos/xv6-riscv/blob/riscv/kernel/proc.c).

Web references were checked on 19 November 2026. Upstream links are moving references; implementation and experiments must record an exact commit SHA.
