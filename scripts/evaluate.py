"""Run the performance matrix, scaling, sensitivity, boost ablation, and plots."""
import argparse
import json
import time
from pathlib import Path
import subprocess
import sys
from xv6 import ROOT
from benchmark import source_hash


def run(script,*args,python=None):
    command=[python or sys.executable,str(ROOT/'scripts'/script),*map(str,args)]
    subprocess.run(command,cwd=ROOT,check=True)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repeats',type=int,default=5)
    parser.add_argument('--wait-validation',action='store_true')
    args=parser.parse_args()
    if args.wait_validation:
        print('Waiting for the current-source validation matrix...',flush=True)
        while True:
            try:
                state=json.loads((ROOT/'results/v2/validation/results.json').read_text())
            except (FileNotFoundError,json.JSONDecodeError):
                time.sleep(2)
                continue
            failed=[c for c in state['cases'] if c['status']=='failed']
            if failed:
                raise RuntimeError('validation failed: '+str(failed[-1].get('error',''))[-1000:])
            if state.get('finished_utc'):
                if state['source_sha256']!=source_hash():
                    raise AssertionError('validated source differs from current source')
                break
            time.sleep(2)
    run('check_platform.py')
    run('benchmark.py','--repeats',args.repeats,'--output',ROOT/'results/v2/benchmarks')
    run('benchmark.py','--workers',8,'--modes','cpu','--repeats',3,
        '--output',ROOT/'results/v2/scaling')
    profiles=[('short_slices',32,'1,1,2,4','2,2,4,8'),
              ('long_slices',256,'2,4,8,16','4,8,16,32'),
              ('no_boost',0,'1,2,4,8','2,4,8,16')]
    for name,boost,quantum,allotment in profiles:
        run('benchmark.py','--cpus',4,'--modes','mixed','phase','--repeats',3,
            '--boost',boost,'--quanta',quantum,'--allotments',allotment,
            '--output',ROOT/'results/v2/sensitivity'/name)
    run('diagnostics.py')
    plot_python=ROOT/'.venv/bin/python'
    python=str(plot_python) if plot_python.exists() else sys.executable
    run('analyze.py',ROOT/'results/v2/benchmarks',python=python)
    run('analyze.py',ROOT/'results/v2/scaling',python=python)
    for name,_,_,_ in profiles:
        run('analyze.py',ROOT/'results/v2/sensitivity'/name,python=python)
    run('plot_trace.py',python=python)
    from xv6 import build
    build('MLFQ',cpus=4)
    print('EVALUATION COMPLETE',flush=True)


if __name__=='__main__':
    main()
