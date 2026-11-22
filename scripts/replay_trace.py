"""Independently replay kernel events: queues, CPU ownership, and CPU budgets."""

import argparse
from collections import Counter, deque
import csv
from dataclasses import dataclass
import json
from pathlib import Path

UNUSED, USED, SLEEPING, RUNNABLE, RUNNING, ZOMBIE = range(6)
BEGIN, SEED, ALLOC, FORK, READY, DISPATCH, CHARGE, STOP, FREE, BOOST, CONFIG, AFFINITY = range(1, 13)
CREATE, WAKE, TIMER, YIELD, SLEEP, EXIT, AFF = range(7)


@dataclass
class Process:
    state: int
    affinity: int
    level: int = 0
    slice: int = 0
    allot: int = 0
    rotate: bool = False
    reason: int = -1
    last_cpu: int = -1
    run_at: int = 0


class Model:
    def __init__(self, cpus=4, timer=100000, policy=True):
        self.cpus, self.timer, self.policy = cpus, timer, policy
        self.processes = {}
        self.queues = [deque() for _ in range(4)]
        self.running = {}
        self.quantum = [1, 2, 4, 8]
        self.allotment = [2, 4, 8, 16]
        self.inherit = True
        self.seq = self.time = None
        self.counts = Counter()
        self.cpus_seen = set()

    def expect(self, condition, message):
        if not condition:
            raise AssertionError(f"event {self.seq}: {message}")

    def budgets_match(self, p, e):
        self.expect((p.level, p.slice, p.allot) == (e['level'], e['slice'], e['allot']),
                    f"budget mismatch: expected {(p.level,p.slice,p.allot)}, "
                    f"got {(e['level'],e['slice'],e['allot'])}")

    def reset_budgets(self):
        merged = deque(pid for queue in self.queues for pid in queue)
        self.queues = [merged, deque(), deque(), deque()]
        for p in self.processes.values():
            if p.state != ZOMBIE:
                p.level = p.slice = p.allot = 0
                p.rotate = True

    def advance_budget(self, p, elapsed):
        if not self.policy:
            return
        # Reference arithmetic consumes the remaining allotment at each level.
        # Slice position is the remainder of elapsed service within a quantum.
        # No knowledge of the kernel's queue/link implementation is used here.
        while elapsed:
            q = p.level
            quantum = self.quantum[q] * self.timer
            allotment = self.allotment[q] * self.timer
            available = elapsed if q == 3 else min(elapsed, allotment - p.allot)
            if p.slice + available >= quantum:
                p.rotate = True
            p.slice = (p.slice + available) % quantum
            p.allot += available
            elapsed -= available
            if q == 3:
                p.allot %= allotment
            elif p.allot == allotment:
                p.level += 1
                p.slice = p.allot = 0
                p.rotate = True

    def feed(self, e):
        kind, pid, cpu = e['type'], e['pid'], e['cpu']
        if self.seq is None:
            self.expect(kind == BEGIN, "trace has no initial snapshot (possibly overwritten)")
        else:
            self.expect(e['seq'] == self.seq + 1, "missing or reordered trace event")
            self.expect(e['time'] >= self.time, "non-monotonic timestamp")
        self.seq, self.time = e['seq'], e['time']
        self.counts[kind] += 1
        if kind == BEGIN:
            self.processes.clear()
            self.queues = [deque() for _ in range(4)]
            self.running.clear()
            return
        if kind == CONFIG:
            self.quantum = [(e['slice'] >> (16 * q)) & 65535 for q in range(4)]
            self.allotment = [(e['allot'] >> (16 * q)) & 65535 for q in range(4)]
            self.expect(all(self.quantum), "invalid trace configuration")
            self.inherit = bool(e['detail'] & 1)
            if e['detail'] & 2:
                self.reset_budgets()
            return
        if kind == BOOST:
            self.reset_budgets()
            return
        if kind in {ALLOC, SEED}:
            self.expect(pid not in self.processes, "duplicate allocation/seed")
            state = USED if kind == ALLOC else e['detail'] & 255
            p = Process(state, e['amount'], e['level'], e['slice'], e['allot'])
            p.rotate = bool(e['detail'] & 256) if kind == SEED else False
            self.processes[pid] = p
            if state == RUNNING:
                self.expect(cpu not in self.running and 0 <= cpu < self.cpus,
                            "duplicate/invalid seeded CPU owner")
                self.running[cpu] = pid
                p.last_cpu = cpu
                p.run_at = e['time']
            return
        self.expect(pid in self.processes, f"unknown PID {pid}")
        p = self.processes[pid]
        if kind == FORK:
            self.expect(e['detail'] in self.processes, "unknown fork parent")
            parent = self.processes[e['detail']]
            p.affinity = parent.affinity
            if self.policy and self.inherit:
                p.level, p.slice, p.allot = parent.level, parent.slice, parent.allot
            self.expect(e['amount'] == p.affinity, "fork affinity not inherited")
            self.budgets_match(p, e)
        elif kind == READY:
            self.expect(all(pid not in queue for queue in self.queues), "duplicate ready entry")
            self.expect(pid not in self.running.values(), "running process was enqueued")
            self.budgets_match(p, e)
            if p.reason >= 0:
                expected_front = p.reason == TIMER and not p.rotate
                self.expect(bool(e['amount']) == expected_front, "wrong partial-turn reinsertion")
            else:
                self.expect(e['amount'] == 0, "new/woken work must join the tail")
            p.state = RUNNABLE
            # A READY event consumes the preceding STOP reason.
            p.reason = -1
            if e['amount']:
                self.queues[p.level].appendleft(pid)
            else:
                self.queues[p.level].append(pid)
        elif kind == DISPATCH:
            self.expect(0 <= cpu < self.cpus and cpu not in self.running, "CPU already occupied")
            self.expect(pid not in self.running.values(), "same process running on two CPUs")
            self.expect(p.state == RUNNABLE and pid in self.queues[p.level], "dispatch non-runnable PID")
            self.expect(p.affinity & (1 << cpu), "dispatch violates affinity")
            if self.policy:
                eligible = [candidate for queue in self.queues for candidate in queue
                            if self.processes[candidate].affinity & (1 << cpu)]
                self.expect(eligible and eligible[0] == pid, "priority/FIFO selection violation")
            self.queues[p.level].remove(pid)
            p.state, p.rotate = RUNNING, False
            self.running[cpu] = pid
            if p.last_cpu >= 0 and p.last_cpu != cpu:
                self.counts['migrations'] += 1
            p.last_cpu = cpu
            p.run_at = e['time']
            self.cpus_seen.add(cpu)
            self.budgets_match(p, e)
        elif kind == CHARGE:
            self.expect(self.running.get(cpu) == pid and p.state == RUNNING,
                        "charging a process without CPU ownership")
            self.expect(e['detail'] == p.level and e['amount'] > 0, "wrong old level or elapsed time")
            self.expect(e['amount'] == e['time'] - p.run_at, "execution interval was lost or double-counted")
            p.run_at = e['time']
            self.advance_budget(p, e['amount'])
            self.budgets_match(p, e)
            self.counts['execution_time'] += e['amount']
        elif kind == STOP:
            self.expect(self.running.get(cpu) == pid, "stop without ownership")
            self.budgets_match(p, e)
            self.expect(p.run_at == e['time'], "stop has uncharged execution time")
            del self.running[cpu]
            p.state, p.reason = e['detail'], e['amount']
            self.expect(p.state in {RUNNABLE, SLEEPING, ZOMBIE}, "invalid stopped state")
        elif kind == AFFINITY:
            self.expect(e['amount'] and not (e['amount'] >> self.cpus), "invalid affinity mask")
            p.affinity = e['amount']
            self.budgets_match(p, e)
        elif kind == FREE:
            self.expect(p.state in {USED, ZOMBIE}, "free live process")
            self.expect(pid not in self.running.values() and all(pid not in q for q in self.queues),
                        "free queued/running process")
            del self.processes[pid]
        else:
            self.expect(False, f"unknown event type {kind}")


def read_events(path):
    lines = [line[len('EVENT,'):] for line in Path(path).read_text().splitlines()
             if line.startswith('EVENT,')]
    return [{k: int(v) for k, v in row.items()} for row in csv.DictReader(lines)]


def validate(events, cpus=4, timer=100000, policy=True):
    model = Model(cpus, timer, policy)
    for event in events:
        model.feed(event)
    if not events:
        raise AssertionError("empty trace")
    return model


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('log', type=Path)
    parser.add_argument('--cpus', type=int, default=4)
    parser.add_argument('--timer', type=int, default=100000)
    parser.add_argument('--policy', choices=['MLFQ', 'DEFAULT'], default='MLFQ')
    args = parser.parse_args()
    text = args.log.read_text()
    if 'TRACE_DONE,lost=0,' not in text:
        raise AssertionError('trace dump incomplete or overflowed')
    model = validate(read_events(args.log), args.cpus, args.timer, args.policy == 'MLFQ')
    if model.counts[DISPATCH] < 10 or model.counts[CHARGE] < 10:
        raise AssertionError('insufficient trace coverage')
    result = {'events': sum(v for k, v in model.counts.items() if isinstance(k, int)),
              'dispatches': model.counts[DISPATCH], 'charges': model.counts[CHARGE],
              'cpus_seen': sorted(model.cpus_seen), 'migrations': model.counts['migrations'],
              'validated': ['priority', 'FIFO', 'CPU ownership', 'affinity', 'budgets', 'lifecycle']}
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
