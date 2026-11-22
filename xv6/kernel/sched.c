// Shared MLFQ queues and execution accounting.
//
// Lock order: existing wait/ticks locks -> p->lock -> rq.lock.
// rq.lock NEVER waits for a process lock, allocates memory, copies user memory,
// or crosses swtch(). Statistics and queue links are owned by rq.lock. Every
// process-state write also holds p->lock, so ordinary xv6 state readers remain
// valid. See scheduler() for the peek -> process lock -> revalidate protocol.
#include "types.h"
#include "param.h"
#include "memlayout.h"
#include "riscv.h"
#include "spinlock.h"
#include "proc.h"
#include "defs.h"

typedef char trace_buffer_fits_page[(TRACE_BATCH * sizeof(struct sched_event) <= PGSIZE) ? 1 : -1];

extern struct proc proc[NPROC];
static struct {
  struct spinlock lock;
  struct proc *head[NQUEUE], *tail[NQUEUE], *running[NCPU];
  struct sched_info info;
  int last_pid[NCPU];
  uint64 next_boost, trace_first, trace_next;
  struct sched_event events[TRACE_CAPACITY];
} rq;

static int
config_valid(struct sched_config *c)
{
  if (c->version != SCHED_VERSION || c->fork_inherit > 1 || c->trace > 1 ||
      c->boost_ticks > 100000)
    return 0;
  for (int q = 0; q < NQUEUE; q++)
    if (!c->quantum[q] || c->quantum[q] > 10000 ||
        c->allotment[q] < c->quantum[q] || c->allotment[q] > 10000 ||
        c->allotment[q] % c->quantum[q])
      return 0;
  return 1;
}

// Events are fixed-size records. The counter stays monotonic even across a
// trace restart; readers can distinguish a gap from a complete trace.
static void
emit(int type, struct proc *p, int cpu, int detail, uint64 amount, uint64 now)
{
  if (!rq.info.config.trace)
    return;
  uint64 seq = rq.trace_next++;
  struct sched_event *e = &rq.events[seq % TRACE_CAPACITY];
  memset(e, 0, sizeof(*e));
  e->seq = seq;
  e->time = now;
  e->type = type;
  e->pid = p ? p->ss.pid : 0;
  e->cpu = cpu;
  e->level = p ? p->ss.level : 0;
  e->slice = p ? p->ss.slice_used : 0;
  e->allot = p ? p->ss.allot_used : 0;
  e->detail = detail;
  e->amount = amount;
  if (type == SE_CONFIG)
    for (int q = 0; q < NQUEUE; q++) {
      e->slice |= (uint64)rq.info.config.quantum[q] << (16 * q);
      e->allot |= (uint64)rq.info.config.allotment[q] << (16 * q);
    }
  if (rq.trace_next - rq.trace_first > TRACE_CAPACITY)
    rq.trace_first = rq.trace_next - TRACE_CAPACITY;
}

static void
insert(struct proc *p, int front)
{
  int q = p->ss.level;
  if (p->on_rq || q < 0 || q >= NQUEUE)
    panic("enqueue");
  p->on_rq = 1;
  p->rq_prev = front ? 0 : rq.tail[q];
  p->rq_next = front ? rq.head[q] : 0;
  if (p->rq_prev)
    p->rq_prev->rq_next = p;
  else
    rq.head[q] = p;
  if (p->rq_next)
    p->rq_next->rq_prev = p;
  else
    rq.tail[q] = p;
}

static void
removeq(struct proc *p)
{
  int q = p->ss.level;
  if (!p->on_rq)
    panic("dequeue");
  if (p->rq_prev)
    p->rq_prev->rq_next = p->rq_next;
  else
    rq.head[q] = p->rq_next;
  if (p->rq_next)
    p->rq_next->rq_prev = p->rq_prev;
  else
    rq.tail[q] = p->rq_prev;
  p->rq_prev = p->rq_next = 0;
  p->on_rq = 0;
}

static struct proc *
pick(int cpu)
{
  for (int q = 0; q < NQUEUE; q++)
    for (struct proc *p = rq.head[q]; p; p = p->rq_next)
      if (p->ss.affinity & (1U << cpu))
        return p;
  return 0;
}

// Exact elapsed time is retained across short blocks/yields. A delayed timer
// may cross several allotments: split the elapsed interval across levels,
// carrying ALL excess forward. At Q3, modulo arithmetic avoids an unbounded
// loop after a long interrupt-disabled section. Quantum and allotment are
// separate: a quantum rotates peers; only an allotment causes demotion.
static void
charge(struct proc *p, uint64 now)
{
  if (p->ss.state != RUNNING)
    return;
  uint64 elapsed = now - p->run_at;
  if (!elapsed)
    return;
  int cpu = p->ss.cpu, oldlevel = p->ss.level;
  if (cpu < 0 || cpu >= NCPU || rq.running[cpu] != p)
    panic("account owner");
  p->run_at = now;
  p->ss.cpu_time += elapsed;
  p->ss.cpu_time_by_cpu[cpu] += elapsed;
  rq.info.busy_time[cpu] += elapsed;
  uint64 left = elapsed;
  while (left) {
    int q = p->ss.level;
    uint64 quantum = (uint64)rq.info.config.quantum[q] * TIMER_INTERVAL;
    uint64 allot = (uint64)rq.info.config.allotment[q] * TIMER_INTERVAL;
    uint64 part = left;
    if (SCHED_MLFQ && q < NQUEUE - 1 && part > allot - p->ss.allot_used)
      part = allot - p->ss.allot_used;
    p->ss.queue_time[q] += part;
    if (SCHED_MLFQ) {
      uint64 turns = (p->ss.slice_used + part) / quantum;
      p->ss.slice_used = (p->ss.slice_used + part) % quantum;
      p->ss.rotations += turns;
      if (turns)
        p->rotate = 1;
      p->ss.allot_used += part;
      if (q == NQUEUE - 1)
        p->ss.allot_used %= allot;
      else if (p->ss.allot_used == allot) {
        p->ss.level++;
        p->ss.demotions++;
        p->ss.allot_used = p->ss.slice_used = 0;
        p->rotate = 1;
      }
    }
    left -= part;
  }
  emit(SE_CHARGE, p, cpu, oldlevel, elapsed, now);
}

static void
charge_all(uint64 now)
{
  for (int cpu = 0; cpu < NCPU; cpu++)
    if (rq.running[cpu])
      charge(rq.running[cpu], now);
}

static void
finish_wait(struct proc *p, uint64 now)
{
  uint64 elapsed = now - p->state_at;
  if (p->ss.state == RUNNABLE) {
    p->ss.wait_time += elapsed;
    if (elapsed > p->ss.max_wait)
      p->ss.max_wait = elapsed;
  } else if (p->ss.state == SLEEPING) {
    p->ss.sleep_time += elapsed;
  } else if (p->ss.state == USED) {
    p->ss.setup_time += elapsed;
  }
  p->state_at = now;
}

// Rebuild the ready queues after a global priority reset. Concatenating the
// old queues Q0..Q3 preserves FIFO within every old queue. Running processes
// are not queue entries; they observe rotate=1 at their next safe timer point.
static void
reset_budgets(uint64 now, int boost)
{
  struct proc *heads[NQUEUE];
  for (int q = 0; q < NQUEUE; q++) {
    heads[q] = rq.head[q];
    rq.head[q] = rq.tail[q] = 0;
  }
  for (struct proc *p = proc; p < &proc[NPROC]; p++)
    if (p->sched_active && p->ss.state != ZOMBIE) {
      p->ss.level = 0;
      p->ss.slice_used = p->ss.allot_used = 0;
      p->rotate = 1;
      if (boost && p->ss.state != USED && p->ss.state != ZOMBIE)
        p->ss.boosts++;
    }
  for (int q = 0; q < NQUEUE; q++)
    for (struct proc *p = heads[q], *next; p; p = next) {
      next = p->rq_next;
      p->on_rq = 0;
      insert(p, 0);
    }
  if (boost) {
    rq.info.boosts++;
    emit(SE_BOOST, 0, cpuid(), 0, 0, now);
  }
}

// This checker runs under rq.lock and never acquires a process lock. State
// writers hold BOTH locks, so reading state under either lock is synchronized.
// Check ownership, acyclic links, unique queue membership, budgets, and exact
// conservation of CPU time by priority and by CPU.
static int
verify(void)
{
  int seen[NPROC] = {0};
  for (int q = 0; q < NQUEUE; q++) {
    struct proc *prev = 0;
    int n = 0;
    for (struct proc *p = rq.head[q]; p; p = p->rq_next) {
      if (++n > NPROC || p < proc || p >= &proc[NPROC])
        return -1;
      int i = p - proc;
      if (seen[i]++ || !p->on_rq || !p->sched_active ||
          p->ss.state != RUNNABLE || p->state != RUNNABLE ||
          p->ss.level != q || p->rq_prev != prev)
        return -1;
      prev = p;
    }
    if (prev != rq.tail[q])
      return -1;
  }
  for (int cpu = 0; cpu < NCPU; cpu++) {
    struct proc *p = rq.running[cpu];
    if (p && (!p->sched_active || p->ss.state != RUNNING || p->ss.cpu != cpu))
      return -1;
  }
  for (struct proc *p = proc; p < &proc[NPROC]; p++) {
    if (!p->sched_active) {
      if (p->on_rq)
        return -1;
      continue;
    }
    if (p->ss.state != p->state || p->ss.pid != p->pid ||
        p->ss.level < 0 || p->ss.level >= NQUEUE || !p->ss.affinity ||
        (!!p->on_rq != (p->ss.state == RUNNABLE)) ||
        (!!seen[p - proc] != !!p->on_rq))
      return -1;
    if (p->ss.state == RUNNING) {
      if (p->ss.cpu < 0 || p->ss.cpu >= NCPU || rq.running[p->ss.cpu] != p)
        return -1;
    } else if (p->ss.cpu != -1) {
      return -1;
    }
    int q = p->ss.level;
    if (p->ss.state != ZOMBIE &&
        (p->ss.slice_used >= (uint64)rq.info.config.quantum[q] * TIMER_INTERVAL ||
         p->ss.allot_used >= (uint64)rq.info.config.allotment[q] * TIMER_INTERVAL))
      return -1;
    uint64 queues = 0, cores = 0;
    for (int i = 0; i < NQUEUE; i++)
      queues += p->ss.queue_time[i];
    for (int i = 0; i < NCPU; i++)
      cores += p->ss.cpu_time_by_cpu[i];
    if (queues != p->ss.cpu_time || cores != p->ss.cpu_time)
      return -1;
    if (p->ss.state == ZOMBIE && p->ss.cpu_time + p->ss.setup_time +
        p->ss.wait_time + p->ss.sleep_time != p->ss.exited - p->ss.born)
      return -1;
  }
  return 0;
}

static void
check(void)
{
  if (SCHED_DEBUG && verify() < 0)
    panic("scheduler invariant");
}

void
schedinit(void)
{
  uint quantum[NQUEUE] = { SCHED_QUANTA };
  uint allot[NQUEUE] = { SCHED_ALLOTMENTS };
  initlock(&rq.lock, "scheduler");
  rq.info.version = rq.info.config.version = SCHED_VERSION;
  rq.info.policy = SCHED_MLFQ;
  rq.info.cpus = SCHED_CPUS;
  rq.info.config.fork_inherit = 1;
  rq.info.config.boost_ticks = BOOST_INTERVAL;
  for (int q = 0; q < NQUEUE; q++) {
    rq.info.config.quantum[q] = quantum[q];
    rq.info.config.allotment[q] = allot[q];
  }
  if (!config_valid(&rq.info.config))
    panic("invalid scheduler config");
  rq.info.boot_time = r_time();
  rq.next_boost = rq.info.boot_time + (uint64)BOOST_INTERVAL * TIMER_INTERVAL;
  rq.trace_first = rq.trace_next = 1;
  rq.info.generation = 1;
}

void
sched_online(void)
{
  int cpu = cpuid();
  if (cpu >= SCHED_CPUS || cpu >= NCPU)
    panic("CPU count differs from build");
  acquire(&rq.lock);
  rq.info.online_mask |= 1U << cpu;
  rq.info.online_at[cpu] = r_time();
  release(&rq.lock);
}

void
sched_alloc(struct proc *p)
{
  acquire(&rq.lock);
  uint64 now = r_time();
  memset(&p->ss, 0, sizeof(p->ss));
  p->ss.pid = p->pid;
  p->ss.state = USED;
  p->ss.cpu = p->ss.last_cpu = -1;
  p->ss.affinity = (1U << SCHED_CPUS) - 1;
  p->ss.born = now;
  p->ss.first_run = ~0UL;
  p->state_at = now;
  p->run_at = p->wake_at = 0;
  p->on_rq = p->rotate = 0;
  p->rq_next = p->rq_prev = 0;
  p->sched_active = 1;
  emit(SE_ALLOC, p, cpuid(), 0, p->ss.affinity, now);
  check();
  release(&rq.lock);
}

void
sched_free(struct proc *p)
{
  acquire(&rq.lock);
  if (p->on_rq || p->ss.state == RUNNING)
    panic("free scheduled process");
  emit(SE_FREE, p, cpuid(), 0, 0, r_time());
  p->state = UNUSED;
  p->sched_active = 0;
  memset(&p->ss, 0, sizeof(p->ss));
  check();
  release(&rq.lock);
}

void
sched_fork(struct proc *child, struct proc *parent)
{
  acquire(&rq.lock);
  uint64 now = r_time();
  charge(parent, now);
  child->ss.affinity = parent->ss.affinity;
  if (SCHED_MLFQ && rq.info.config.fork_inherit) {
    child->ss.level = parent->ss.level;
    child->ss.slice_used = parent->ss.slice_used;
    child->ss.allot_used = parent->ss.allot_used;
  }
  emit(SE_FORK, child, cpuid(), parent->pid, child->ss.affinity, now);
  check();
  release(&rq.lock);
}

void
sched_ready(struct proc *p, int reason)
{
  acquire(&rq.lock);
  uint64 now = r_time();
  if (!holding(&p->lock) || (p->state != USED && p->state != SLEEPING))
    panic("ready transition");
  finish_wait(p, now);
  if (p->state == SLEEPING)
    p->wake_at = now;
  p->state = p->ss.state = RUNNABLE;
  insert(p, 0);
  emit(SE_READY, p, cpuid(), reason, 0, now);
  check();
  release(&rq.lock);
}

struct proc *
sched_peek(void)
{
  acquire(&rq.lock);
  struct proc *p = pick(cpuid());
  release(&rq.lock);
  return p;
}

int
sched_claim(struct proc *p, int strict)
{
  acquire(&rq.lock);
  int cpu = cpuid();
  if (p->state != RUNNABLE || !p->on_rq ||
      !(p->ss.affinity & (1U << cpu)) || (strict && pick(cpu) != p)) {
    release(&rq.lock);
    return 0;
  }
  uint64 now = r_time();
  if (rq.running[cpu])
    panic("CPU already owns a process");
  removeq(p);
  finish_wait(p, now);
  if (p->wake_at) {
    uint64 delay = now - p->wake_at;
    p->ss.wakeups++;
    p->ss.wake_delay += delay;
    if (delay > p->ss.max_wake_delay)
      p->ss.max_wake_delay = delay;
    p->wake_at = 0;
  }
  if (p->ss.first_run == ~0UL)
    p->ss.first_run = now;
  if (p->ss.last_cpu >= 0 && p->ss.last_cpu != cpu)
    p->ss.migrations++;
  p->ss.cpu = p->ss.last_cpu = cpu;
  p->state = p->ss.state = RUNNING;
  p->run_at = p->state_at = now;
  p->rotate = 0;
  p->ss.dispatches++;
  rq.info.dispatches[cpu]++;
  if (rq.last_pid[cpu] != p->pid)
    rq.info.switches[cpu]++;
  rq.last_pid[cpu] = p->pid;
  rq.running[cpu] = p;
  emit(SE_DISPATCH, p, cpu, 0, 0, now);
  check();
  release(&rq.lock);
  return 1;
}

// p->lock remains held by the caller across sched()/swtch(). Only rq.lock
// is released here. Returning a partial, priority-preempted quantum to the
// front preserves its remaining turn; voluntary yield always joins the tail.
static void
stop_locked(struct proc *p, int state, int reason, uint64 now)
{
  if (p->ss.state != RUNNING || p->ss.cpu != cpuid())
    panic("stop owner");
  charge(p, now);
  int front = reason == SR_TIMER && !p->rotate;
  rq.running[p->ss.cpu] = 0;
  p->ss.cpu = -1;
  p->state = p->ss.state = state;
  p->state_at = now;
  emit(SE_STOP, p, cpuid(), state, reason, now);
  if (state == ZOMBIE)
    p->ss.exited = now;
  if (state == RUNNABLE) {
    insert(p, front);
    emit(SE_READY, p, cpuid(), reason, front, now);
  }
}

void
sched_stop(struct proc *p, int state, int reason)
{
  acquire(&rq.lock);
  stop_locked(p, state, reason, r_time());
  check();
  release(&rq.lock);
}

int
schedpreempt(void)
{
  struct proc *p = myproc();
  acquire(&p->lock);
  acquire(&rq.lock);
  charge(p, r_time());
  struct proc *best = pick(cpuid());
  int preempt = !SCHED_MLFQ || p->rotate ||
                (best && best->ss.level < p->ss.level);
  release(&rq.lock);
  release(&p->lock);
  return preempt;
}

// Only CPU0 advances the global boost deadline. It does not acquire any
// process lock; remote running intervals are closed at one shared timestamp
// before their priorities change. Each CPU handles its own timer preemption.
void
schedclock(void)
{
  acquire(&rq.lock);
  uint64 now = r_time();
  uint64 interval = (uint64)rq.info.config.boost_ticks * TIMER_INTERVAL;
  if (SCHED_MLFQ && interval && now >= rq.next_boost) {
    charge_all(now);
    reset_budgets(now, 1);
    rq.next_boost += ((now - rq.next_boost) / interval + 1) * interval;
    check();
  }
  release(&rq.lock);
}

int
sched_affinity(struct proc *p, uint mask)
{
  acquire(&rq.lock);
  if (!mask || (mask & ~rq.info.online_mask)) {
    release(&rq.lock);
    return -1;
  }
  uint64 now = r_time();
  charge(p, now);
  p->ss.affinity = mask;
  emit(SE_AFFINITY, p, cpuid(), 0, mask, now);
  int moved = !(mask & (1U << cpuid()));
  if (moved)
    stop_locked(p, RUNNABLE, SR_AFFINITY, now);
  check();
  release(&rq.lock);
  return moved;
}

void
sched_snapshot(struct proc *p, struct schedstat *s)
{
  acquire(&rq.lock);
  uint64 now = r_time();
  charge(p, now);
  *s = p->ss;
  s->now = now;
  uint64 elapsed = now - p->state_at;
  if (s->state == RUNNABLE) {
    s->wait_time += elapsed;
    if (elapsed > s->max_wait)
      s->max_wait = elapsed;
  } else if (s->state == SLEEPING) {
    s->sleep_time += elapsed;
  } else if (s->state == USED) {
    s->setup_time += elapsed;
  }
  release(&rq.lock);
}

int
getpinfo(int slot, uint64 addr)
{
  if (slot < -1 || slot >= NPROC || !addr)
    return -1;
  struct proc *p = slot == -1 ? myproc() : &proc[slot];
  struct schedstat s;
  acquire(&p->lock);
  if (p->state == UNUSED) {
    release(&p->lock);
    return -1;
  }
  sched_snapshot(p, &s);
  release(&p->lock);
  struct proc *caller = myproc();
  return copyout(caller->pagetable, caller->sz, addr, (char *)&s, sizeof(s));
}

int
sched_info(uint64 addr)
{
  struct sched_info info;
  acquire(&rq.lock);
  uint64 now = r_time();
  charge_all(now);
  info = rq.info;
  info.now = now;
  info.trace_first = rq.trace_first;
  info.trace_next = rq.trace_next;
  for (int q = 0; q < NQUEUE; q++) {
    info.ready[q] = 0;
    for (struct proc *p = rq.head[q]; p; p = p->rq_next)
      info.ready[q]++;
  }
  release(&rq.lock);
  struct proc *p = myproc();
  return addr ? copyout(p->pagetable, p->sz, addr, (char *)&info, sizeof(info)) : -1;
}

int
sched_control(int command, uint64 addr)
{
  struct proc *caller = myproc();
  struct sched_config config;
  if (!addr || (command != SCHED_GET && command != SCHED_SET && command != SCHED_TRACE))
    return -1;
  if (command != SCHED_GET &&
      (copyin(caller->pagetable, caller->sz, (char *)&config, addr, sizeof(config)) < 0 ||
       !config_valid(&config)))
    return -1;
  acquire(&rq.lock);
  if (command == SCHED_GET) {
    config = rq.info.config;
    release(&rq.lock);
    return copyout(caller->pagetable, caller->sz, addr, (char *)&config, sizeof(config));
  }
  uint64 now = r_time();
  charge_all(now);
  int starting = config.trace && !rq.info.config.trace;
  int reset = command == SCHED_SET;
  if (reset) {
    rq.info.config = config;
    reset_budgets(now, 0);
    rq.next_boost = now + (uint64)config.boost_ticks * TIMER_INTERVAL;
  } else {
    // Observing a running system must not reset its policy state.
    rq.info.config.trace = config.trace;
  }
  rq.info.generation++;
  if (starting) {
    rq.trace_first = rq.trace_next;
    emit(SE_BEGIN, 0, cpuid(), 0, 0, now);
  }
  emit(SE_CONFIG, 0, cpuid(), rq.info.config.fork_inherit | (reset ? 2 : 0),
       rq.info.config.boost_ticks, now);
  if (starting) {
    for (struct proc *p = proc; p < &proc[NPROC]; p++)
      if (p->sched_active)
        emit(SE_SEED, p, p->ss.cpu, p->ss.state | (p->rotate ? 256 : 0),
             p->ss.affinity, now);
    for (int q = 0; q < NQUEUE; q++)
      for (struct proc *p = rq.head[q]; p; p = p->rq_next)
        emit(SE_READY, p, cpuid(), SR_CREATE, 0, now);
  }
  check();
  release(&rq.lock);
  return 0;
}

int
sched_check(void)
{
  acquire(&rq.lock);
  int result = verify();
  release(&rq.lock);
  return result;
}

int
sched_trace(uint64 cursoraddr, uint64 output, int capacity)
{
  struct proc *p = myproc();
  struct sched_cursor cursor;
  if (!cursoraddr || !output || capacity < 1 || capacity > TRACE_BATCH ||
      copyin(p->pagetable, p->sz, (char *)&cursor, cursoraddr, sizeof(cursor)) < 0)
    return -1;
  // A bounded syscall buffer is allocated outside rq.lock, so the context
  // switch path never allocates and the small kernel stack cannot overflow.
  char *buffer = kalloc();
  if (!buffer)
    return -1;
  acquire(&rq.lock);
  if (!cursor.next)
    cursor.next = rq.trace_first;
  if (cursor.next < rq.trace_first) {
    cursor.lost += rq.trace_first - cursor.next;
    cursor.next = rq.trace_first;
  }
  if (cursor.next > rq.trace_next) {
    release(&rq.lock);
    kfree(buffer);
    return -1;
  }
  int n = 0;
  while (n < capacity && cursor.next < rq.trace_next) {
    memmove(buffer + n * sizeof(struct sched_event),
            &rq.events[cursor.next % TRACE_CAPACITY], sizeof(struct sched_event));
    cursor.next++;
    n++;
  }
  release(&rq.lock);
  int result = n;
  if (copyout(p->pagetable, p->sz, output, buffer, n * sizeof(struct sched_event)) < 0 ||
      copyout(p->pagetable, p->sz, cursoraddr, (char *)&cursor, sizeof(cursor)) < 0)
    result = -1;
  kfree(buffer);
  return result;
}
