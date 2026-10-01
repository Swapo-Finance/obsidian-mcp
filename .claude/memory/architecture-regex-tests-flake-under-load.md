---
name: regex-tests-flake-under-load
description: Testes do pool de regex e do modo índice da busca regex falham por timeout quando a máquina está muito carregada — rodar isolados antes de suspeitar de regressão
metadata:
  type: architecture
---

`tests/test_regex_search_security.py::TestRegexTimeoutProtection::test_pool_recycles_after_consecutive_timeouts` e testes de modo de resultado da busca regex, como `tests/test_search_index_mode_all_tools.py::TestSearchByRegexIndexMode::test_auto_mode_switches_to_index_above_threshold`, falham com load average muito alto (visto entre 30 e 64, com vários agentes rodando em paralelo). O worker de regex recém-criado não consegue subir e casar dentro do timeout por arquivo (2 s no teste de reciclagem, `REGEX_MATCH_TIMEOUT_SECONDS` nos demais), e a busca devolve menos resultados ou nenhum. Isolados e com carga normal, passam; na `main` falham do mesmo jeito.

**Why:** a falha na suíte completa parece regressão no que acabou de ser alterado; uma sessão gastou uma rodada de revisão provando que não era.

**How to apply:** checar `uptime` primeiro. Com carga alta, rodar só os testes de regex que falharam (e a suíte completa depois) antes de mexer no código. Não aumentar os timeouts para "consertar": os 2 s do teste de reciclagem distinguem de propósito um pool reciclado (~0,2 s) de um travado (~5,8 s). Relacionadas: [[architecture-regex-pool-orphan-workers]], [[architecture-regex-gil-process-isolation]].
