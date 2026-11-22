"""Verify QEMU's actual device-tree clock frequency used by timing conversion."""
import json
import os
import platform
from pathlib import Path
import struct
import subprocess
from xv6 import ROOT


def read_timebase(data):
    header=struct.unpack_from('>10I',data)
    if header[0]!=0xd00dfeed:
        raise AssertionError('invalid flattened device tree')
    position,strings=header[2],header[3]
    while True:
        token=struct.unpack_from('>I',data,position)[0]; position+=4
        if token==1:
            position=(data.index(b'\0',position)+4)&~3
        elif token==3:
            length,name=struct.unpack_from('>II',data,position); position+=8
            key=data[strings+name:data.index(b'\0',strings+name)]
            payload=data[position:position+length]
            position=(position+length+3)&~3
            if key==b'timebase-frequency':
                if length!=4:
                    raise AssertionError('unexpected timebase property size')
                return struct.unpack('>I',payload)[0]
        elif token==9:
            raise AssertionError('timebase-frequency absent')
        elif token not in {2,4}:
            raise AssertionError('invalid device-tree token')


def main():
    directory=ROOT/'results/v2/platform'
    directory.mkdir(parents=True,exist_ok=True)
    dtb=directory/'virt-8cpu.dtb'
    command=['qemu-system-riscv64','-machine',f'virt,dumpdtb={dtb}',
             '-smp','8','-m','128M','-bios','none','-nographic']
    result=subprocess.run(command,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,timeout=15)
    if result.returncode:
        raise RuntimeError(result.stdout)
    frequency=read_timebase(dtb.read_bytes())
    if frequency!=10000000:
        raise AssertionError(f'expected 10 MHz clock, got {frequency}')
    cpu_model=next((line.split(':',1)[1].strip()
                    for line in Path('/proc/cpuinfo').read_text().splitlines()
                    if line.startswith('model name')),'unknown')
    metadata=dict(host=platform.platform(),host_cpu_model=cpu_model,
                  host_logical_cpus=os.cpu_count(),host_available_cpus=len(os.sched_getaffinity(0)),
                  machine='virt',cpus=8,timebase_hz=frequency,
                  qemu=subprocess.check_output(['qemu-system-riscv64','--version'],text=True).splitlines()[0])
    (directory/'verified.json').write_text(json.dumps(metadata,indent=2)+'\n')
    print(f'Platform verified: shared timebase = {frequency} Hz')


if __name__=='__main__':
    main()
