---
name: architecture-regex-pool-orphan-workers
description: por que os workers do pool de regex ficam órfãos quando o servidor morre (SIGKILL/SIGTERM) e como watchdog no worker + backstop itimer + recuperação de BrokenProcessPool resolvem em Linux, macOS e Windows
metadata:
  type: architecture
---

Os workers `spawn` do `ProcessPoolExecutor` de regex (`obsidian_mcp/utils/persistent_index.py`) não morrem quando o servidor morre de forma abrupta (SIGKILL, ou SIGTERM com disposição default): cada worker segura as DUAS pontas dos pipes da fila, nunca vê EOF e fica bloqueado para sempre, e o `resource_tracker` vive enquanto um worker segurar o fd dele. Nenhum cleanup no processo pai cobre SIGKILL.

**Why:** medido no servidor real (macOS, Python 3.12; controle só com stdlib também em 3.14): 5 de 5 órfãos (4 workers + tracker, PPID=1, ociosos) após SIGKILL, SIGTERM e SIGINT escalado para SIGTERM. `close()`, `atexit` ou handler no pai só resolvem o caminho de EOF do stdin. Os órfãos ainda herdam e seguram o stdout do servidor (fd 0/1/2).

**How to apply:**
- A detecção da morte do pai fica no worker: `multiprocessing.parent_process().join()` numa thread daemon (initializer do pool) e depois `os._exit(0)`. Não use `os.getppid()`: no Windows ele não muda quando o pai morre.
- Worker preso dentro de `re` segura o GIL e a thread não roda. Backstop: `signal.setitimer(ITIMER_REAL, 2 x timeout)` armado DENTRO do worker (`_match_with_backstop`), nunca no servidor (SIGALRM com disposição default mata o processo que o armou). Só POSIX; no Windows não há backstop (upgrade: Job Object com KILL_ON_JOB_CLOSE). O valor do timeout vai por argumento, porque o worker spawn reimporta o módulo e uma constante alterada por monkeypatch no pai não chega lá.
- Worker morto quebra o pool inteiro (`BrokenProcessPool` nos futuros em voo e em todo submit seguinte). `gather(return_exceptions=True)` engole o erro e toda busca regex passa a voltar vazia para sempre. Por isso `_match_in_regex_pool` recicla o pool (com a guarda `pool is self._regex_process_pool`, senão um handler atrasado mata o pool novo) e tenta de novo uma única vez.
- Saída limpa: `app.main()` usa try/finally e chama `kill_regex_pool()`. Sem isso o hook de saída do `concurrent.futures` dá join e trava num worker preso.
- Premissas erradas que circulavam: o índice persistente é criado em QUALQUER busca, não só em vault grande (`OBSIDIAN_SEARCH_INDEX_THRESHOLD` só escolhe o formato da resposta); e um handler Python de SIGALRM roda, sim, dentro de `re` (o `_sre` consulta sinais). O kill pelo kernel foi escolhido porque não depende do interpretador, não porque `re` seja ininterruptível.
- Defeito separado, não corrigido: o servidor não sai sozinho no EOF do stdin (thread não-daemon do aiosqlite sem `close()`), então o cliente precisa escalar para SIGTERM.
- Testes em `tests/test_regex_pool_orphans.py` (+ `_regex_pool_orphan_helper.py`). `os.kill(pid, 0)` no Windows MATA o processo, então os testes com PID são só POSIX.

Relacionado: [architecture-regex-gil-process-isolation](architecture-regex-gil-process-isolation.md) (por que processo e não thread).
