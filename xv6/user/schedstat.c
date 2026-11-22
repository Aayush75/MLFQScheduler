#include "schedutil.h"

int
main(void)
{
  struct sched_info i = sysinfo();
  printf("policy=%s cpus=%d online=%x generation=%lu timer=%lu timebase=%lu\n",
         i.policy ? "MLFQ" : "DEFAULT", i.cpus, i.online_mask, i.generation,
         (uint64)TIMER_INTERVAL, (uint64)SCHED_TIMEBASE);
  printf("quantum=%d,%d,%d,%d allotment=%d,%d,%d,%d boost=%d inherit=%d\n",
         i.config.quantum[0], i.config.quantum[1], i.config.quantum[2], i.config.quantum[3],
         i.config.allotment[0], i.config.allotment[1], i.config.allotment[2],
         i.config.allotment[3], i.config.boost_ticks, i.config.fork_inherit);
  printf("PID STATE Q CPU MASK CPU_us WAIT_us SLEEP_us DISPATCH MIGRATE DEMOTE BOOST\n");
  struct schedstat s;
  for (int slot = 0; slot < NPROC; slot++) {
    if (getpinfo(slot, &s) < 0)
      continue;
    printf("%d %d %d %d %x %lu %lu %lu %lu %lu %lu %lu\n",
           s.pid, s.state, s.level, s.cpu, s.affinity, s.cpu_time / 10,
           s.wait_time / 10, s.sleep_time / 10, s.dispatches,
           s.migrations, s.demotions, s.boosts);
  }
  require(schedcheck() == 0, "scheduler invariants");
  exit(0);
}
