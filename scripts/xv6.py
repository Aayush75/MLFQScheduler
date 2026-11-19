"""Small, dependency-free QEMU console driver. Run this inside WSL/Linux."""

import argparse
import os
from pathlib import Path
import select
import subprocess
import time

ROOT = Path(__file__).resolve().parents[1]
XV6 = ROOT / "xv6"


def build(policy="MLFQ", boost=64, quanta="1,2,4,8"):
    args = [f"SCHED={policy}", "CPUS=1", f"BOOST={boost}", f"QUANTA={quanta}"]
    result = subprocess.run(
        ["make", "-s", "-j4", *args, "kernel/kernel", "fs.img"],
        cwd=XV6, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )
    if result.returncode:
        raise RuntimeError("Build failed:\n" + result.stdout)


class Qemu:
    def __init__(self, log):
        self.log = Path(log)
        self.log.parent.mkdir(parents=True, exist_ok=True)
        self.output = bytearray()

    def __enter__(self):
        self.file = self.log.open("wb")
        self.proc = subprocess.Popen([
            "qemu-system-riscv64", "-machine", "virt", "-bios", "none",
            "-kernel", "kernel/kernel", "-m", "128M", "-smp", "1", "-nographic",
            "-global", "virtio-mmio.force-legacy=false",
            "-drive", "file=fs.img,if=none,format=raw,id=x0",
            "-device", "virtio-blk-device,drive=x0,bus=virtio-mmio-bus.0",
        ], cwd=XV6, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
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
            if select.select([self.proc.stdout], [], [], 0.2)[0]:
                chunk = os.read(self.proc.stdout.fileno(), 65536)
                if not chunk:
                    raise RuntimeError("QEMU exited:\n" + self.output.decode(errors="replace"))
                self.output.extend(chunk)
                self.file.write(chunk)
                self.file.flush()
                current = bytes(self.output[start:])
                if b"panic:" in current:
                    raise RuntimeError("Kernel panic; see " + str(self.log))
                if marker in current:
                    return current.decode(errors="replace").replace("\r", "")
        raise TimeoutError(f"Timed out waiting for {marker!r}; see {self.log}")

    def command(self, command, timeout=300):
        self.proc.stdin.write((command + "\n").encode())
        self.proc.stdin.flush()
        output = self.wait(b"$ ", timeout)
        # xv6's shell has no host exit status. Check explicit guest markers:
        # reaching the next prompt alone does NOT mean a test passed.
        expected = None
        if command == "schedtest":
            expected = "SCHEDTEST ALL PASSED"
        elif command.startswith("usertests"):
            expected = "ALL TESTS PASSED"
        elif command == "forktest":
            expected = "fork test OK"
        elif command.startswith("schedbench") and "trace" not in command:
            expected = "BENCH DONE"
        if expected and expected not in output:
            raise RuntimeError(f"Guest command failed: {command}\n{output}")
        return output

    def __exit__(self, *args):
        try:
            if self.proc.poll() is None:
                self.proc.stdin.write(b"\x01x")
                self.proc.stdin.flush()
                self.proc.wait(timeout=5)
        finally:
            if self.proc.poll() is None:
                self.proc.kill()
                self.proc.wait()
            self.file.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--policy", choices=["DEFAULT", "MLFQ"], default="MLFQ")
    parser.add_argument("--boost", type=int, default=64)
    parser.add_argument("--quanta", default="1,2,4,8")
    parser.add_argument("--log", default=str(ROOT / "results/raw/console.log"))
    parser.add_argument("--command", action="append")
    parser.add_argument("--timeout", type=int, default=600)
    args = parser.parse_args()
    build(args.policy, args.boost, args.quanta)
    with Qemu(args.log) as qemu:
        for command in args.command or ["schedtest"]:
            print(qemu.command(command, args.timeout), flush=True)


if __name__ == "__main__":
    main()
