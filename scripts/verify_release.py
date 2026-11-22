"""Verify release evidence and rebuild/test the exported source in isolation."""
import csv
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time
import zipfile

from benchmark import source_hash
from xv6 import ROOT


def run(script,*arguments):
    subprocess.run([sys.executable,str(ROOT/'scripts'/script),*map(str,arguments)],
                   cwd=ROOT,check=True)


def read(path):
    return json.loads(path.read_text())


def exported_source_hash(xv6):
    digest=hashlib.sha256()
    for p in sorted(xv6.rglob('*')):
        if p.is_file() and p.name!='usys.S' and (p.suffix in {'.c','.h','.S','.pl','.ld'} or p.name=='Makefile'):
            digest.update(p.relative_to(xv6).as_posix().encode())
            digest.update(p.read_bytes())
    return digest.hexdigest()


def main():
    run('check_platform.py')
    base=ROOT/'results/v2'
    digest=source_hash()
    primary=read(base/'validation/results.json')
    if primary.get('passed')!=72 or primary['source_sha256']!=digest:
        raise AssertionError('complete current-source primary validation required')
    for c in primary['cases']:
        if c['status']!='passed': raise AssertionError('failed validation case')
    performance_boots=0
    performance_records=0
    for name in ['benchmarks','scaling','sensitivity/short_slices',
                 'sensitivity/long_slices','sensitivity/no_boost']:
        meta=read(base/name/'metadata.json')
        if meta['source_sha256']!=digest or not meta.get('finished_utc'):
            raise AssertionError('missing/stale performance evidence: '+name)
        expected_boots={'benchmarks':240,'scaling':24}.get(name,12)
        if meta.get('finished_boots')!=expected_boots or not meta.get('kernels'):
            raise AssertionError('incomplete performance matrix: '+name)
        if name in {'benchmarks','scaling'}:
            for case in primary['cases']:
                key=f"{case['policy']}-{case['cpus']}cpu"
                if meta['kernels'][key]!=case['kernel_sha256']:
                    raise AssertionError('benchmarked kernel differs from validated kernel')
        with (base/name/'samples.csv').open() as stream:
            rows=list(csv.DictReader(stream))
        if len(rows)!=meta['worker_records']:
            raise AssertionError('sample count differs from collection metadata')
        performance_boots+=expected_boots
        performance_records+=len(rows)
    pressure=read(base/'pressure/metadata.json')
    if pressure['source_sha256']!=digest or pressure['cpus']!=1:
        raise AssertionError('missing/stale controlled-load evidence')
    if read(base/'platform/verified.json')['timebase_hz']!=10000000:
        raise AssertionError('platform timebase mismatch')

    # Full upstream coverage uses 1/2/4/8. Exercise the other supported CPU
    # counts with targeted rules, seeded races, replay, and overflow checks.
    extra_path=base/'validation-extra/results.json'
    if not extra_path.exists() or read(extra_path).get('source_sha256')!=digest or read(extra_path).get('passed')!=32:
        run('run_tests.py','--cpus',3,5,6,7,'--cases','rules','stress','trace','trace-overflow',
            '--seeds',7,'--output',base/'validation-extra')
    extra=read(extra_path)
    if extra.get('passed')!=32 or any(c['status']!='passed' for c in extra['cases']):
        raise AssertionError('additional CPU coverage incomplete')
    run('render_report.py')
    for p in [ROOT/'README.md',ROOT/'PROJECT_PLAN.md',*(ROOT/'docs').glob('*.md')]:
        text=p.read_text(encoding='utf-8-sig')
        if text.count('```')%2: raise AssertionError('unclosed code fence: '+str(p))
        for link in re.findall(r'\]\(([^)]+)\)',text):
            if not link.startswith(('http:','https:','#')) and not (p.parent/link.split('#')[0]).exists():
                raise AssertionError('broken local document link: '+link)
    release=dict(version=2,source_sha256=digest,kernel_source_sha256=source_hash(True),
                 primary_guest_cases=72,additional_guest_cases=32,
                 stress_actions=primary['stress_actions']+extra['stress_actions'],
                 model_tests=primary['model_tests'],standalone='pending',
                 performance_boots=performance_boots,performance_worker_records=performance_records,
                 controlled_pressure_boots=2)
    with (base/'benchmarks/samples.csv').open() as f:
        samples=list(csv.DictReader(f))
    release['main_worker_records']=len(samples)
    release['main_benchmark_boots']=len({(r['cpus'],r['policy'],r['mode'],r['run']) for r in samples})
    (base/'RELEASE.json').write_text(json.dumps(release,indent=2)+'\n')
    run('package.py')
    destination=base/'release'
    destination.mkdir(exist_ok=True)
    start=time.monotonic()
    with tempfile.TemporaryDirectory(prefix='mlfq-source-check-') as temp:
        directory=Path(temp).resolve()
        assert directory.parent==Path(tempfile.gettempdir()).resolve()
        with zipfile.ZipFile(ROOT/'submission.zip') as archive:
            if archive.testzip() is not None: raise AssertionError('corrupt ZIP')
            for name in archive.namelist():
                target=(directory/name).resolve()
                if not target.is_relative_to(directory): raise AssertionError('unsafe ZIP member')
            archive.extractall(directory)
        exported=directory/'MLFQScheduler'
        if exported_source_hash(exported/'xv6')!=digest:
            raise AssertionError('exported source differs from validated source')
        command=[sys.executable,str(exported/'scripts/xv6.py'),'--cpus','4',
                 '--log',str(destination/'standalone.log'),'--command','schedtest']
        result=subprocess.run(command,cwd=exported,stdout=subprocess.PIPE,
                              stderr=subprocess.STDOUT,text=True,timeout=180)
        (destination/'build-and-test.log').write_text(result.stdout)
        if result.returncode or 'SCHEDTEST ALL PASSED' not in result.stdout:
            raise RuntimeError('exported source build/test failed; see release logs')
        binary=hashlib.sha256((exported/'xv6/kernel/kernel').read_bytes()).hexdigest()
        expected=next(c['kernel_sha256'] for c in primary['cases']
                      if c['policy']=='MLFQ' and c['cpus']==4)
        clean=dict(source_sha256=digest,cpus=4,status='passed',kernel_sha256=binary,
                   matches_validated_kernel=binary==expected,seconds=round(time.monotonic()-start,3))
        (destination/'standalone.json').write_text(json.dumps(clean,indent=2)+'\n')
    release['standalone']='passed'
    release['standalone_kernel_sha256']=binary
    release['matches_validated_kernel']=binary==expected
    (base/'RELEASE.json').write_text(json.dumps(release,indent=2)+'\n')
    from xv6 import build
    build('MLFQ',cpus=4)
    run('package.py')
    archive_hash=hashlib.sha256((ROOT/'submission.zip').read_bytes()).hexdigest()
    (ROOT/'submission.zip.sha256').write_text(archive_hash+'  submission.zip\n')
    print('RELEASE VERIFIED: current source, complete evidence, standalone build and scheduler tests',flush=True)


if __name__=='__main__':
    main()
