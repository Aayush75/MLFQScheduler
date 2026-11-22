#include "schedutil.h"

int
main(int argc, char **argv)
{
  struct sched_config c;
  require(schedctl(SCHED_GET, &c) == 0, "read configuration");
  if (argc == 3) {
    int value = atoi(argv[2]);
    if (strcmp(argv[1], "boost") == 0)
      c.boost_ticks = value;
    else if (strcmp(argv[1], "trace") == 0)
      c.trace = value;
    else if (strcmp(argv[1], "inherit") == 0)
      c.fork_inherit = value;
    else
      require(0, "usage: schedctl [boost|trace|inherit value] or [q0 q1 q2 q3 a0 a1 a2 a3 boost]");
    require(schedctl(strcmp(argv[1], "trace") == 0 ? SCHED_TRACE : SCHED_SET, &c) == 0,
            "validated configuration update");
  } else if (argc == 10) {
    for (int q = 0; q < NQUEUE; q++) {
      c.quantum[q] = atoi(argv[1 + q]);
      c.allotment[q] = atoi(argv[5 + q]);
    }
    c.boost_ticks = atoi(argv[9]);
    require(schedctl(SCHED_SET, &c) == 0, "validated configuration update");
  } else {
    require(argc == 1, "invalid argument count");
  }
  printf("CONFIG,%d,%d,%d,%d,%d,%d,%d,%d,%d,%d,%d\n",
         c.quantum[0], c.quantum[1], c.quantum[2], c.quantum[3],
         c.allotment[0], c.allotment[1], c.allotment[2], c.allotment[3],
         c.boost_ticks, c.fork_inherit, c.trace);
  exit(0);
}
