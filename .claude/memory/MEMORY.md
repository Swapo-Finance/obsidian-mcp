# Memory index

- [Hooks need node on PATH](hooks-need-node-on-path.md) — why a hook can silently do nothing without any bug in its script
- [Regex needs process isolation, not threads](architecture-regex-gil-process-isolation.md) — why a ThreadPoolExecutor timeout cannot stop a runaway regex from freezing the event loop
- [Workers do pool de regex ficam órfãos](architecture-regex-pool-orphan-workers.md) — por que sobrevivem ao servidor e como watchdog, backstop itimer e recuperação do pool resolvem em Linux, macOS e Windows
- [Testes de regex falham sob carga alta](architecture-regex-tests-flake-under-load.md) — timeouts espúrios no pool de regex com load average alto; rodar isolados antes de suspeitar de regressão
