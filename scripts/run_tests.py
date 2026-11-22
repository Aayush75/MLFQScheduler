"""Run full regressions, seeded stress, and independent trace checks."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
import time

from benchmark import source_hash, upstream_commit
from replay_trace import read_events, validate, DISPATCH, CHARGE
from xv6 import ROOT, XV6, Qemu, build


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cpus', nargs='+', type=int, default=[1,2,4,8])
    parser.add_argument('--policies', nargs='+', choices=['DEFAULT','MLFQ'], default=['DEFAULT','MLFQ'])
    parser.add_argument('--seeds', nargs='+', type=int, default=[1,7,19,73,101])
    parser.add_argument('--rounds', type=int, default=100)
    parser.add_argument('--workers', type=int, default=12)
    parser.add_argument('--cases',nargs='+',choices=['rules','upstream','stress','trace','trace-overflow'],
                        default=['rules','upstream','stress','trace','trace-overflow'])
    parser.add_argument('--quick', action='store_true', help='omit upstream slow tests')
    parser.add_argument('--output', type=Path, default=ROOT/'results/v2/validation')
    args=parser.parse_args()
    args.output.mkdir(parents=True,exist_ok=True)
    report=dict(started_utc=datetime.now(timezone.utc).isoformat(),
                upstream=upstream_commit(),source_sha256=source_hash(),
                kernel_source_sha256=source_hash(True),checks=1,selected_cases=args.cases,cases=[])
    destination=args.output/'results.json'

    def save():
        destination.write_text(json.dumps(report,indent=2)+'\n')

    save()
    unit=subprocess.run([sys.executable,str(ROOT/'scripts/test_replay.py')],
                        stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True)
    (args.output/'model-unit.log').write_text(unit.stdout)
    if unit.returncode:
        raise RuntimeError(unit.stdout)
    report['model_tests']=13
    save()
    for cpus in args.cpus:
        for policy in args.policies:
            build(policy,cpus=cpus)
            kernel=sha(XV6/'kernel/kernel')
            cases=[('rules','schedtest'),('upstream','usertests -q' if args.quick else 'usertests')]
            cases += [(f'stress-{seed}',f'schedstress {seed} {args.rounds} {args.workers}')
                      for seed in args.seeds]
            cases += [('trace','schedtest trace'),('trace-overflow','schedtest overflow')]
            for name,command in cases:
                category='stress' if name.startswith('stress-') else name
                if category not in args.cases: continue
                log=args.output/f'{policy}-{cpus}cpu-{name}.log'
                case=dict(policy=policy,cpus=cpus,name=name,command=command,
                          kernel_sha256=kernel,status='running',log=log.name)
                report['cases'].append(case)
                save()
                start=time.monotonic()
                try:
                    with Qemu(log,cpus) as qemu:
                        output=qemu.command(command,1800)
                        if name=='trace':
                            dump=qemu.command('schedtrace',300)
                            if 'TRACE_DONE,lost=0,' not in dump:
                                raise AssertionError('incomplete/overflowed trace')
                    if name=='trace':
                        model=validate(read_events(log),cpus,policy=policy=='MLFQ')
                        if model.counts[DISPATCH]<10 or model.counts[CHARGE]<10:
                            raise AssertionError('insufficient trace coverage')
                        case.update(events=sum(v for k,v in model.counts.items() if isinstance(k,int)),
                                    dispatches=model.counts[DISPATCH],charges=model.counts[CHARGE],
                                    trace_cpus=sorted(model.cpus_seen))
                    if name.startswith('stress-'):
                        match=re.search(r'^STRESS_RESULT,([0-9,]+)$',output,re.MULTILINE)
                        if not match:
                            raise AssertionError('missing stress operation counts')
                        counts=list(map(int,match.group(1).split(',')))
                        case.update(actions=counts[0],forks=counts[1],execs=counts[2],
                                    kills=counts[3],pipes=counts[4],affinities=counts[5])
                    if name=='upstream':
                        case['upstream_tests']=len(re.findall(r'^test \w+:',output,re.MULTILINE))
                    case['status']='passed'
                except Exception as error:
                    case['status']='failed'
                    case['error']=str(error)
                    case['seconds']=round(time.monotonic()-start,3)
                    save()
                    raise
                case['seconds']=round(time.monotonic()-start,3)
                save()
                print(f'{policy} {cpus} CPU(s): {name} PASSED ({case["seconds"]} s)',flush=True)
    report['finished_utc']=datetime.now(timezone.utc).isoformat()
    report['passed']=sum(c['status']=='passed' for c in report['cases'])
    report['stress_actions']=sum(c.get('actions',0) for c in report['cases'])
    save()
    print(f'ALL {report["passed"]} guest cases PASSED; {report["stress_actions"]} seeded actions',flush=True)


if __name__=='__main__':
    main()
