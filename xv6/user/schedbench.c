#include "schedutil.h"

#define MAX_WORKERS 32
#define IO_ROUNDS 24

enum role { CPU_JOB, BLOCK_JOB, SHORT_JOB, PRODUCER, READER, PHASE_JOB, FAIR_JOB };
struct release { uint64 time, deadline; };
struct message { uint64 sent; uint sequence; };
struct result {
  int pid, index, role, reserved;
  uint64 release, first, done, cpu_time, wait_time, sleep_time;
  uint64 dispatches, demotions, boosts, migrations, wakeups, wake_delay;
  uint64 lifetime_max_wake_delay, pipe_samples, pipe_delay, pipe_max;
  uint64 work_units, checksum, queue_time[NQUEUE], cpu_time_by_cpu[SCHED_MAXCPU];
};
static struct result results[MAX_WORKERS];

typedef char result_fits_pipe[(sizeof(struct result) <= 512) ? 1 : -1];

static void
read_exact(int fd, void *buffer, int bytes)
{
  int got = 0;
  while (got < bytes) {
    int n = read(fd, (char *)buffer + got, bytes - got);
    require(n > 0, "complete benchmark record");
    got += n;
  }
}

static void
work(int units)
{
  for (int n = 0; n < units; n++)
    spin(100000);
}

static void
worker(int index, int role, int units, int gate[2], int output[2], int io[2],
       int mutex[2], int finished[2], int collect[2])
{
  close(gate[1]); close(output[0]); close(finished[0]); close(collect[1]);
  if (role != READER) close(io[0]);
  if (role != PRODUCER) close(io[1]);
  struct release start;
  read_exact(gate[0], &start, sizeof(start));
  close(gate[0]);
  struct schedstat before = selfstat();
  struct result r;
  memset(&r, 0, sizeof(r));
  r.pid = getpid(); r.index = index; r.role = role;
  r.release = start.time; r.first = before.now;
  if (role == READER) {
    struct message m;
    for (int n = 0; n < IO_ROUNDS; n++) {
      read_exact(io[0], &m, sizeof(m));
      require(m.sequence == (uint)n, "pipe message order");
      uint64 delay = selfstat().now - m.sent;
      r.pipe_samples++; r.pipe_delay += delay;
      if (delay > r.pipe_max) r.pipe_max = delay;
      spin(4000);
    }
    close(io[0]);
  } else if (role == PRODUCER) {
    for (int n = 0; n < IO_ROUNDS; n++) {
      pause(1);
      struct message m = { .sent = selfstat().now, .sequence = n };
      require(write(io[1], &m, sizeof(m)) == sizeof(m), "pipe message write");
    }
    close(io[1]);
  } else if (role == BLOCK_JOB || role == PHASE_JOB) {
    if (role == PHASE_JOB) {
      work(units / 2);
      r.work_units += units / 2;
    }
    for (int n = 0; n < IO_ROUNDS; n++) {
      spin(4000);
      pause(1);
    }
  } else if (role == FAIR_JOB) {
    while (selfstat().now < start.deadline) {
      spin(100000);
      r.work_units++;
    }
  } else {
    int amount = role == SHORT_JOB ? units / 8 + 1 : units;
    work(amount);
    r.work_units = amount;
  }
  struct schedstat after = selfstat();
  validstat(&after);
  r.done = after.now;
  r.cpu_time = after.cpu_time - before.cpu_time;
  r.wait_time = after.wait_time - before.wait_time;
  r.sleep_time = after.sleep_time - before.sleep_time;
  r.dispatches = after.dispatches - before.dispatches;
  r.demotions = after.demotions - before.demotions;
  r.boosts = after.boosts - before.boosts;
  r.migrations = after.migrations - before.migrations;
  r.wakeups = after.wakeups - before.wakeups;
  r.wake_delay = after.wake_delay - before.wake_delay;
  r.lifetime_max_wake_delay = after.max_wake_delay;
  r.checksum = sched_sink;
  for (int q = 0; q < NQUEUE; q++)
    r.queue_time[q] = after.queue_time[q] - before.queue_time[q];
  for (int cpu = 0; cpu < SCHED_MAXCPU; cpu++)
    r.cpu_time_by_cpu[cpu] = after.cpu_time_by_cpu[cpu] - before.cpu_time_by_cpu[cpu];
  require(r.cpu_time + r.wait_time + r.sleep_time == r.done - r.first,
          "timed phase state-time conservation");

  // Completed workers park until ALL timed work is finished. Only then are
  // result records serialized. The token prevents byte interleaving when a
  // shared xv6 pipe fills (small writes alone do not guarantee atomicity).
  require(write(finished[1], "d", 1) == 1, "completion notification");
  close(finished[1]);
  char token;
  read_exact(collect[0], &token, 1);
  close(collect[0]);
  read_exact(mutex[0], &token, 1);
  require(write(output[1], &r, sizeof(r)) == sizeof(r), "serialized benchmark record");
  require(write(mutex[1], "x", 1) == 1, "release result writer");
  close(mutex[0]); close(mutex[1]); close(output[1]);
  exit(0);
}

int
main(int argc, char **argv)
{
  char *mode = argc > 1 ? argv[1] : "mixed";
  int units = argc > 2 ? atoi(argv[2]) : 1200;
  int count = argc > 3 ? atoi(argv[3]) : 2 * SCHED_CPUS;
  if (count < 4) count = 4;
  require(units > 0 && units <= 100000 && count <= MAX_WORKERS,
          "usage: schedbench cpu|mixed|short|pipe|phase|fair [units=1200] [workers]");
  require(!strcmp(mode,"cpu") || !strcmp(mode,"mixed") || !strcmp(mode,"short") ||
          !strcmp(mode,"pipe") || !strcmp(mode,"phase") || !strcmp(mode,"fair"),
          "known benchmark mode");
  struct sched_config c;
  require(schedctl(SCHED_GET, &c) == 0, "benchmark configuration");
  c.fork_inherit = 0; // independent arrivals all start with the same fresh Q0 budget
  require(schedctl(SCHED_SET, &c) == 0, "equalize independent job admission");
  int gate[2], output[2], io[2], mutex[2], finished[2], collect[2];
  require(pipe(gate) == 0 && pipe(output) == 0 && pipe(io) == 0 &&
          pipe(mutex) == 0 && pipe(finished) == 0 && pipe(collect) == 0,
          "benchmark pipes fit within NOFILE");
  require(write(mutex[1], "x", 1) == 1, "initialize writer token");
  for (int n = 0; n < count; n++) {
    int role = CPU_JOB;
    if (!strcmp(mode,"fair")) role = FAIR_JOB;
    if (n == count - 1) {
      if (!strcmp(mode,"mixed")) role = BLOCK_JOB;
      if (!strcmp(mode,"short")) role = SHORT_JOB;
      if (!strcmp(mode,"phase")) role = PHASE_JOB;
      if (!strcmp(mode,"pipe")) role = READER;
    }
    if (!strcmp(mode,"pipe") && n == 0) role = PRODUCER;
    int pid = fork();
    require(pid >= 0, "fork benchmark worker");
    if (!pid)
      worker(n, role, units, gate, output, io, mutex, finished, collect);
  }
  close(gate[0]); close(output[1]); close(io[0]); close(io[1]);
  close(mutex[0]); close(mutex[1]); close(finished[1]); close(collect[0]);
  struct release start = { .time = selfstat().now };
  // In fair mode, units denotes milliseconds in a common observation window.
  start.deadline = start.time + (uint64)units * (SCHED_TIMEBASE / 1000);
  for (int n = 0; n < count; n++)
    require(write(gate[1], &start, sizeof(start)) == sizeof(start), "release all workers");
  close(gate[1]);
  char token;
  for (int n = 0; n < count; n++)
    read_exact(finished[0], &token, 1);
  close(finished[0]);
  for (int n = 0; n < count; n++)
    require(write(collect[1], "c", 1) == 1, "release result collection");
  close(collect[1]);
  for (int n = 0; n < count; n++)
    read_exact(output[0], &results[n], sizeof(results[n]));
  close(output[0]);
  for (int n = 0; n < count; n++) {
    int status;
    struct schedstat s;
    require(waitstats(&status, &s) > 0 && status == 0, "reap benchmark worker");
    validstat(&s);
  }
  require(schedcheck() == 0, "benchmark final invariants");
  printf("RESULT,mode,worker,pid,role,release,first,done,cpu_time,wait_time,sleep_time,dispatches,demotions,boosts,migrations,wakeups,wake_delay,lifetime_max_wake_delay,pipe_samples,pipe_delay,pipe_max,work_units,checksum,q0,q1,q2,q3,cpu0,cpu1,cpu2,cpu3,cpu4,cpu5,cpu6,cpu7\n");
  for (int n = 0; n < count; n++) {
    struct result *r = &results[n];
    printf("RESULT,%s,%d,%d,%d,%lu,%lu,%lu,%lu,%lu,%lu,%lu,%lu,%lu,%lu,%lu,%lu,%lu,%lu,%lu,%lu,%lu,%lu",
           mode, r->index, r->pid, r->role, r->release, r->first, r->done,
           r->cpu_time, r->wait_time, r->sleep_time, r->dispatches, r->demotions,
           r->boosts, r->migrations, r->wakeups, r->wake_delay, r->lifetime_max_wake_delay,
           r->pipe_samples, r->pipe_delay, r->pipe_max, r->work_units, r->checksum);
    for (int q = 0; q < NQUEUE; q++) printf(",%lu", r->queue_time[q]);
    for (int cpu = 0; cpu < SCHED_MAXCPU; cpu++) printf(",%lu", r->cpu_time_by_cpu[cpu]);
    printf("\n");
  }
  printf("BENCH DONE\n");
  exit(0);
}
