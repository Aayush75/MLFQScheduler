"""Collect matched high-resolution workloads on fresh 1/2/4/8-core QEMU boots."""
import argparse
import csv
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import platform
import subprocess

from xv6 import ROOT, XV6, Qemu, build


def source_hash(kernel_only=False):
    digest = hashlib.sha256()
    for path in sorted(XV6.rglob('*')):
        if not path.is_file() or '.git' in path.parts or path.name == 'usys.S':
            continue
        if kernel_only and path.parent != XV6 / 'kernel' and path.name != 'Makefile':
            continue
        if path.suffix in {'.c','.h','.S','.pl','.ld'} or path.name == 'Makefile':
            digest.update(path.relative_to(XV6).as_posix().encode())
            digest.update(path.read_bytes())
    return digest.hexdigest()


def upstream_commit():
    # The explicit base marker also works in source exports without Git, or
    # when an extracted export happens to sit inside a different repository.
    marker=XV6/'UPSTREAM'
    if marker.exists():
        return marker.read_text().strip()
    return subprocess.check_output(['git','rev-parse','HEAD'],cwd=XV6,text=True).strip()


def version(*args):
    return subprocess.check_output(args, text=True).splitlines()[0]


def result_rows(output):
    lines = [line[len('RESULT,'):] for line in output.splitlines() if line.startswith('RESULT,')]
    return list(csv.DictReader(lines))


def validate_rows(rows, workers, cpus):
    if len(rows) != workers or len({r['worker'] for r in rows}) != workers:
        raise AssertionError('missing/duplicate worker result')
    elapsed=max(int(r['done']) for r in rows)-min(int(r['release']) for r in rows)
    if sum(int(r['cpu_time']) for r in rows)>cpus*elapsed:
        raise AssertionError('aggregate CPU service exceeds available multicore time')
    for r in rows:
        if None in r or any(v is None for v in r.values()):
            raise AssertionError('malformed CSV row')
        total = int(r['cpu_time'])
        if sum(int(r[f'q{q}']) for q in range(4)) != total:
            raise AssertionError('per-priority CPU conservation')
        if sum(int(r[f'cpu{c}']) for c in range(8)) != total:
            raise AssertionError('per-core CPU conservation')
        first, done, release = (int(r[k]) for k in ['first','done','release'])
        if not release <= first <= done:
            raise AssertionError('invalid timestamp ordering')
        if total + int(r['wait_time']) + int(r['sleep_time']) != done - first:
            raise AssertionError('timed state conservation')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cpus', type=int, nargs='+', default=[1,2,4,8])
    parser.add_argument('--repeats', type=int, default=5)
    parser.add_argument('--units', type=int, default=1200)
    parser.add_argument('--fair-ms', type=int, default=400)
    parser.add_argument('--workers', type=int, default=0)
    parser.add_argument('--boost', type=int, default=100)
    parser.add_argument('--quanta', default='1,2,4,8')
    parser.add_argument('--allotments', default='2,4,8,16')
    parser.add_argument('--checks', type=int, choices=[0,1], default=1)
    parser.add_argument('--modes', nargs='+', default=['cpu','mixed','short','pipe','phase','fair'])
    parser.add_argument('--output', type=Path, default=ROOT/'results/v2/benchmarks')
    args = parser.parse_args()
    if args.workers and not 4 <= args.workers <= 32:
        parser.error('explicit worker count must be 4..32')
    if args.repeats < 1 or not set(args.modes) <= {'cpu','mixed','short','pipe','phase','fair'}:
        parser.error('invalid repetition count or workload')
    args.output.mkdir(parents=True, exist_ok=True)
    metadata = dict(started_utc=datetime.now(timezone.utc).isoformat(),
                    version=2, upstream_commit=upstream_commit(), source_sha256=source_hash(),
                    kernel_source_sha256=source_hash(True), timebase_hz=10000000,
                    timer_interval=100000, cpus=args.cpus, repeats=args.repeats,
                    units=args.units, fair_ms=args.fair_ms, workers=args.workers,
                    boost=args.boost, quanta=args.quanta, allotments=args.allotments,
                    checks=args.checks, modes=args.modes, workload_fork_inherit=0,
                    host=platform.platform(), python=platform.python_version(),
                    qemu=version('qemu-system-riscv64','--version'),
                    compiler=version('riscv64-linux-gnu-gcc','--version'),
                    ordering='alternate policy order by repetition; isolated kernel and disk image per boot',
                    kernels={},finished_boots=0,worker_records=0)
    path = args.output/'metadata.json'
    path.write_text(json.dumps(metadata,indent=2)+'\n')
    checksums = {}
    with (args.output/'samples.csv').open('w',newline='') as stream:
        writer = None
        for cpus in args.cpus:
            workers = args.workers or max(4,2*cpus)
            for repeat in range(args.repeats):
                policies = ['DEFAULT','MLFQ'] if repeat % 2 == 0 else ['MLFQ','DEFAULT']
                for policy in policies:
                    build(policy,args.boost,args.quanta,cpus,args.allotments,checks=args.checks)
                    key=f'{policy}-{cpus}cpu'
                    binary=hashlib.sha256((XV6/'kernel/kernel').read_bytes()).hexdigest()
                    if metadata['kernels'].setdefault(key,binary)!=binary:
                        raise AssertionError('kernel changed between matched repetitions')
                    for mode in args.modes:
                        units = args.fair_ms if mode == 'fair' else args.units
                        log = args.output/'logs'/f'{cpus}cpu-{repeat:02d}-{policy}-{mode}.log'
                        with Qemu(log,cpus) as qemu:
                            output = qemu.command(f'schedbench {mode} {units} {workers}')
                        rows = result_rows(output)
                        validate_rows(rows,workers,cpus)
                        for row in rows:
                            if row['mode'] != mode:
                                raise AssertionError('wrong workload result')
                            if mode != 'fair':
                                key = (mode,row['role'],row['worker'])
                                if checksums.setdefault(key,row['checksum']) != row['checksum']:
                                    raise AssertionError('fixed-work checksum differs across policies/runs')
                            row.update(run=repeat,policy=policy,cpus=cpus,units=units,workers=workers,
                                       boost=args.boost,quanta=args.quanta,allotments=args.allotments,
                                       timer=100000,checks=args.checks)
                            if writer is None:
                                writer=csv.DictWriter(stream,fieldnames=list(row))
                                writer.writeheader()
                            writer.writerow(row)
                        stream.flush()
                        metadata['finished_boots']+=1
                        metadata['worker_records']+=len(rows)
                        print(f'{cpus} CPUs {repeat+1}/{args.repeats} {policy} {mode}: OK',flush=True)
    if source_hash()!=metadata['source_sha256']:
        raise AssertionError('source changed during collection')
    metadata['finished_utc']=datetime.now(timezone.utc).isoformat()
    path.write_text(json.dumps(metadata,indent=2)+'\n')


if __name__ == '__main__':
    main()
