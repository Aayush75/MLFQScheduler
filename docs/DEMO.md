# Running an interactive demonstration

Inside Ubuntu:

```bash
cd '/mnt/c/Users/Aayush/IITGN/CS 330/MLFQScheduler/xv6'
make qemu CPUS=4
```

Inspect configuration and statistics, then run the correctness and stress checks:

```text
schedstat
schedtest
schedstress 7 100 12
```

Compare CPU, blocking, short-job, pipe, phase-change, and fairness workloads:

```text
schedbench cpu 1200 8
schedbench mixed 1200 8
schedbench short 1200 8
schedbench pipe 1200 8
schedbench phase 1200 8
schedbench fair 400 8
```

In fair mode, the numeric argument is the observation duration in milliseconds.
In the other modes, it is a fixed arithmetic-work count.

Capture a bounded trace and inspect it:

```text
schedtest trace
schedtrace
schedctl trace 0
```

Change policy parameters without rebuilding:

```text
schedctl boost 64
schedctl 1 2 4 8 4 8 16 32 64
schedstat
```

Exit with Ctrl-a, then x. To switch to the comparison policy:

```bash
make qemu SCHED=DEFAULT CPUS=4
```

For a reproducible trace check and controlled boost experiment, use the host
scripts rather than copying console output:

```bash
cd ..
python3 scripts/check_trace.py --cpus 4
python3 scripts/diagnostics.py
```
