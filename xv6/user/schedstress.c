#include "schedutil.h"

// A deterministic PRNG makes each worker's operation sequence reproducible.
// Interleavings still vary between runs, which is useful for race detection.
static uint
random_next(uint *state)
{
  *state = *state * 1664525U + 1013904223U;
  return *state >> 8;
}

struct report { int pid; uint seed, actions, forks, execs, kills, pipes, affinities; };

static void
child_status(int pid, int expected)
{
  struct schedstat s;
  int status;
  require(waitstats(&status, &s) == pid && status == expected, "stress child status");
  validstat(&s);
}

static void
worker(uint seed, int rounds, int output)
{
  struct report r = { .pid = getpid(), .seed = seed };
  uint state = seed;
  for (int n = 0; n < rounds; n++) {
    uint value = random_next(&state);
    switch (value % 10) {
    case 0:
      spin(value % 20000 + 1);
      break;
    case 1:
      require(schedyield() == 0, "stress voluntary yield");
      break;
    case 2:
      require(pause(value % 3 + 1) == 0, "stress timed block");
      break;
    case 3: {
      uint mask = value & 1 ? (1U << (random_next(&state) % SCHED_CPUS)) :
                  (1U << SCHED_CPUS) - 1;
      require(setaffinity(mask) == 0, "stress affinity migration");
      require(selfstat().affinity == mask, "stress affinity snapshot");
      r.affinities++;
      break;
    }
    case 4: {
      int pid = fork();
      require(pid >= 0, "stress fork");
      if (!pid) {
        spin(value % 10000);
        schedyield();
        exit(37);
      }
      child_status(pid, 37);
      r.forks++;
      break;
    }
    case 5: {
      int fds[2];
      require(pipe(fds) == 0, "stress pipe");
      int pid = fork();
      require(pid >= 0, "stress pipe fork");
      if (!pid) {
        close(fds[0]);
        require(write(fds[1], &value, sizeof(value)) == sizeof(value), "stress pipe send");
        close(fds[1]);
        exit(0);
      }
      close(fds[1]);
      uint got;
      require(read(fds[0], &got, sizeof(got)) == sizeof(got) && got == value, "stress pipe payload");
      require(read(fds[0], &got, sizeof(got)) == 0, "stress pipe EOF");
      close(fds[0]);
      child_status(pid, 0);
      r.pipes++;
      break;
    }
    case 6: {
      struct schedstat s = selfstat();
      validstat(&s);
      int slot = random_next(&state) % NPROC;
      if (getpinfo(slot, &s) == 0)
        validstat(&s);
      break;
    }
    case 7: {
      struct sched_config c = { .version = SCHED_VERSION };
      uint scale = value % 3 + 1;
      for (int q = 0; q < NQUEUE; q++) {
        c.quantum[q] = (1U << q) * scale;
        c.allotment[q] = c.quantum[q] * (value % 2 + 2);
      }
      c.boost_ticks = value % 7;
      c.fork_inherit = value & 1;
      require(schedctl(SCHED_SET, &c) == 0, "stress global reconfiguration");
      break;
    }
    case 8: {
      int ready[2];
      require(pipe(ready) == 0, "stress kill barrier");
      int pid = fork();
      require(pid >= 0, "stress kill fork");
      if (!pid) {
        close(ready[0]);
        write(ready[1], "r", 1);
        close(ready[1]);
        for (;;)
          pause(100000);
      }
      close(ready[1]);
      char token;
      require(read(ready[0], &token, 1) == 1, "stress kill readiness");
      close(ready[0]);
      require(kill(pid) == 0, "stress kill");
      child_status(pid, -1);
      r.kills++;
      break;
    }
    case 9: {
      int pid = fork();
      require(pid >= 0, "stress exec fork");
      if (!pid) {
        char *args[] = { "schedstress", "leaf", 0 };
        exec(args[0], args);
        require(0, "stress exec");
      }
      child_status(pid, 23);
      r.execs++;
      break;
    }
    }
    require(schedcheck() == 0, "stress global queue/ownership invariants");
    r.actions++;
  }
  struct schedstat s = selfstat();
  validstat(&s);
  require(write(output, &r, sizeof(r)) == sizeof(r), "write stress summary");
  close(output);
  exit(0);
}

int
main(int argc, char **argv)
{
  if (argc > 1 && strcmp(argv[1], "leaf") == 0) {
    struct schedstat s = selfstat();
    validstat(&s);
    exit(23);
  }
  uint seed = argc > 1 ? atoi(argv[1]) : 1;
  int rounds = argc > 2 ? atoi(argv[2]) : 100;
  int workers = argc > 3 ? atoi(argv[3]) : 12;
  require(rounds > 0 && rounds <= 1000 && workers > 0 && workers <= 16, "stress argument range");
  struct sched_config original;
  require(schedctl(SCHED_GET, &original) == 0, "save stress configuration");
  int reports[2];
  require(pipe(reports) == 0, "stress reporting pipe");
  printf("STRESS seed=%d workers=%d rounds=%d cpus=%d policy=%s\n",
         seed, workers, rounds, SCHED_CPUS, SCHED_MLFQ ? "MLFQ" : "DEFAULT");
  for (int n = 0; n < workers; n++) {
    int pid = fork();
    require(pid >= 0, "fork stress worker");
    if (!pid) {
      close(reports[0]);
      worker(seed + 0x9e3779b9U * (n + 1), rounds, reports[1]);
    }
  }
  close(reports[1]);
  struct report total = {0};
  for (int n = 0; n < workers; n++) {
    struct report r;
    require(read(reports[0], &r, sizeof(r)) == sizeof(r), "collect all stress workers");
    require(r.actions == (uint)rounds, "every worker completed its action sequence");
    total.actions += r.actions;
    total.forks += r.forks; total.execs += r.execs; total.kills += r.kills;
    total.pipes += r.pipes; total.affinities += r.affinities;
  }
  close(reports[0]);
  for (int n = 0; n < workers; n++) {
    struct schedstat s;
    int status;
    require(waitstats(&status, &s) > 0 && status == 0, "reap stress worker");
    validstat(&s);
  }
  require(schedctl(SCHED_SET, &original) == 0, "restore stress configuration");
  require(schedcheck() == 0, "stress final invariants");
  printf("STRESS_RESULT,%d,%d,%d,%d,%d,%d\n", total.actions, total.forks,
         total.execs, total.kills, total.pipes, total.affinities);
  printf("SCHEDSTRESS ALL PASSED\n");
  exit(0);
}
