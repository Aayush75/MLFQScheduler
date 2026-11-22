"""Positive and deliberately corrupted traces test the independent validator."""
import copy
import unittest

from replay_trace import *

T = 100000


def event(kind, pid=0, cpu=0, level=0, amount=0, detail=0, slice=0, allot=0, time=0):
    return dict(type=kind, pid=pid, cpu=cpu, level=level, amount=amount,
                detail=detail, slice=slice, allot=allot, time=time, seq=0)


def packed(values):
    return sum(v << (16 * q) for q, v in enumerate(values))


def prefix():
    return [event(BEGIN, time=T),
            event(CONFIG, detail=2, slice=packed([1,2,4,8]),
                  allot=packed([2,4,8,16]), time=2*T),
            event(ALLOC, 1, amount=3, time=3*T), event(READY, 1, time=4*T),
            event(ALLOC, 2, amount=3, time=5*T), event(READY, 2, time=6*T)]


def complete():
    return prefix() + [event(DISPATCH, 1, time=7*T),
        event(CHARGE, 1, level=1, amount=2*T, detail=0, time=9*T),
        event(STOP, 1, level=1, amount=TIMER, detail=RUNNABLE, time=9*T),
        event(READY, 1, level=1, detail=TIMER, time=9*T),
        event(DISPATCH, 2, time=10*T)]


def numbered(events):
    records = copy.deepcopy(events)
    for i, record in enumerate(records, 1):
        record['seq'] = i
    return records


class ReplayTests(unittest.TestCase):
    def passes(self, records):
        return validate(numbered(records), cpus=2)

    def rejects(self, records):
        with self.assertRaises(AssertionError):
            self.passes(records)

    def test_valid_priority_fifo_and_budget_trace(self):
        self.passes(complete())

    def test_rejects_wrong_priority(self):
        records = complete()
        records[-1] = event(DISPATCH, 1, level=1, time=10*T)
        self.rejects(records)

    def test_rejects_wrong_fifo(self):
        self.rejects(prefix() + [event(DISPATCH, 2, time=7*T)])

    def test_rejects_duplicate_cpu_owner(self):
        self.rejects(prefix() + [event(DISPATCH, 1, time=7*T),
                                 event(DISPATCH, 2, time=8*T)])

    def test_rejects_same_process_on_two_cpus(self):
        self.rejects(prefix() + [event(DISPATCH, 1, time=7*T),
                                 event(DISPATCH, 1, cpu=1, time=8*T)])

    def test_rejects_affinity_violation(self):
        records = prefix()
        records[2]['amount'] = 1
        self.rejects(records + [event(DISPATCH, 1, cpu=1, time=7*T)])

    def test_rejects_lost_execution(self):
        records = complete()
        records[7].update(amount=T, level=0, allot=T)
        self.rejects(records)

    def test_rejects_wrong_demotion(self):
        records = complete()
        records[7]['level'] = 0
        self.rejects(records)

    def test_rejects_wrong_quantum_reinsertion(self):
        records = complete()
        records[9]['amount'] = 1
        self.rejects(records)

    def test_rejects_missing_event(self):
        records = numbered(complete())
        del records[5]
        with self.assertRaises(AssertionError):
            validate(records, cpus=2)

    def test_rejects_trace_without_initial_snapshot(self):
        self.rejects(prefix()[1:])

    def test_boost_preserves_merged_fifo(self):
        records = complete()[:-1] + [event(BOOST, time=10*T),
                                     event(DISPATCH, 2, time=11*T)]
        self.passes(records)
        records[-1]['pid'] = 1
        self.rejects(records)

    def test_seed_preserves_pending_rotation(self):
        records = prefix()[:2] + [
            event(SEED, 1, level=3, amount=1, detail=RUNNING | 256,
                  slice=T, allot=3*T, time=3*T),
            event(STOP, 1, level=3, detail=RUNNABLE, amount=TIMER,
                  slice=T, allot=3*T, time=3*T),
            event(READY, 1, level=3, detail=TIMER,
                  slice=T, allot=3*T, time=3*T)]
        self.passes(records)


if __name__ == '__main__':
    unittest.main(verbosity=2)
