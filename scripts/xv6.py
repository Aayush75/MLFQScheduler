"""Build and drive xv6 in isolated QEMU boots. Run inside WSL/Linux."""

import argparse
import os
from pathlib import Path
import select
import shutil
import subprocess
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
XV6 = ROOT / "xv6"


def build(policy="MLFQ", boost=100, quanta="1,2,4,8", cpus=4,
          allotments="2,4,8,16", timer=100000, checks=1):
    if policy not in {"MLFQ", "DEFAULT"} or not 1 <= cpus <= 8:
        raise ValueError("invalid policy/CPU count")
    q = [int(v) for v in quanta.split(",")]
    a = [int(v) for v in allotments.split(",")]
    if len(q) != 4 or len(a) != 4 or any(x < 1 or x > 10000 for x in q):
        raise ValueError("four positive bounded quanta required")
    if any(y < x or y > 10000 or y % x for x, y in zip(q, a)):
        raise ValueError("allotments must be bounded multiples of their quanta")
    if not 0 <= boost <= 100000 or not 1000 <= timer <= 1000000 or checks not in {0, 1}:
        raise ValueError("invalid boost, timer, or invariant-check setting")
    args = [f"SCHED={policy}", f"CPUS={cpus}", f"BOOST={boost}", f"QUANTA={quanta}",
            f"ALLOTMENTS={allotments}", f"TIMER={timer}", f"CHECKS={checks}"]
    result = subprocess.run(["make", "-s", "-j4", *args, "kernel/kernel", "fs.img"],
                            cwd=XV6, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    if result.returncode:
        raise RuntimeError("Build failed:\n" + result.stdout)


class Qemu:
    def __init__(self, log, cpus=4):
        if not 1 <= cpus <= 8:
            raise ValueError("CPU count must be 1..8")
        self.cpus = cpus
        self.log = Path(log)
        self.log.parent.mkdir(parents=True, exist_ok=True)
        self.output = bytearray()

    def __enter__(self):
        # Every guest gets an independent kernel/image pair. Tests cannot
        # contaminate the next boot or accidentally share a writable image.
        # Native Linux temporary storage avoids DrvFS write/flush overhead
        # while the editable source remains in the Windows project folder.
        self.temp = tempfile.TemporaryDirectory(prefix="xv6-mlfq-")
        directory = Path(self.temp.name).resolve()
        assert directory.parent == Path(tempfile.gettempdir()).resolve()
        shutil.copyfile(XV6 / "kernel/kernel", directory / "kernel")
        shutil.copyfile(XV6 / "fs.img", directory / "fs.img")
        self.file = self.log.open("wb")
        self.proc = subprocess.Popen([
            "qemu-system-riscv64", "-machine", "virt", "-bios", "none",
            "-kernel", "kernel", "-m", "128M", "-smp", str(self.cpus), "-nographic",
            "-global", "virtio-mmio.force-legacy=false",
            "-drive", "file=fs.img,if=none,format=raw,id=x0",
            "-device", "virtio-blk-device,drive=x0,bus=virtio-mmio-bus.0",
        ], cwd=directory, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
           stderr=subprocess.STDOUT)
        try:
            self.wait(b"$ ", 30)
        except BaseException:
            self.__exit__(None, None, None)
            raise
        return self

    def wait(self, marker, timeout):
        start = len(self.output)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if select.select([self.proc.stdout], [], [], 0.1)[0]:
                chunk = os.read(self.proc.stdout.fileno(), 65536)
                if not chunk:
                    raise RuntimeError(f"QEMU exited; see {self.log}\n" +
                                       self.output[-4000:].decode(errors="replace"))
                self.output.extend(chunk)
                self.file.write(chunk)
                self.file.flush()
                current = bytes(self.output[start:])
                if b"panic:" in current:
                    raise RuntimeError(f"Kernel panic; see {self.log}\n" +
                                       current[-2000:].decode(errors="replace"))
                if marker in current:
                    return current.decode(errors="replace").replace("\r", "")
        raise TimeoutError(f"Timed out waiting for {marker!r}; see {self.log}")

    def command(self, command, timeout=600):
        self.proc.stdin.write((command + "\n").encode())
        self.proc.stdin.flush()
        output = self.wait(b"$ ", timeout)
        expected = None
        if command.startswith("schedtest"):
            expected = "SCHEDTEST ALL PASSED"
        elif command.startswith("schedstress"):
            expected = "SCHEDSTRESS ALL PASSED"
        elif command.startswith("usertests"):
            expected = "ALL TESTS PASSED"
        elif command == "forktest":
            expected = "fork test OK"
        elif command.startswith("schedbench"):
            expected = "BENCH DONE"
        elif command.startswith("schedpressure"):
            expected = "PRESSURE DONE"
        elif command == "schedtrace":
            expected = "TRACE_DONE,lost=0,"
        if expected and expected not in output:
            raise RuntimeError(f"Guest command failed: {command}; see {self.log}\n{output[-4000:]}")
        return output

    def __exit__(self, *args):
        try:
            if self.proc.poll() is None:
                try:
                    self.proc.stdin.write(b"\x01x")
                    self.proc.stdin.flush()
                    self.proc.wait(timeout=5)
                except (BrokenPipeError, subprocess.TimeoutExpired):
                    self.proc.kill()
                    self.proc.wait()
        finally:
            self.file.close()
            self.temp.cleanup()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--policy", choices=["DEFAULT", "MLFQ"], default="MLFQ")
    parser.add_argument("--cpus", type=int, default=4)
    parser.add_argument("--boost", type=int, default=100)
    parser.add_argument("--quanta", default="1,2,4,8")
    parser.add_argument("--allotments", default="2,4,8,16")
    parser.add_argument("--timer", type=int, default=100000)
    parser.add_argument("--checks", type=int, choices=[0, 1], default=1)
    parser.add_argument("--log", default=str(ROOT / "results/v2/console.log"))
    parser.add_argument("--command", action="append")
    parser.add_argument("--timeout", type=int, default=1800)
    args = parser.parse_args()
    build(args.policy, args.boost, args.quanta, args.cpus, args.allotments, args.timer, args.checks)
    with Qemu(args.log, args.cpus) as qemu:
        for command in args.command or ["schedtest"]:
            print(qemu.command(command, args.timeout), flush=True)


if __name__ == "__main__":
    main()
