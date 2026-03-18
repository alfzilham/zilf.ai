"use strict";

const { spawn } = require("child_process");
const path = require("path");
const http = require("http");
const fs = require("fs");

const DEFAULT_PORT = 8000;
const STARTUP_TIMEOUT_MS = 30_000;

function waitForServer(port, timeoutMs) {
  return new Promise((resolve, reject) => {
    const deadline = Date.now() + timeoutMs;

    function poll() {
      if (Date.now() > deadline) {
        return reject(new Error(`hams.ai backend did not start within ${timeoutMs / 1000}s`));
      }
      const req = http.get(`http://127.0.0.1:${port}/health`, (res) => {
        if (res.statusCode < 500) resolve();
        else setTimeout(poll, 500);
      });
      req.on("error", () => setTimeout(poll, 500));
      req.setTimeout(1000, () => { req.destroy(); setTimeout(poll, 500); });
    }

    poll();
  });
}

async function startServer({ pythonCmd, projectRoot, port = DEFAULT_PORT, verbose = false }) {
  const apiEntry = path.join(projectRoot, "agent", "api.py");
  const mainEntry = path.join(projectRoot, "agent", "main.py");
  const entry = fs.existsSync(apiEntry) ? apiEntry : mainEntry;

  const env = {
    ...process.env,
    AGENT_PORT: String(port),
    PYTHONPATH: projectRoot,
  };

  const child = spawn(pythonCmd, [entry, "--port", String(port)], {
    cwd: projectRoot,
    env,
    stdio: verbose ? "inherit" : "pipe",
  });

  if (!verbose) {
    let stderr = "";
    child.stderr && child.stderr.on("data", (d) => { stderr += d.toString(); });
    child.on("exit", (code) => {
      if (code !== 0 && code !== null) {
        stderr && process.stderr.write(`\n[hams.ai] ${stderr}\n`);
      }
    });
  }

  child.on("error", (err) => {
    throw new Error(`Failed to start hams.ai backend: ${err.message}`);
  });

  await waitForServer(port, STARTUP_TIMEOUT_MS);
  return { process: child, port };
}

function stopServer(child) {
  if (!child || child.killed) return;
  child.kill("SIGTERM");
  setTimeout(() => { if (!child.killed) child.kill("SIGKILL"); }, 5000);
}

module.exports = { startServer, stopServer };
