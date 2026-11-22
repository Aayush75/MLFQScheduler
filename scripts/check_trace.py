"""Run a bounded multicore trace scenario and independently replay it."""
import argparse
import json
from pathlib import Path
from replay_trace import read_events, validate, DISPATCH, CHARGE
from xv6 import ROOT, Qemu, build


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cpus', type=int, default=4)
    parser.add_argument('--policy', choices=['MLFQ','DEFAULT'], default='MLFQ')
    args = parser.parse_args()
    build(args.policy, cpus=args.cpus)
    log = ROOT / 'results/v2' / f'trace-{args.policy}-{args.cpus}.log'
    with Qemu(log, args.cpus) as qemu:
        qemu.command('schedtest trace')
        output = qemu.command('schedtrace')
    if 'TRACE_DONE,lost=0,' not in output:
        raise AssertionError('incomplete/overflowed trace')
    model = validate(read_events(log), args.cpus, policy=args.policy == 'MLFQ')
    result = {'cpus': args.cpus, 'policy': args.policy,
              'dispatches': model.counts[DISPATCH], 'charges': model.counts[CHARGE],
              'cpus_seen': sorted(model.cpus_seen), 'migrations': model.counts['migrations']}
    log.with_suffix('.json').write_text(json.dumps(result, indent=2)+'\n')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
