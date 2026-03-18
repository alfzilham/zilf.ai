"use strict";

const axios = require("axios");

class HamsClient {
  constructor(port = 8000) {
    this.base = `http://127.0.0.1:${port}`;
    this.http = axios.create({
      baseURL: this.base,
      timeout: 120_000,
    });
  }

  async runTask(task) {
    const resp = await this.http.post("/run", { task });
    return resp.data;
  }

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
                onChunk(payload.text || payload.content || "");
              } catch (_) {
                onChunk(line.slice(6));
              }
            }
          }
        });
        resp.data.on("end", resolve);
        resp.data.on("error", reject);
      });
    } catch (err) {
      const result = await this.runTask(task);
      onChunk(result.result || result.output || JSON.stringify(result));
    }
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

module.exports = HamsClient;
