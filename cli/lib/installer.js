"use strict";

const { spawnSync } = require("child_process");
const path = require("path");
const fs = require("fs");
const which = require("which");

function findPython() {
  for (const cmd of ["python3", "python"]) {
    try {
      which.sync(cmd);
      const result = spawnSync(cmd, ["--version"], { encoding: "utf8" });
      if (result.status === 0) return cmd;
    } catch (_) {}
  }
  return null;
}

function findRequirements(projectRoot) {
  const candidates = [
    path.join(projectRoot, "requirements.txt"),
    path.join(projectRoot, "..", "requirements.txt"),
  ];
  for (const c of candidates) {
    if (fs.existsSync(c)) return c;
  }
  return null;
}

function isPythonAgentInstalled(pythonCmd) {
  const result = spawnSync(
    pythonCmd,
    ["-c", "import agent; print('ok')"],
    { encoding: "utf8" }
  );
  return result.status === 0;
}

function installPythonDeps(pythonCmd, requirementsPath) {
  const result = spawnSync(
    pythonCmd,
    ["-m", "pip", "install", "-r", requirementsPath, "--quiet"],
    { encoding: "utf8", stdio: "pipe" }
  );
  if (result.status !== 0) {
    throw new Error(`pip install failed:\n${result.stderr || result.stdout}`);
  }
  return true;
}

module.exports = { findPython, findRequirements, isPythonAgentInstalled, installPythonDeps };
