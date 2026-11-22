#include "schedutil.h"

#define ARRIVALS 40

int
main(int argc, char **argv)
{
  require(SCHED_MLFQ, "pressure diagnostic requires MLFQ");
  uint boost = argc > 1 ? atoi(argv[1]) : 64;
  struct sched_config c = { .version = SCHED_VERSION,
    .quantum = {1,2,4,8}, .allotment = {2,4,8,16},
    .boost_ticks = boost, .fork_inherit = 0, .trace = 0 };
  require(schedctl(SCHED_SET, &c) == 0, "pressure configuration");
  require(setaffinity(1) == 0, "pin controlled load to CPU0");
  int ready[2], gate[2];
  require(pipe(ready) == 0 && pipe(gate) == 0, "pressure barriers");
  int low = fork();
  require(low >= 0, "fork old CPU worker");
  if (!low) {
    close(ready[0]); close(gate[0]); close(gate[1]);
    burn_until(14 * TIMER_INTERVAL);
    require(write(ready[1], "l", 1) == 1, "old worker reached Q3");
    close(ready[1]);
    for (;;)
      spin(200000);
  }
  char token;
  require(read(ready[0], &token, 1) == 1, "wait for old worker");
  int high[ARRIVALS];
  for (int n = 0; n < ARRIVALS; n++) {
    int pid = fork();
    require(pid >= 0, "pre-create fixed high-priority burst");
    if (!pid) {
      close(ready[0]); close(gate[1]);
      require(write(ready[1], "h", 1) == 1, "high-priority worker ready");
      close(ready[1]);
      require(read(gate[0], &token, 1) == 1, "release fixed burst");
      close(gate[0]);
      uint64 target = selfstat().cpu_time + 4 * TIMER_INTERVAL;
      // Coarse work chunks keep the diagnostic trace bounded. Accounting
      // still retains the exact interval and any final-chunk overshoot.
      while (selfstat().cpu_time < target)
        spin(200000);
      exit(0);
    }
    high[n] = pid;
  }
  close(ready[1]); close(gate[0]);
  for (int n = 0; n < ARRIVALS; n++)
    require(read(ready[0], &token, 1) == 1, "all arrivals blocked behind barrier");
  close(ready[0]);
  struct schedstat s;
  // Readiness notifications precede the blocking read. Verify that every
  // arrival has actually reached its barrier before publishing the burst.
  int sleeping = 0;
  for (int attempt = 0; attempt < 1000; attempt++) {
    sleeping = 0;
    for (int slot = 0; slot < NPROC; slot++)
      if (getpinfo(slot, &s) == 0 && s.state == SLEEPING)
        for (int n = 0; n < ARRIVALS; n++)
          if (s.pid == high[n]) sleeping++;
    if (sleeping == ARRIVALS) break;
    schedyield();
  }
  require(sleeping == ARRIVALS, "all arrivals parked before burst");
  int found = 0;
  for (int slot = 0; slot < NPROC; slot++)
    if (getpinfo(slot, &s) == 0 && s.pid == low) {
      require(s.level == 3, "setup completed before first boost");
      found = 1;
      break;
    }
  require(found, "find old worker");
  c.trace = 1;
  require(schedctl(SCHED_TRACE, &c) == 0, "observe without resetting priority");
  char tokens[ARRIVALS];
  memset(tokens, 'x', sizeof(tokens));
  require(selfstat().level < 3, "controller retains priority during publication");
  uint64 start = selfstat().now, end = start + 100 * TIMER_INTERVAL;
  // One bounded pipe write publishes all tokens under the pipe lock; timer
  // preemption cannot split the burst into generator-dependent batches.
  require(write(gate[1], tokens, sizeof(tokens)) == sizeof(tokens),
          "publish identical offered load atomically");
  close(gate[1]);
  for (int n = 0; n < ARRIVALS; n++) {
    int status;
    require(waitstats(&status, &s) > 0 && status == 0, "high-priority arrival completed");
    validstat(&s);
  }
  while (selfstat().now < end)
    pause(1);
  require(kill(low) == 0, "stop old CPU worker after observation window");
  int status;
  require(waitstats(&status, &s) == low && status == -1, "reap old worker");
  validstat(&s);
  require(schedcheck() == 0, "pressure final invariants");
  // The host integrates old-worker CHARGE intervals clipped to [start,end].
  // Final lifetime totals would include service after the measured window.
  printf("LOAD,boost,old_pid,start,end,arrivals,target_cpu\n");
  printf("LOAD,%d,%d,%lu,%lu,%d,%lu\n", boost, low, start, end,
         ARRIVALS, (uint64)4 * TIMER_INTERVAL);
  printf("LOADIDS");
  for (int n = 0; n < ARRIVALS; n++) printf(",%d", high[n]);
  printf("\nPRESSURE DONE\n");
  exit(0);
}
