#ifndef XV6_SCHED_H
#define XV6_SCHED_H

// All durations below are in QEMU virt's shared 10 MHz time-counter units.
// Scheduling parameters use ticks, but execution is charged on EVERY switch,
// block, exit, timer interrupt, and observation. Short bursts are not free.
#define SCHED_VERSION 2
#define NQUEUE 4
#define SCHED_MAXCPU 8
#define SCHED_TIMEBASE 10000000UL
#ifndef TIMER_INTERVAL
#define TIMER_INTERVAL 100000UL
#endif
#ifndef SCHED_CPUS
#define SCHED_CPUS 4
#endif
#ifndef SCHED_MLFQ
#define SCHED_MLFQ 1
#endif
#ifndef SCHED_DEBUG
#define SCHED_DEBUG 1
#endif
#ifndef SCHED_QUANTA
#define SCHED_QUANTA 1, 2, 4, 8
#endif
#ifndef SCHED_ALLOTMENTS
#define SCHED_ALLOTMENTS 2, 4, 8, 16
#endif
#ifndef BOOST_INTERVAL
#define BOOST_INTERVAL 100
#endif

#if SCHED_CPUS < 1 || SCHED_CPUS > SCHED_MAXCPU
#error "CPUS must be 1..8"
#endif
#if TIMER_INTERVAL < 1000 || TIMER_INTERVAL > 1000000
#error "TIMER must be 1000..1000000 time-counter units"
#endif
#if BOOST_INTERVAL < 0 || BOOST_INTERVAL > 100000
#error "BOOST must be 0..100000 ticks"
#endif

// Kept in the shared header so user tools can print states without including
// kernel-private process/context definitions.
enum procstate { UNUSED, USED, SLEEPING, RUNNABLE, RUNNING, ZOMBIE };
enum sched_reason { SR_CREATE, SR_WAKE, SR_TIMER, SR_YIELD, SR_SLEEP,
                    SR_EXIT, SR_AFFINITY };
enum sched_event_type { SE_BEGIN = 1, SE_SEED, SE_ALLOC, SE_FORK, SE_READY,
                        SE_DISPATCH, SE_CHARGE, SE_STOP, SE_FREE, SE_BOOST,
                        SE_CONFIG, SE_AFFINITY };
#define SCHED_GET 0
#define SCHED_SET 1
#define SCHED_TRACE 2              // observational toggle; preserves budgets/deadline
#define TRACE_CAPACITY 8192
#define TRACE_BATCH 32

struct sched_config {
  uint version;
  uint quantum[NQUEUE];
  uint allotment[NQUEUE];
  uint boost_ticks;             // 0 disables periodic boosting
  uint fork_inherit;            // inherit priority AND consumed budget
  uint trace;                   // bounded in-memory events; no hot-path printing
};

struct schedstat {
  int pid, state, level, cpu;
  uint affinity;
  int last_cpu;
  uint64 born, first_run, exited, now;
  uint64 cpu_time, queue_time[NQUEUE], cpu_time_by_cpu[SCHED_MAXCPU];
  uint64 setup_time, wait_time, sleep_time, max_wait;
  uint64 dispatches, demotions, rotations, boosts, migrations;
  uint64 wakeups, wake_delay, max_wake_delay;
  uint64 slice_used, allot_used;
};

struct sched_info {
  struct sched_config config;
  uint version, policy, online_mask, cpus;
  uint ready[NQUEUE];
  uint64 boot_time, now, boosts, generation;
  uint64 dispatches[SCHED_MAXCPU], switches[SCHED_MAXCPU];
  uint64 busy_time[SCHED_MAXCPU], online_at[SCHED_MAXCPU];
  uint64 trace_next, trace_first;
};

// seq detects overwritten events; cursor.lost explicitly reports overflow.
// READY.amount=1 means front insertion; SEED/FORK/AFFINITY.amount=CPU mask;
// CHARGE.amount=elapsed execution time. STOP.detail=new process state.
struct sched_event {
  uint64 seq, time, slice, allot, amount;
  int type, pid, cpu, level, detail;
  uint reserved;
};
struct sched_cursor { uint64 next, lost; };

#endif
