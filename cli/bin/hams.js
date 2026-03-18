#!/usr/bin/env node
"use strict";

/**
 *  ██╗  ██╗ █████╗ ███╗   ███╗███████╗   █████╗ ██╗
 *  ██║  ██║██╔══██╗████╗ ████║██╔════╝  ██╔══██╗██║
 *  ███████║███████║██╔████╔██║███████╗  ███████║██║
 *  ██╔══██║██╔══██║██║╚██╔╝██║╚════██║  ██╔══██║██║
 *  ██║  ██║██║  ██║██║ ╚═╝ ██║███████║  ██║  ██║██║
 *  ╚═╝  ╚═╝╚═╝  ╚═╝╚═╝     ╚═╝╚══════╝  ╚═╝  ╚═╝╚═╝
 *
 *  hams.ai CLI — @hams-ai/cli
 *
 *  Commands:
 *    hams              → interactive chat (default)
 *    hams run "task"   → run single task and exit
 *    hams tools        → list available tools
 *    hams status       → check backend status
 */

const path = require("path");
const fs = require("fs");
const { program } = require("commander");
const chalk = require("chalk");
const ora = require("ora");
const inquirer = require("inquirer");

const { findPython, findRequirements, isPythonAgentInstalled, installPythonDeps } = require("../lib/installer");
const { startServer, stopServer } = require("../lib/server");
const HamsClient = require("../lib/client");

// ─── Banner ──────────────────────────────────────────────────────────────────
const BANNER = `
${chalk.bold.white("hams.ai")} ${chalk.dim("— AI Coding Agent")}
`;

// ─── Resolve project root ─────────────────────────────────────────────────────
function resolveProjectRoot() {
  // 1. Environment variable
  if (process.env.HAMS_PATH && fs.existsSync(process.env.HAMS_PATH)) {
    return process.env.HAMS_PATH;
  }
  // 2. npm global install: .../node_modules/@hams-ai/cli/bin/hams.js → up 5 levels
  const fromBin = path.resolve(__dirname, "..", "..", "..", "..", "..");
  if (fs.existsSync(path.join(fromBin, "agent", "api.py"))) return fromBin;

  // 3. Local dev: cli/ is sibling of agent/
  const sibling = path.resolve(__dirname, "..", "..");
  if (fs.existsSync(path.join(sibling, "agent", "api.py"))) return sibling;

  // 4. Current working directory
  if (fs.existsSync(path.join(process.cwd(), "agent", "api.py"))) return process.cwd();

  return null;
}

// ─── Setup: find Python, install deps, start server ──────────────────────────
async function setup(verbose = false, port = 8000) {
  console.log(BANNER);
  const spinner = ora({ text: "Starting hams.ai...", color: "white" }).start();

  // 1. Find Python
  const pythonCmd = findPython();
  if (!pythonCmd) {
    spinner.fail(
      chalk.red("Python not found.\n") +
      chalk.dim("  Install Python 3.8+ from https://python.org and add it to PATH.")
    );
    process.exit(1);
  }

  // 2. Find project root
  const projectRoot = resolveProjectRoot();
  if (!projectRoot) {
    spinner.fail(
      chalk.red("Cannot find hams.ai project folder.\n\n") +
      chalk.white("  Set the HAMS_PATH environment variable:\n") +
      chalk.dim("  PowerShell: ") + chalk.cyan(`$env:HAMS_PATH = "C:\\path\\to\\hams.ai"\n`) +
      chalk.dim("  Linux/Mac:  ") + chalk.cyan(`export HAMS_PATH="/path/to/hams.ai"`)
    );
    process.exit(1);
  }

  spinner.text = "Checking Python dependencies...";

  // 3. Auto-install deps if needed
  if (!isPythonAgentInstalled(pythonCmd)) {
    const reqFile = findRequirements(projectRoot);
    if (reqFile) {
      spinner.text = "Installing Python dependencies (first time setup)...";
      try {
        installPythonDeps(pythonCmd, reqFile);
      } catch (err) {
        spinner.warn(chalk.yellow(`Could not auto-install deps: ${err.message}`));
        spinner.warn(chalk.dim(`Run manually: pip install -r ${reqFile}`));
      }
    }
  }

  // 4. Start Python backend
  spinner.text = "Starting backend...";
  try {
    const { process: serverProc } = await startServer({ pythonCmd, projectRoot, port, verbose });
    spinner.succeed(chalk.green(`hams.ai ready`) + chalk.dim(` — port ${port}`));
    return { serverProc, port };
  } catch (err) {
    spinner.fail(chalk.red(`Backend failed to start: ${err.message}`));
    if (!verbose) console.log(chalk.dim("  Tip: run with --verbose to see Python output"));
    process.exit(1);
  }
}

// ─── Print result ─────────────────────────────────────────────────────────────
function printResult(text) {
  process.stdout.write("\n");
}

// ─── Interactive REPL ─────────────────────────────────────────────────────────
async function interactiveMode(client) {
  console.log(chalk.dim('  Type your task and press Enter. Type "exit" to quit.\n'));

  process.on("SIGINT", () => {
    console.log(chalk.dim("\n\n  Goodbye.\n"));
    process.exit(0);
  });

  while (true) {
    const { task } = await inquirer.prompt([
      {
        type: "input",
        name: "task",
        message: chalk.bold.white("›"),
        validate: (v) => v.trim().length > 0 || "Please enter a task.",
      },
    ]);

    const trimmed = task.trim();
    if (["exit", "quit", "q"].includes(trimmed.toLowerCase())) {
      console.log(chalk.dim("\n  Goodbye.\n"));
      process.exit(0);
    }

    const spinner = ora({ text: chalk.dim("Thinking..."), color: "white" }).start();
    try {
      let output = "";
      let started = false;
      await client.streamTask(trimmed, (chunk) => {
        if (!started) {
          spinner.stop();
          process.stdout.write("\n");
          started = true;
        }
        process.stdout.write(chunk);
        output += chunk;
      });
      if (!started) {
        spinner.stop();
        process.stdout.write("\n" + output);
      }
      process.stdout.write("\n\n");
    } catch (err) {
      spinner.fail(chalk.red(err.message));
    }
  }
}

// ─── Commands ─────────────────────────────────────────────────────────────────
program
  .name("hams")
  .description("hams.ai — AI Coding Agent CLI")
  .version("1.0.0", "-v, --version")
  .option("--verbose", "Show Python backend output")
  .option("--port <port>", "Backend port", "8000");

// Default: interactive chat
program
  .command("chat", { isDefault: true })
  .description("Start interactive chat (default)")
  .action(async () => {
    const opts = program.opts();
    const port = parseInt(opts.port);
    const { serverProc } = await setup(opts.verbose, port);
    const client = new HamsClient(port);

    process.on("exit", () => stopServer(serverProc));
    process.on("SIGINT", () => { stopServer(serverProc); process.exit(0); });

    await interactiveMode(client);
  });

// One-shot run
program
  .command("run <task>")
  .description("Run a single task and exit")
  .action(async (task) => {
    const opts = program.opts();
    const port = parseInt(opts.port);
    const { serverProc } = await setup(opts.verbose, port);
    const client = new HamsClient(port);

    process.on("exit", () => stopServer(serverProc));

    const spinner = ora({ text: chalk.dim("Running..."), color: "white" }).start();
    try {
      let started = false;
      await client.streamTask(task, (chunk) => {
        if (!started) { spinner.stop(); process.stdout.write("\n"); started = true; }
        process.stdout.write(chunk);
      });
      process.stdout.write("\n\n");
      stopServer(serverProc);
      process.exit(0);
    } catch (err) {
      spinner.fail(chalk.red(err.message));
      stopServer(serverProc);
      process.exit(1);
    }
  });

// List tools
program
  .command("tools")
  .description("List available agent tools")
  .action(async () => {
    const opts = program.opts();
    const port = parseInt(opts.port);
    const { serverProc } = await setup(opts.verbose, port);
    const client = new HamsClient(port);

    try {
      const tools = await client.tools();
      console.log(chalk.bold("\n  Tools available in hams.ai:\n"));
      if (Array.isArray(tools) && tools.length) {
        tools.forEach((t) => {
          const name = t.name || t;
          const desc = t.description || "";
          console.log(`  ${chalk.white("•")} ${chalk.bold(name)}  ${chalk.dim(desc)}`);
        });
      } else {
        console.log(chalk.dim("  (no tools info returned by API)"));
      }
      console.log();
    } catch (err) {
      console.error(chalk.red(`Could not fetch tools: ${err.message}`));
    }
    stopServer(serverProc);
    process.exit(0);
  });

// Status check (no server spawn — just check existing)
program
  .command("status")
  .description("Check if backend is already running")
  .action(async () => {
    const opts = program.opts();
    const port = parseInt(opts.port);
    const client = new HamsClient(port);
    try {
      const h = await client.health();
      console.log(chalk.green(`\n  hams.ai backend is running`) + chalk.dim(` on port ${port}`));
      console.log(chalk.dim(`  ${JSON.stringify(h)}\n`));
    } catch (_) {
      console.log(chalk.dim(`\n  No backend found on port ${port}.\n`));
    }
    process.exit(0);
  });

program.parse(process.argv);
