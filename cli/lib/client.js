"use strict";
const axios = require("axios");

class ZilfClient {
  constructor(port = 8000) {
    this.base = `http://127.0.0.1:${port}`;
    this.http = axios.create({
      baseURL: this.base,
      timeout: 120_000,
    });
  }

  // -- Deteksi apakah input butuh agent mode --------------------------
  isAgentTask(input) {
    const agentKeywords = [
      "buat", "buatkan", "tambah", "tambahkan", "fix", "perbaiki",
      "hapus", "ubah", "refactor", "debug", "test", "install",
      "create", "make", "add", "remove", "update", "write", "run",
      "generate", "implement", "deploy", "build", "edit", "delete",
      "rename", "move", "copy", "migrate", "optimize", "convert"
    ];
    const lower = input.toLowerCase();
    return agentKeywords.some((kw) => lower.includes(kw));
  }

  // -- Smart router: /chat untuk sapaan, /run/stream untuk coding -----
  async smartTask(task, onChunk) {
    if (this.isAgentTask(task)) {
      return this.streamTask(task, onChunk);
    } else {
      try {
        const resp = await this.http.post("/chat", { message: task });
        const text = resp.data.response || resp.data.answer || JSON.stringify(resp.data);
        onChunk(text);
      } catch (err) {
        // fallback ke agent jika /chat gagal
        return this.streamTask(task, onChunk);
      }
    }
  }

  // -- Agent mode via SSE stream ---------------------------------------
  async streamTask(task, onChunk) {
    try {
      const resp = await this.http.post(
        "/run/stream",
        { task },
        { responseType: "stream" }
      );
      return new Promise((resolve, reject) => {
        let buffer = "";
        resp.data.on("data", (chunk) => {
          buffer += chunk.toString();
          const lines = buffer.split("\n");
          buffer = lines.pop();
          for (const line of lines) {
            if (line.startsWith("data: ")) {
              try {
                const payload = JSON.parse(line.slice(6));
                const text = payload.text || payload.content ||
                             payload.final_answer || payload.answer || "";
                if (text) onChunk(text);
              } catch (_) {
                const raw = line.slice(6);
                if (raw) onChunk(raw);
              }
            }
          }
        });
        resp.data.on("end", resolve);
        resp.data.on("error", reject);
      });
    } catch (err) {
      // fallback ke /run blocking
      const result = await this.runTask(task);
      const text = result.final_answer || result.result ||
                   result.output || JSON.stringify(result);
      onChunk(text);
    }
  }

  // -- Blocking run (fallback) -----------------------------------------
  async runTask(task) {
    const resp = await this.http.post("/run", { task });
    return resp.data;
  }

  async health() {
    const resp = await this.http.get("/health");
    return resp.data;
  }

  async tools() {
    try {
      const resp = await this.http.get("/tools");
      return resp.data;
    } catch (_) {
      return [];
    }
  }
}

module.exports = ZilfClient;