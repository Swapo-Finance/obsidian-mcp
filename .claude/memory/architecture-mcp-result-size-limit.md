---
name: architecture-mcp-result-size-limit
description: Claude Code grava em arquivo todo resultado de tool MCP acima de 50.000 caracteres; MAX_MCP_OUTPUT_TOKENS não move esse corte
metadata:
  type: architecture
---

O Claude Code salva em `~/.claude/projects/.../tool-results/` todo resultado de texto de tool MCP acima de 50.000 caracteres, seja qual for a contagem de tokens. A mensagem de erro fala em "tokens", mas o corte é por caracteres (issue anthropics/claude-code#97622). `MAX_MCP_OUTPUT_TOKENS` não altera esse corte. Quando o resultado tem `structuredContent`, o modelo recebe o JSON compacto dele, e o `TextContent` é descartado. Um servidor pode declarar um teto maior por tool com `_meta["anthropic/maxResultSizeChars"]` (até 500.000; no FastMCP 2.12, `@mcp.tool(meta=...)`), mas o resultado inteiro entra no contexto do agente.

**Why:** `read_note_tool` devolvia a nota inteira, e uma nota de 63.544 caracteres estourou. A primeira hipótese (subir `MAX_MCP_OUTPUT_TOKENS`) não resolve. Achar o corte real exigiu pesquisa na documentação e no binário do Claude Code.

**How to apply:** toda tool nova ou alterada que possa devolver payload grande precisa de paginação ou de corte com dica de continuação. Mantenha o bloco de texto abaixo de ~45.000 caracteres, com margem para escapes JSON. Siga o padrão de `read_note_tool`: `offset`, `max_chars`, `next_offset`, `truncated` e o knob `OBSIDIAN_READ_MAX_CHARS`.
