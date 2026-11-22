#ifndef USER_SCHEDUTIL_H
#define USER_SCHEDUTIL_H
#include "kernel/types.h"
#include "kernel/param.h"
#include "kernel/sched.h"
#include "user/user.h"

static volatile uint64 sched_sink;
static inline void
require(int ok, const char *message)
{
  if (!ok) {
    fprintf(2, "FAIL pid=%d: %s\n", getpid(), message);
    exit(1);
  }
}

static inline struct schedstat
selfstat(void)
{
  struct schedstat s;
  require(getpinfo(-1, &s) == 0, "self snapshot");
  return s;
}

static inline struct sched_info
sysinfo(void)
{
  struct sched_info s;
  require(schedinfo(&s) == 0, "system snapshot");
  return s;
}

static inline void
spin(int iterations)
{
  for (int i = 0; i < iterations; i++)
    sched_sink = (sched_sink * 1664525 + 1013904223) ^ (sched_sink >> 7);
}

static inline void
burn_until(uint64 target)
{
  while (selfstat().cpu_time < target)
    spin(1000);
}

static inline void
burn_for(uint64 duration)
{
  burn_until(selfstat().cpu_time + duration);
}

static inline void
validstat(struct schedstat *s)
{
  uint64 queues = 0, cpus = 0;
  for (int q = 0; q < NQUEUE; q++)
    queues += s->queue_time[q];
  for (int c = 0; c < SCHED_MAXCPU; c++)
    cpus += s->cpu_time_by_cpu[c];
  require(s->cpu_time == queues && queues == cpus, "CPU time conservation");
  uint64 elapsed = (s->state == ZOMBIE ? s->exited : s->now) - s->born;
  require(s->cpu_time + s->setup_time + s->wait_time + s->sleep_time == elapsed,
          "process state-time conservation");
  require(s->level >= 0 && s->level < NQUEUE, "priority in range");
}

static inline void
reap_ok(int pid)
{
  int status;
  struct schedstat s;
  require(waitstats(&status, &s) == pid && status == 0, "child completed successfully");
  require(s.pid == pid && s.state == ZOMBIE, "final PID and state");
  validstat(&s);
}
#endif
