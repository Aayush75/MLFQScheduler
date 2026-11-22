#include "schedutil.h"

static struct sched_config original;
static uint allcpus;

static struct sched_config
configure(uint boost, uint inherit, uint trace)
{
  struct sched_config c = { .version = SCHED_VERSION,
    .quantum = {1, 2, 4, 8}, .allotment = {2, 4, 8, 16},
    .boost_ticks = boost, .fork_inherit = inherit, .trace = trace };
  require(schedctl(SCHED_SET, &c) == 0, "set test configuration");
  return c;
}

static void
api_test(void)
{
  struct schedstat s;
  struct sched_config c, bad;
  struct sched_info before = sysinfo();
  require(getpinfo(-2, &s) == -1 && getpinfo(NPROC, &s) == -1, "reject invalid slots");
  require(getpinfo(-1, 0) == -1 && getpinfo(-1, (void *)~0UL) == -1, "reject bad stats pointers");
  require(schedctl(SCHED_GET, &c) == 0, "read configuration");
  bad = c; bad.quantum[0] = 0;
  require(schedctl(SCHED_SET, &bad) == -1, "reject zero quantum");
  bad = c; bad.allotment[1] = bad.quantum[1] + 1;
  require(schedctl(SCHED_SET, &bad) == -1, "reject fractional quantum allotment");
  bad = c; bad.allotment[0] = 10001;
  require(schedctl(SCHED_SET, &bad) == -1, "reject overflowing configuration");
  bad = c; bad.version++;
  require(schedctl(SCHED_SET, &bad) == -1, "reject incompatible ABI");
  require(schedctl(99, &c) == -1 && schedctl(SCHED_SET, 0) == -1, "reject invalid control request");
  require(schedinfo(0) == -1 && schedinfo((void *)~0UL) == -1, "reject bad system pointers");
  require(sysinfo().generation == before.generation, "invalid configuration is atomic");
  uint mask = selfstat().affinity;
  require(setaffinity(0) == -1 && setaffinity(1U << SCHED_MAXCPU) == -1, "reject empty/offline affinity");
  require(selfstat().affinity == mask, "bad affinity leaves old mask intact");
  int found = 0;
  for (int slot = 0; slot < NPROC; slot++)
    if (getpinfo(slot, &s) == 0 && s.pid == getpid())
      found++;
  require(found == 1, "one process-table record per PID");
  int pid = fork();
  require(pid >= 0, "fork for reaping test");
  if (!pid)
    exit(42);
  require(waitstats(0, 0) == -1, "null final stats rejected");
  require(waitstats(0, (void *)~0UL) == -1, "bad output does not reap child");
  require(waitstats((void *)~0UL, &s) == -1, "bad status does not reap child");
  int status;
  require(waitstats(&status, &s) == pid && status == 42, "retry final-statistics wait");
  validstat(&s);
  require(waitstats(0, &s) == -1, "no remaining children");
  require(schedcheck() == 0, "API invariants");
  printf("PASS API validation, atomic rejection, enumeration, and safe reaping\n");
}

static void
accounting_test(void)
{
  configure(0, 0, 0);
  int pid = fork();
  require(pid >= 0, "fork short-burst worker");
  if (!pid) {
    for (int i = 0; i < 20; i++) {
      spin(500);
      require(schedyield() == 0, "voluntary yield");
    }
    struct schedstat s = selfstat();
    require(s.cpu_time > 0, "sub-tick execution is charged");
    validstat(&s);
    exit(0);
  }
  reap_ok(pid);
  struct sched_info i = sysinfo();
  for (int cpu = 0; cpu < SCHED_CPUS; cpu++)
    require(i.busy_time[cpu] <= i.now - i.online_at[cpu], "per-CPU busy-time upper bound");
  printf("PASS sub-tick accounting and exact CPU/state-time conservation\n");
}

static void
policy_test(void)
{
  if (!SCHED_MLFQ) {
    printf("SKIP feedback policy under DEFAULT\n");
    return;
  }
  configure(0, 0, 0);
  int pid = fork();
  require(pid >= 0, "fork demotion worker");
  if (!pid) {
    uint64 top = 14 * TIMER_INTERVAL;
    burn_until(top + 9 * TIMER_INTERVAL);
    struct schedstat s = selfstat();
    require(s.level == 3 && s.demotions == 3, "complete demotion and bottom rotation");
    require(s.queue_time[0] == 2 * TIMER_INTERVAL &&
            s.queue_time[1] == 4 * TIMER_INTERVAL &&
            s.queue_time[2] == 8 * TIMER_INTERVAL, "exact elapsed allotments at each level");
    require(s.dispatches <= s.rotations + 4, "multi-tick turns are not split every tick");
    validstat(&s);
    exit(0);
  }
  reap_ok(pid);

  struct sched_config c = configure(0, 0, 0);
  c.quantum[0] = 2;
  c.allotment[0] = 20;
  require(schedctl(SCHED_SET, &c) == 0, "set multi-quantum allotment");
  pid = fork();
  require(pid >= 0, "fork allotment worker");
  if (!pid) {
    burn_until(7 * TIMER_INTERVAL);
    struct schedstat s = selfstat();
    require(s.level == 0 && s.demotions == 0 && s.rotations >= 3,
            "quantum rotation is independent of allotment demotion");
    require(s.allot_used >= 7 * TIMER_INTERVAL, "cumulative allotment survives rotations");
    exit(0);
  }
  reap_ok(pid);
  printf("PASS separate quanta/allotments, exact demotion, and bottom service\n");
}

static void
blocking_test(void)
{
  configure(0, 0, 0);
  int pid = fork();
  require(pid >= 0, "fork budget preservation worker");
  if (!pid) {
    burn_until(3 * TIMER_INTERVAL);
    struct schedstat before = selfstat();
    pause(3);
    struct schedstat after = selfstat();
    require(after.sleep_time > before.sleep_time, "sleep duration charged separately");
    require(after.wakeups > before.wakeups, "wakeup delay observed");
    if (SCHED_MLFQ)
      require(after.level == before.level && after.allot_used >= before.allot_used,
              "blocking does not refresh CPU allotment");
    validstat(&after);
    exit(0);
  }
  reap_ok(pid);
  if (SCHED_MLFQ) {
    configure(0, 0, 0);
    pid = fork();
    require(pid >= 0, "fork yield adversary");
    if (!pid) {
      while (selfstat().cpu_time < 3 * TIMER_INTERVAL) {
        spin(500);
        schedyield();
      }
      struct schedstat s = selfstat();
      require(s.level >= 1 && s.queue_time[0] == 2 * TIMER_INTERVAL,
              "repeated short yields cannot evade cumulative demotion");
      require(s.dispatches > s.rotations, "adversary actually yielded early");
      exit(0);
    }
    reap_ok(pid);
  }
  printf("PASS blocking budgets, early-yield adversary, and wakeup accounting\n");
}

static void
migration_test(void)
{
  configure(0, 0, 0);
  uint64 before = selfstat().migrations;
  for (int cpu = 0; cpu < SCHED_CPUS; cpu++) {
    require(setaffinity(1U << cpu) == 0, "pin to online CPU");
    burn_for(TIMER_INTERVAL / 4);
    struct schedstat s = selfstat();
    require(s.cpu == cpu && s.affinity == (1U << cpu), "affinity effective before syscall returns");
    require(s.cpu_time_by_cpu[cpu] > 0, "execution accounted on selected CPU");
    validstat(&s);
  }
  require(selfstat().migrations >= before + SCHED_CPUS - 1, "migration count");
  require(setaffinity(allcpus) == 0, "restore all CPUs");
  printf("PASS affinity enforcement and migrations across %d CPUs\n", SCHED_CPUS);
}

static void
parallel_test(void)
{
  configure(0, 0, 0);
  int gate[2], ready[2];
  require(pipe(gate) == 0 && pipe(ready) == 0, "parallel barriers");
  for (int cpu = 0; cpu < SCHED_CPUS; cpu++) {
    int pid = fork();
    require(pid >= 0, "fork parallel worker");
    if (!pid) {
      close(gate[1]); close(ready[0]);
      require(setaffinity(1U << cpu) == 0, "pin parallel worker");
      require(write(ready[1], "r", 1) == 1, "worker ready");
      char token;
      require(read(gate[0], &token, 1) == 1, "parallel release");
      close(gate[0]); close(ready[1]);
      burn_for(4 * TIMER_INTERVAL);
      struct schedstat s = selfstat();
      require(s.cpu_time_by_cpu[cpu] >= 4 * TIMER_INTERVAL, "pinned parallel CPU service");
      validstat(&s);
      exit(0);
    }
  }
  close(gate[0]); close(ready[1]);
  char token;
  for (int cpu = 0; cpu < SCHED_CPUS; cpu++)
    require(read(ready[0], &token, 1) == 1, "all CPUs ready");
  for (int cpu = 0; cpu < SCHED_CPUS; cpu++)
    require(write(gate[1], "x", 1) == 1, "release parallel CPU");
  close(gate[1]); close(ready[0]);
  for (int cpu = 0; cpu < SCHED_CPUS; cpu++) {
    int status;
    struct schedstat s;
    require(waitstats(&status, &s) > 0 && status == 0, "parallel worker completed");
    validstat(&s);
  }
  require(schedcheck() == 0, "unique ownership after parallel execution");
  printf("PASS parallel execution and unique ownership on all CPUs\n");
}

static void
fairness_test(void)
{
  configure(0, 0, 0);
  require(setaffinity(1) == 0, "pin fairness experiment");
  int gate[2];
  require(pipe(gate) == 0, "fairness barrier");
  for (int n = 0; n < 3; n++) {
    int pid = fork();
    require(pid >= 0, "fork fairness worker");
    if (!pid) {
      close(gate[1]);
      uint64 end;
      require(read(gate[0], &end, sizeof(end)) == sizeof(end), "fairness start");
      close(gate[0]);
      while (selfstat().now < end)
        spin(4000);
      exit(0);
    }
  }
  close(gate[0]);
  uint64 end = selfstat().now + 100 * TIMER_INTERVAL;
  for (int n = 0; n < 3; n++)
    require(write(gate[1], &end, sizeof(end)) == sizeof(end), "common fairness deadline");
  close(gate[1]);
  uint64 lo = ~0UL, hi = 0;
  for (int n = 0; n < 3; n++) {
    struct schedstat s;
    int status;
    require(waitstats(&status, &s) > 0 && status == 0, "fairness child");
    validstat(&s);
    require(s.cpu_time >= 14 * TIMER_INTERVAL, "all continuously runnable peers progress");
    if (s.cpu_time < lo) lo = s.cpu_time;
    if (s.cpu_time > hi) hi = s.cpu_time;
  }
  require(hi - lo <= 11 * TIMER_INTERVAL, "bounded service skew over a common window");
  require(setaffinity(allcpus) == 0, "restore fairness affinity");
  printf("PASS fixed-window fairness: service %lu..%lu us\n", lo / 10, hi / 10);
}

static void
preemption_test(void)
{
  if (!SCHED_MLFQ)
    return;
  struct sched_config c = configure(0, 0, 0);
  c.quantum[3] = 32; c.allotment[3] = 64;
  require(schedctl(SCHED_SET, &c) == 0, "long low-priority quantum");
  require(setaffinity(1) == 0, "pin priority experiment");
  int ready[2];
  require(pipe(ready) == 0, "priority readiness pipe");
  int pid = fork();
  require(pid >= 0, "fork low-priority worker");
  if (!pid) {
    close(ready[0]);
    burn_until(14 * TIMER_INTERVAL);
    require(write(ready[1], "r", 1) == 1, "low-priority readiness");
    close(ready[1]);
    for (;;)
      spin(4000);
  }
  close(ready[1]);
  char token;
  require(read(ready[0], &token, 1) == 1, "wait for Q3 subject");
  close(ready[0]);
  struct schedstat before = selfstat();
  pause(2);
  struct schedstat after = selfstat();
  // pause() rechecks its deadline after each timer-channel wakeup, so one
  // call can contain several sleep/wakeup observations.
  uint64 observations = after.wakeups - before.wakeups;
  require(observations > 0, "priority wakeups observed");
  require(after.wake_delay - before.wake_delay < 3 * TIMER_INTERVAL * observations,
          "higher priority preempts a partial long quantum promptly");
  require(kill(pid) == 0, "stop priority worker");
  int status;
  struct schedstat s;
  require(waitstats(&status, &s) == pid && status == -1, "reap killed worker");
  validstat(&s);
  require(setaffinity(allcpus) == 0, "restore priority affinity");
  printf("PASS higher-priority preemption before low-priority quantum expiry\n");
}

static void
boost_test(void)
{
  if (!SCHED_MLFQ)
    return;
  configure(40, 0, 0);
  int pid = fork();
  require(pid >= 0, "fork boost sleeper");
  if (!pid) {
    burn_until(14 * TIMER_INTERVAL);
    struct schedstat before = selfstat();
    require(before.level == 3, "sleeper reached Q3 before boost");
    pause(90);
    struct schedstat after = selfstat();
    require(after.boosts >= before.boosts + 2, "multiple periodic boost epochs");
    require(after.level == 0 && after.allot_used < 2 * TIMER_INTERVAL, "boosted sleeper has fresh budget");
    require(after.sleep_time - before.sleep_time >= 80 * TIMER_INTERVAL,
            "boost does not wake a sleeping process");
    validstat(&after);
    exit(0);
  }
  reap_ok(pid);
  printf("PASS repeated global boosts include sleepers without waking them\n");
}

static void
inheritance_test(void)
{
  if (!SCHED_MLFQ)
    return;
  configure(0, 1, 0);
  burn_for(15 * TIMER_INTERVAL);
  require(selfstat().level == 3, "parent reached bottom");
  struct sched_config trace_config;
  require(schedctl(SCHED_GET, &trace_config) == 0, "read trace settings");
  trace_config.trace = 1;
  require(schedctl(SCHED_TRACE, &trace_config) == 0, "enable observational trace");
  require(selfstat().level == 3, "trace observation preserves priority");
  trace_config.trace = 0;
  require(schedctl(SCHED_TRACE, &trace_config) == 0, "disable observational trace");
  int pid = fork();
  require(pid >= 0, "fork inheritance worker");
  if (!pid) {
    struct schedstat s = selfstat();
    require(s.level == 3 && s.demotions == 0, "inherit priority without copying lifetime counters");
    char *args[] = { "schedtest", "exec-check", 0 };
    exec(args[0], args);
    require(0, "exec inheritance worker");
  }
  reap_ok(pid);
  configure(0, 0, 0);
  printf("PASS fork debt inheritance and exec history preservation\n");
}

static void
lifecycle_test(void)
{
  configure(0, 0, 0);
  for (int n = 0; n < 20; n++) {
    int pid = fork();
    require(pid >= 0, "fork reusable slot");
    if (!pid) {
      struct schedstat s = selfstat();
      require(s.demotions == 0 && s.boosts == 0, "reused slot has fresh counters");
      exit(0);
    }
    reap_ok(pid);
  }
  int ready[2];
  require(pipe(ready) == 0, "kill sleeper readiness");
  int pid = fork();
  require(pid >= 0, "fork kill sleeper");
  if (!pid) {
    close(ready[0]);
    write(ready[1], "r", 1);
    close(ready[1]);
    pause(1000);
    exit(7);
  }
  close(ready[1]);
  char token;
  require(read(ready[0], &token, 1) == 1, "sleeper started");
  close(ready[0]);
  require(kill(pid) == 0, "kill sleeping or about-to-sleep child");
  int status;
  struct schedstat s;
  require(waitstats(&status, &s) == pid && status == -1, "kill/sleep race terminates");
  validstat(&s);
  printf("PASS slot reuse and kill/sleep lifecycle races\n");
}

static void
reconfiguration_test(void)
{
  configure(3, 0, 0);
  for (int n = 0; n < 8; n++) {
    int pid = fork();
    require(pid >= 0, "fork concurrent configuration worker");
    if (!pid) {
      for (int j = 0; j < 20; j++) {
        spin(5000);
        if (j % 2) schedyield();
        else pause(1);
      }
      exit(0);
    }
  }
  for (int n = 0; n < 20; n++) {
    struct sched_config c = configure(n % 2 ? 2 : 5, 0, 0);
    for (int q = 0; q < NQUEUE; q++) {
      c.quantum[q] *= n % 2 + 1;
      c.allotment[q] *= n % 2 + 1;
    }
    require(schedctl(SCHED_SET, &c) == 0, "atomic concurrent configuration");
    require(schedcheck() == 0, "invariants during configuration/boost races");
    pause(1);
  }
  for (int n = 0; n < 8; n++) {
    struct schedstat s;
    int status;
    require(waitstats(&status, &s) > 0 && status == 0, "concurrent config child");
    validstat(&s);
  }
  configure(0, 0, 0);
  printf("PASS concurrent budget changes, frequent boosts, and blocking workers\n");
}

static void
trace_case(void)
{
  configure(0, 0, 1);
  int gate[2];
  require(pipe(gate) == 0, "trace barrier");
  for (int n = 0; n < 6; n++) {
    int pid = fork();
    require(pid >= 0, "fork traced worker");
    if (!pid) {
      close(gate[1]);
      int cpu = n < 3 ? 0 : (n - 2) % SCHED_CPUS;
      require(setaffinity(1U << cpu) == 0, "trace affinity");
      char token;
      require(read(gate[0], &token, 1) == 1, "trace release");
      close(gate[0]);
      for (int j = 0; j < 120; j++) {
        spin(200000);
        if (j % 3 == 0) schedyield();
        if (j == 55) pause(1);
      }
      exit(0);
    }
  }
  close(gate[0]);
  for (int n = 0; n < 6; n++)
    require(write(gate[1], "x", 1) == 1, "release traced worker");
  close(gate[1]);
  for (int n = 0; n < 6; n++) {
    struct schedstat s;
    int status;
    require(waitstats(&status, &s) > 0 && status == 0, "traced child completed");
    validstat(&s);
  }
  require(schedcheck() == 0, "trace scenario invariants");
  printf("SCHEDTEST ALL PASSED trace scenario\n");
}

static void
trace_overflow(void)
{
  configure(0, 0, 1);
  struct sched_cursor cursor = { sysinfo().trace_first, 0 };
  for (int n = 0; n < TRACE_CAPACITY / 3 + 64; n++)
    schedyield();
  struct sched_event event;
  require(schedtrace(&cursor, &event, 1) == 1 && cursor.lost > 0,
          "trace overflow is reported, never silently accepted");
  require(schedtrace(&cursor, &event, 0) == -1 &&
          schedtrace(&cursor, &event, TRACE_BATCH + 1) == -1,
          "trace capacity validation");
  cursor.next = sysinfo().trace_next + 100;
  require(schedtrace(&cursor, &event, 1) == -1, "future cursor rejected");
  configure(0, 0, 0);
  printf("SCHEDTEST ALL PASSED trace overflow\n");
}

int
main(int argc, char **argv)
{
  if (argc > 1 && strcmp(argv[1], "exec-check") == 0) {
    require(selfstat().level == 3, "exec did not reset inherited priority");
    exit(0);
  }
  struct sched_info info = sysinfo();
  allcpus = (1U << SCHED_CPUS) - 1;
  require(info.online_mask == allcpus, "all configured CPUs online");
  require(schedctl(SCHED_GET, &original) == 0, "save configuration");
  require(setaffinity(1) == 0, "pin test controller");
  if (argc > 1 && strcmp(argv[1], "trace") == 0) {
    trace_case();
    exit(0);
  }
  if (argc > 1 && strcmp(argv[1], "overflow") == 0) {
    trace_overflow();
    exit(0);
  }
  printf("schedtest policy=%s cpus=%d\n", SCHED_MLFQ ? "MLFQ" : "DEFAULT", SCHED_CPUS);
  api_test();
  accounting_test();
  policy_test();
  blocking_test();
  migration_test();
  parallel_test();
  fairness_test();
  preemption_test();
  boost_test();
  inheritance_test();
  lifecycle_test();
  reconfiguration_test();
  require(setaffinity(allcpus) == 0, "restore CPU mask");
  require(schedctl(SCHED_SET, &original) == 0, "restore original configuration");
  require(schedcheck() == 0, "final global invariants");
  printf("SCHEDTEST ALL PASSED\n");
  exit(0);
}
