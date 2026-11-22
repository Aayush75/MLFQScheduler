#include "schedutil.h"

int
main(void)
{
  // Console printing performs many syscalls and can itself fill the ring.
  // Freeze observation before taking the end cursor; policy budgets and the
  // boost deadline are preserved by SCHED_TRACE. Re-enable explicitly later.
  struct sched_config config;
  require(schedctl(SCHED_GET, &config) == 0, "read trace state");
  config.trace = 0;
  require(schedctl(SCHED_TRACE, &config) == 0, "freeze trace capture");
  struct sched_info info = sysinfo();
  struct sched_cursor cursor = {0, 0};
  struct sched_event events[8];
  // The frozen interval cannot be overwritten while the console drains.
  uint64 end = info.trace_next;
  printf("EVENT,seq,time,type,pid,cpu,level,slice,allot,amount,detail\n");
  while (cursor.next < end) {
    int n = schedtrace(&cursor, events, 8);
    require(n >= 0, "read trace");
    if (!n)
      break;
    for (int j = 0; j < n; j++) {
      struct sched_event *e = &events[j];
      if (e->seq >= end)
        break;
      printf("EVENT,%lu,%lu,%d,%d,%d,%d,%lu,%lu,%lu,%d\n",
             e->seq, e->time, e->type, e->pid, e->cpu, e->level,
             e->slice, e->allot, e->amount, e->detail);
    }
  }
  printf("TRACE_DONE,lost=%lu,end=%lu\n", cursor.lost, end);
  exit(cursor.lost ? 1 : 0);
}
