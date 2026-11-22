"""Create a reproducible source/results archive without build or Git state."""
from pathlib import Path
import zipfile
from xv6 import ROOT, XV6

SOURCE_SUFFIXES={'.c','.h','.S','.pl','.py','.ld','.yml','.yaml'}
SOURCE_NAMES={'Makefile','README','LICENSE','UPSTREAM','.gitignore','.editorconfig',
              '.clang-format','.dir-locals.el','.gdbinit.tmpl-riscv'}
RESULT_DIRECTORIES={'validation','benchmarks','scaling','sensitivity','pressure','platform','validation-extra','release'}


def main():
    files=[ROOT/name for name in ['README.md','PROJECT_PLAN.md','requirements.txt']]
    for name in ['scripts','docs']:
        files.extend(p for p in (ROOT/name).rglob('*') if p.is_file()
                     and '__pycache__' not in p.parts and p.suffix in {'.py','.md','.json'})
    for p in XV6.rglob('*'):
        if p.is_file() and '.git' not in p.parts and p.name!='usys.S' and (
                p.suffix in SOURCE_SUFFIXES or p.name in SOURCE_NAMES):
            files.append(p)
    current=ROOT/'results/v2'
    for name in RESULT_DIRECTORIES:
        files.extend(p for p in (current/name).rglob('*') if p.is_file()
                     and '__pycache__' not in p.parts and '.runs' not in p.parts)
    if (current/'RELEASE.json').exists(): files.append(current/'RELEASE.json')
    destination=ROOT/'submission.zip'
    with zipfile.ZipFile(destination,'w',zipfile.ZIP_DEFLATED) as archive:
        for p in sorted(set(files)):
            archive.write(p,Path('MLFQScheduler')/p.relative_to(ROOT))
    with zipfile.ZipFile(destination) as archive:
        if archive.testzip() is not None:
            raise RuntimeError('archive integrity failure')
    print(f'Created {destination} ({destination.stat().st_size:,} bytes; {len(set(files))} files)')


if __name__=='__main__':
    main()
