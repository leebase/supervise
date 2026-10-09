# Third-party components

No third-party source code is included in this repository, and no third-party software is redistributed with it.
Every Python module imports only the Python standard library and this package. The OMP manager extension
(`supervise/omp/supervise.ts`) imports only Node.js built-in modules and a type-only declaration from oh-my-pi, which
is erased at load time.

The software runs alongside programs that you install yourself, under each program's own license and terms:

| Program | Role | Distributed here? |
|---|---|---|
| Python 3.12 or newer | Runs the supervisor, controller, verifier and tests | No |
| bubblewrap (`bwrap`) | Isolates the reviewer and the trusted verifier | No |
| Codex CLI | Worker harness for OpenAI models | No |
| Claude Code | Worker harness for Anthropic models | No |
| oh-my-pi (`omp`) | Runs the manager that drives `supervise run` | No |
| git | Not used by supervise; the quickstart uses it to clone this repository | No |
