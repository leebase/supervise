// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 Lee Harrington
import { execFile } from "node:child_process";
import { promises as fs } from "node:fs";
import { join } from "node:path";
import type { ExtensionAPI } from "@oh-my-pi/pi-coding-agent";

const python = process.env.SUPERVISE_PYTHON || "python3";
const completed = new Set(["ACCEPTED", "CHECKPOINT_EXHAUSTED", "RECONCILE_REQUIRED"]);
type Result = { code: number; stdout: string; stderr: string };
type Turns = { turns: number; version: number; idle: number };

function call(args: string[], timeoutMs: number, signal?: AbortSignal): Promise<Result> {
  return new Promise((resolve) => {
    execFile(python, ["-m", "supervise", ...args], {
      env: process.env, maxBuffer: 16 * 1024 * 1024, timeout: timeoutMs, signal,
      encoding: "utf8",
    }, (error, stdout, stderr) => resolve({
      code: typeof error?.code === "number" ? error.code : error ? 1 : 0,
      stdout, stderr,
    }));
  });
}

function isObject(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

export default function supervise(pi: ExtensionAPI): void {
  let active: { home: string; invocationId: string } | undefined;
  let savedTools: string[] | undefined;
  const restoreTools = () => {
    if (savedTools) pi.setActiveTools(savedTools);
    savedTools = undefined;
  };
  const send = (content: string, triggerTurn: boolean) => pi.sendMessage({
    customType: "supervise", content, display: true, attribution: "agent",
  }, triggerTurn ? { triggerTurn: true, deliverAs: "nextTurn" } : { triggerTurn: false });

  pi.registerCommand("supervise", {
    description: "Begin portable Python supervision for a plan path",
    handler: async (args: string, ctx) => {
      if (active) {
        let summary = "state unavailable";
        try {
          const state: unknown = JSON.parse(await fs.readFile(join(active.home, "state.json"), "utf8"));
          if (isObject(state)) summary = `status ${String(state.status)}, version ${String(state.version)}`;
        } catch { /* Report the active invocation even when its state cannot be read. */ }
        send(`Supervise invocation ${active.invocationId} is already active (${summary}). ` +
          "Use supervise_continue for its next action.", false);
        return;
      }
      const input = args.trim();
      const plan: unknown = input.startsWith('"') ? JSON.parse(input) : input;
      if (typeof plan !== "string") throw new Error("Plan path must be a string");
      const result = await call(["begin", plan, "--project", ctx.cwd], 60_000);
      if (result.code !== 0) throw new Error(result.stderr.trim());
      const begun: unknown = JSON.parse(result.stdout);
      if (!isObject(begun) || typeof begun.home !== "string" ||
          typeof begun.invocation_id !== "string" || typeof begun.bootstrap !== "string") {
        throw new Error("Invalid supervise begin response");
      }
      active = { home: begun.home, invocationId: begun.invocation_id };
      savedTools ??= pi.getActiveTools().slice();
      const allowed = new Set(["read", "grep", "glob", "find", "ls", "supervise_continue"]);
      const restricted = savedTools.filter((name) => allowed.has(name));
      if (!restricted.includes("supervise_continue")) restricted.push("supervise_continue");
      pi.setActiveTools(restricted);
      send(begun.bootstrap, true);
    },
  });

  pi.registerTool({
    name: "supervise_continue",
    label: "Continue supervised work",
    description: "Forward one JSON intent object to the Python supervisor",
    parameters: pi.zod.object({ intent_json: pi.zod.string() }),
    async execute(_id, params, signal, _update, _ctx) {
      if (!active) throw new Error("No active supervise invocation");
      const invocation = active;
      try {
        if (!isObject(JSON.parse(params.intent_json))) throw new Error("Expected an object");
      } catch {
        return { content: [{ type: "text", text: "REFUSED: intent_json must be a JSON object" }],
          details: { exit_code: 1 } };
      }
      const result = await call(["intent", invocation.home, params.intent_json], 3_600_000, signal);
      try {
        const outcome: unknown = JSON.parse(result.stdout);
        if (active === invocation && isObject(outcome) && typeof outcome.status === "string" &&
            completed.has(outcome.status)) {
          restoreTools();
        }
      } catch { /* Plain text output is also a valid tool result. */ }
      return { content: [{ type: "text", text: result.code === 0 ? result.stdout :
        "REFUSED: " + result.stderr.trim() }], details: { exit_code: result.code } };
    },
  });

  pi.on("agent_end", async (_event, _ctx) => {
    if (!active) return;
    const invocation = active;
    try {
      const state: unknown = JSON.parse(await fs.readFile(join(invocation.home, "state.json"), "utf8"));
      if (!isObject(state) || typeof state.status !== "string" ||
          typeof state.version !== "number" || !Number.isInteger(state.version)) return;
      if (completed.has(state.status) || state.status === "PRECHECK_FAILED") return;
      const path = join(invocation.home, "manager-turns.json");
      let previous: Turns = { turns: 0, version: -1, idle: 0 };
      try {
        const value: unknown = JSON.parse(await fs.readFile(path, "utf8"));
        if (!isObject(value) || ![value.turns, value.version, value.idle].every(Number.isInteger)) return;
        previous = value as Turns;
      } catch (error) {
        if ((error as { code?: string }).code !== "ENOENT") return;
      }
      const next: Turns = { turns: previous.turns + 1, version: state.version,
        idle: state.version === previous.version ? previous.idle + 1 : 0 };
      if (active !== invocation) return;
      await fs.writeFile(path, JSON.stringify(next), { mode: 0o600 });
      await fs.chmod(path, 0o600);
      if (active !== invocation || next.turns >= 8 || next.idle >= 2) return;
      send(`The outcome is still open (status: ${state.status}, version: ${state.version}). ` +
        "Inspect the latest result and take the next concrete action through supervise_continue; " +
        "do not implement project files yourself.", true);
    } catch { /* Read/parse errors must not cause another manager turn. */ }
  });

  pi.on("session_switch", () => {
    restoreTools();
    active = undefined;
  });
}
