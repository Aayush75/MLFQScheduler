"""Controlled boost ablation with identical pre-created load and a fixed window."""
import argparse
import csv
import hashlib
from datetime import datetime, timezone
import json
from pathlib import Path
from replay_trace import read_events, validate, CHARGE, READY, WAKE
from xv6 import ROOT, XV6, Qemu, build
from benchmark import source_hash


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cpus',type=int,default=1)
    parser.add_argument('--existing-build',action='store_true')
    parser.add_argument('--output',type=Path,default=ROOT/'results/v2/pressure')
    args=parser.parse_args()
    directory=args.output
    directory.mkdir(parents=True,exist_ok=True)
    if args.existing_build:
        stamp=(XV6/'.sched-config').read_text().split()
        if stamp[:2]!=['MLFQ',str(args.cpus)]:
            raise AssertionError('existing kernel policy/CPU count mismatch')
    else:
        build('MLFQ',cpus=args.cpus)
    rows=[]
    for boost in [0,64]:
        log=directory/f'boost-{boost}.log'
        with Qemu(log,args.cpus) as qemu:
            output=qemu.command(f'schedpressure {boost}')
            dump=qemu.command('schedtrace')
        if 'TRACE_DONE,lost=0,' not in dump:
            raise AssertionError('pressure trace is incomplete')
        lines=[line[len('LOAD,'):] for line in output.splitlines() if line.startswith('LOAD,')]
        row=list(csv.DictReader(lines))[0]
        id_line=next(line for line in output.splitlines() if line.startswith('LOADIDS,'))
        arrivals=set(map(int,id_line.split(',')[1:]))
        if len(arrivals)!=int(row['arrivals']):
            raise AssertionError('offered arrival set is incomplete')
        events=read_events(log)
        validate(events,cpus=args.cpus)
        pid,start,end=(int(row[k]) for k in ['old_pid','start','end'])
        first_ready={}
        service=0
        for e in events:
            if e['type']==READY and e['detail']==WAKE and e['pid'] in arrivals and e['time']>=start:
                first_ready.setdefault(e['pid'],e['time'])
            if e['type']==CHARGE and e['pid']==pid:
                service+=max(0,min(e['time'],end)-max(e['time']-e['amount'],start))
        if not 0<=service<=end-start:
            raise AssertionError('invalid fixed-window service')
        if len(first_ready)!=len(arrivals):
            raise AssertionError('not every offered job was observed becoming runnable')
        release_span=max(first_ready.values())-min(first_ready.values())
        if release_span>=100000:
            raise AssertionError('burst publication exceeded one timer period')
        row.update(old_cpu=service,window=end-start,events=len(events),release_span=release_span)
        rows.append(row)
        print(f'boost={boost}: old-worker service={service} / {end-start} clock units',flush=True)
    if int(rows[0]['old_cpu'])!=0 or int(rows[1]['old_cpu'])<=0:
        raise AssertionError('controlled progress hypothesis not observed')
    with (directory/'summary.csv').open('w',newline='') as stream:
        writer=csv.DictWriter(stream,fieldnames=list(rows[0]))
        writer.writeheader(); writer.writerows(rows)
    (directory/'metadata.json').write_text(json.dumps(dict(
        source_sha256=source_hash(),cpus=args.cpus,timebase_hz=10000000,
        kernel_sha256=hashlib.sha256((XV6/'kernel/kernel').read_bytes()).hexdigest(),
        finished_utc=datetime.now(timezone.utc).isoformat(),
        offered_arrivals=40,target_cpu_per_arrival=400000,window=10000000,
        metric='sum of CHARGE intervals clipped to the fixed observation window'),indent=2)+'\n')
    print('CONTROLLED PRESSURE CHECKS PASSED',flush=True)


if __name__=='__main__':
    main()
