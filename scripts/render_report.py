"""Generate the technical report from completed source-matching artifacts."""
import csv
import json
from benchmark import source_hash
from xv6 import ROOT


def summary(path):
    with (path/'summary.csv').open() as f:
        return {(int(r['cpus']),r['mode'],r['policy'],r['metric']):r for r in csv.DictReader(f)}


def main():
    base=ROOT/'results/v2'
    tests=json.loads((base/'validation/results.json').read_text())
    meta=json.loads((base/'benchmarks/metadata.json').read_text())
    platform=json.loads((base/'platform/verified.json').read_text())
    current=source_hash()
    if not tests.get('finished_utc') or any(c['status']!='passed' for c in tests['cases']):
        raise AssertionError('validation incomplete')
    if tests['source_sha256']!=current or meta['source_sha256']!=current or not meta.get('finished_utc'):
        raise AssertionError('benchmark/source provenance mismatch')
    cases=tests['cases']
    expected={(c,p) for c in [1,2,4,8] for p in ['DEFAULT','MLFQ']}
    if {(c['cpus'],c['policy']) for c in cases}!=expected:
        raise AssertionError('CPU/policy matrix incomplete')
    if any(c['command']!='usertests' for c in cases if c['name']=='upstream'):
        raise AssertionError('complete upstream suites required')
    data=summary(base/'benchmarks')
    def val(cpu,mode,policy,metric):
        return float(data[cpu,mode,policy,metric]['median'])
    def pair(cpu,mode,metric,digits=3):
        return ' | '.join(f'{val(cpu,mode,p,metric):.{digits}f}' for p in ['DEFAULT','MLFQ'])
    actions=sum(c.get('actions',0) for c in cases)
    text=f'''# Multicore MLFQ implementation and evaluation

## Implementation

Four shared FIFO priority queues use a single policy lock. Lifecycle/context
handoff uses the existing process locks. A peek-lock-revalidate claim protocol
prevents stale selections and duplicate CPU ownership. Only the process lock
crosses a context switch.

Execution is charged using elapsed shared-counter time across dispatch,
timers, blocking, yield, exit, and snapshots. Quanta rotate peers; separate
allotments control demotion. Partial and excess execution are retained.
Boosts close running intervals before resetting live budgets; sleepers remain
asleep. Runtime configuration is validated and atomic. Affinity can migrate a
caller before returning. Trace toggling preserves budgets and the boost deadline.

DEFAULT retains xv6's table-order per-CPU selection and timer yielding, with
the same accounting, timer, affinity checks, and invariant setting as MLFQ.
The comparison uses two instrumented policies rather than a pristine old binary.
[DESIGN.md](DESIGN.md) defines the synchronization and scheduling rules.

## Correctness evidence

The completed matrix has **{len(cases)} passing guest validation boots** across
both policies and 1, 2, 4, and 8 CPUs. It includes complete upstream suites,
seeded stress, targeted assertions, trace replay, and overflow checks.
Stress completed **{actions:,} top-level actions**, with additional checked work
in forked children. The independent replay validator passed
{tests['model_tests']} positive/negative self-tests.

| CPUs | Policy | Upstream tests | Seeded actions | Replay events |
| --- | --- | ---: | ---: | ---: |
'''
    for cpu in [1,2,4,8]:
        for policy in ['DEFAULT','MLFQ']:
            group=[c for c in cases if c['cpus']==cpu and c['policy']==policy]
            n=next(c['upstream_tests'] for c in group if c['name']=='upstream')
            events=next(c['events'] for c in group if c['name']=='trace')
            text+=f'| {cpu} | {policy} | {n} passed | {sum(c.get("actions",0) for c in group):,} | {events:,} |\n'
    extra_path=base/'validation-extra/results.json'
    if extra_path.exists():
        extra=json.loads(extra_path.read_text())
        if extra.get('passed')!=32 or extra['source_sha256']!=current:
            raise AssertionError('additional CPU coverage incomplete')
        text+='\nThe other supported CPU counts (3, 5, 6, and 7) passed 32 additional\nrule/stress/replay/overflow boots. Across both matrices, seeded top-level\nactions total '+f'{actions+extra["stress_actions"]:,}'+'.\n'
    text+='''
Assertions cover exact CPU-by-level/core and process state-time conservation,
budget persistence, quanta/allotments, demotion, parallel ownership, affinity,
migration, fairness, preemption, boosts, inheritance, exec, slot reuse,
kill/sleep races, and concurrent configuration. Trace replay checks each
priority/FIFO choice and exact execution interval. Negative inputs exercise
wrong ordering, ownership, affinity, budgets, timestamps, and missing records.
Trace dumps freeze capture before console printing to prevent self-overwrite.

Raw logs and source/kernel hashes are retained in `results/v2/validation/`.
Expected invalid-memory/out-of-space messages in upstream tests are followed
by their normal passing verdicts.

## Measurement protocol
'''
    text+=f'''\nPlatform: {meta['qemu']}; {meta['compiler']}; {meta['host']}.
Host CPU: {platform.get('host_cpu_model','unrecorded')};
{platform.get('host_available_cpus','unrecorded')} logical CPUs available to the runner.
The device tree independently verifies a {platform['timebase_hz']:,} Hz clock.
The timer interval is {meta['timer_interval']:,} units, nominally 10 ms.
Automatic invariant scans are enabled in both builds.

The main matrix uses {meta['repeats']} repetitions of six workloads, two
policies, and four CPU counts: **{meta['repeats']*48} independent benchmark
boots**. Worker count is `max(4,2*CPUs)`. Each fixed work unit performs 100,000
dependent arithmetic iterations. Fixed-work checksums must match.

CPU, mixed, short-job, pipe, phase-change, and fixed-window fairness workloads
use a common release barrier. Timed statistics exclude pre-barrier work.
Completed workers park until all timed work ends, then serialize their results.
Each boot uses independent kernel/disk copies. Policy order alternates across
repetitions. Trace capture is disabled for performance measurements.

Analysis first computes per-run metrics, then reports medians and observed
min/max ranges. Error bars are not confidence intervals. Workers within one
guest are not treated as independent repetitions. CPU residency includes
kernel/lock time while dispatched; it is not host-thread CPU time.

## Responsiveness

Wakeup delay uses only observations within the timed phase. Pipe message
latency is separately retained from send timestamp to read receipt.

| CPUs | Mixed DEFAULT ms | MLFQ ms | Pipe DEFAULT ms | MLFQ ms |
| --- | ---: | ---: | ---: | ---: |
'''
    for cpu in [1,2,4,8]:
        text+=f'| {cpu} | {pair(cpu,"mixed","special_wake_delay_ms")} | {pair(cpu,"pipe","special_wake_delay_ms")} |\n'
    text+='''
![Wakeup delay](../results/v2/benchmarks/figures/wakeup_latency.png)

## CPU completion and fairness

Dispatch spans include the initial turn containing measured work and subsequent
dispatches. They are an event-count proxy, not scheduler instruction overhead.

| CPUs | CPU turnaround DEFAULT ms | MLFQ ms | Dispatch spans DEFAULT | MLFQ |
| --- | ---: | ---: | ---: | ---: |
'''
    for cpu in [1,2,4,8]:
        text+=f'| {cpu} | {pair(cpu,"cpu","mean_turnaround_ms")} | {pair(cpu,"cpu","dispatch_spans",1)} |\n'
    text+='''
![CPU execution](../results/v2/benchmarks/figures/cpu_execution.png)

| CPUs | Jain fairness DEFAULT | MLFQ | Short-job DEFAULT ms | MLFQ ms |
| --- | ---: | ---: | ---: | ---: |
'''
    for cpu in [1,2,4,8]:
        text+=f'| {cpu} | {pair(cpu,"fair","jain_fairness",4)} | {pair(cpu,"short","special_turnaround_ms")} |\n'
    overshoot=max(float(row['maximum']) for key,row in data.items()
                  if key[1]=='fair' and key[3]=='fair_stop_overshoot_ms')
    text+=f'\nFair workers check the shared deadline between work chunks. The largest\nfinish-checkpoint overshoot was {overshoot:.3f} ms. Reported service extends\nthrough each stop checkpoint rather than clipping exact trace intervals.\n'
    text+='''
![Turnaround and fairness](../results/v2/benchmarks/figures/turnaround_and_fairness.png)

Fairness uses identical workers over a common window, not final fixed-work
service totals. Short-job behavior is reported separately from workload means.
Longer peer turns, feedback classification, boosts, and idle CPU availability
produce tradeoffs; MLFQ is not guaranteed to win every metric.

## Fixed-work scaling

The main matrix changes worker count with CPU count. A separate experiment
holds eight workers and their arithmetic input fixed, with three repetitions.

| CPUs | Makespan DEFAULT ms | MLFQ ms | DEFAULT speedup | MLFQ speedup |
| --- | ---: | ---: | ---: | ---: |
'''
    scaling=summary(base/'scaling')
    single=[float(scaling[1,'cpu',p,'makespan_ms']['median']) for p in ['DEFAULT','MLFQ']]
    for cpu in [1,2,4,8]:
        values=[float(scaling[cpu,'cpu',p,'makespan_ms']['median']) for p in ['DEFAULT','MLFQ']]
        text+=f'| {cpu} | {values[0]:.3f} | {values[1]:.3f} | {single[0]/values[0]:.2f}x | {single[1]/values[1]:.2f}x |\n'
    text+='''
Speedup divides each policy's single-core median makespan by its multicore
median. Ideal linear scaling would equal the CPU count; shared locks and
emulation can limit it.

## Parameter sensitivity

Additional four-core profiles use three repetitions of mixed and phase-change
workloads under both policies. The table shows MLFQ; matched DEFAULT records
are retained. Default measurements come from the main matrix.

| Profile | Quanta | Allotments | Boost | Mixed wake ms | Phase turnaround ms |
| --- | --- | --- | ---: | ---: | ---: |
'''
    text+=f'| Default | 1,2,4,8 | 2,4,8,16 | 100 | {val(4,"mixed","MLFQ","special_wake_delay_ms"):.3f} | {val(4,"phase","MLFQ","special_turnaround_ms"):.3f} |\n'
    for name in ['short_slices','long_slices','no_boost']:
        folder=base/'sensitivity'/name
        config=json.loads((folder/'metadata.json').read_text())
        sm=summary(folder)
        wake=float(sm[4,'mixed','MLFQ','special_wake_delay_ms']['median'])
        phase=float(sm[4,'phase','MLFQ','special_turnaround_ms']['median'])
        text+=f'| {name} | {config["quanta"]} | {config["allotments"]} | {config["boost"]} | {wake:.3f} | {phase:.3f} |\n'
    text+='''
This is a sensitivity study, not exhaustive tuning or a claim of optimal defaults.

## Controlled boost ablation

The same 40 CPU jobs are pre-created, verified sleeping, and released by one
bounded pipe write. Each requests at least 40 ms on CPU0. A preexisting Q3 job
competes with the burst. Its exact CHARGE intervals are clipped to a fixed
one-second window. Actual admission spread must remain below one timer period.

| Boost ticks | Jobs | Window ms | Old-job CPU ms | Admission spread ms |
| --- | ---: | ---: | ---: | ---: |
'''
    with (base/'pressure/summary.csv').open() as f:
        for r in csv.DictReader(f):
            text+=f'| {r["boost"]} | {r["arrivals"]} | {int(r["window"])/10000:.1f} | {int(r["old_cpu"])/10000:.3f} | {int(r["release_span"])/10000:.3f} |\n'
    text+='''
![Priority and service](../results/v2/pressure/priority_and_service.png)

Both traces pass independent replay. This is a bounded empirical progress
check, not a universal starvation or deadline guarantee.

## Limits and reproduction

Shared queues target eight CPUs and 64 slots. There are no rescheduling IPIs,
per-CPU work-stealing queues, or scheduling-group fairness. Synthetic arithmetic,
blocking, and pipe jobs do not cover arbitrary workloads or physical disk I/O.
Emulation, host load, and invariant instrumentation affect measured performance.
No unrecorded latency percentiles or significance claims are inferred.

The root README reproduces validation, evaluation, and source export.
[TESTING.md](TESTING.md) specifies controls and assertions. Current evidence
is under `results/v2/`; earlier single-core data is historical.

References: [MIT xv6-riscv](https://github.com/mit-pdos/xv6-riscv),
[OSTEP MLFQ](https://pages.cs.wisc.edu/~remzi/OSTEP/cpu-sched-mlfq.pdf).
'''
    (ROOT/'docs/REPORT.md').write_text(text)
    print('Report generated from verified current-source artifacts.')


if __name__=='__main__':
    main()
