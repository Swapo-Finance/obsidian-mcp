---
description: Verify code changes work by running them. Proves through execution, not just inspection.
---
<!-- Vendored from ag-kit (github.com/vudovn/ag-kit) @ 20a13da6d4414c7c6ae33db050a9c606eaef9f40 :: .agents/workflows/verify.md. MIT (c) vudovn. -->

# /verify — Prove Code Works

$ARGUMENTS

---

## 🔴 CRITICAL RULES

1. **Run the project's real checks** — `ruff check <changed files>`, `pyright <changed files>`, `OBSIDIAN_VAULT_PATH=/path/to/vault pytest`
2. **Execute, don't inspect** — Run the code, don't just read it
3. **Report evidence** — Show actual output, not assumptions
4. **Cover edge cases** — Test error paths, not just happy path

---

## Task

Prove code works by running it:

```
CONTEXT:
- What to verify: $ARGUMENTS
- If empty: verify the most recent code changes in this session

WORKFLOW:
1. IDENTIFY what changed (files, functions, behavior)
2. DETERMINE verification method — `ruff check`, `pyright`, `OBSIDIAN_VAULT_PATH=/path/to/vault pytest`
3. EXECUTE verification commands
4. REPORT evidence of success or failure
5. FLAG anything that couldn't be verified automatically

RULES:
1. "It should work" is NOT verification — run it
2. Test error paths, not just success paths
3. Report with actual command output as evidence
```

---

## Expected Output

```
## Verification Report

### Changes Verified
- [file/change 1]: ✅ Pass
- [file/change 2]: ✅ Pass

### Evidence
- Build: ✅ Compiled without errors
- Tests: ✅ [N]/[N] passing
- Runtime: ✅ [specific verification result]

### Not Verified
- [anything that needs manual testing]
```

---

## Usage Examples

```
/verify
/verify the login endpoint handles expired tokens
/verify build passes after refactoring
/verify the new component renders correctly
```
