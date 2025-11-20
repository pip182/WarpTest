// Simple HTTP server to execute JavaScript snippets. Do not expose to untrusted networks.
const http = require("http");
const { Script, createContext } = require("vm");

const verbose =
  process.env.VERBOSE === "1" ||
  process.env.DEBUG === "1" ||
  process.env.LOG_LEVEL === "debug";

const log = (...args) => {
  if (verbose) {
    // eslint-disable-next-line no-console
    console.log(...args);
  }
};

const logError = (...args) => {
  // eslint-disable-next-line no-console
  console.error(...args);
};

const port = Number(process.env.PORT || 3210);

function makeConsoleCollector() {
  const logs = [];
  const record = (level) => (...args) => {
    const msg = args.map((a) => String(a)).join(" ");
    logs.push({ level, message: msg });
    if (verbose) {
      const sink = level === "error" || level === "warn" ? console.error : console.log; // eslint-disable-line no-console
      sink(`[${level}]`, msg);
    }
  };
  return {
    console: {
      log: record("log"),
      info: record("info"),
      warn: record("warn"),
      error: record("error"),
    },
    logs,
  };
}

const server = http.createServer((req, res) => {
  const start = Date.now();
  if (req.method === "GET" && req.url === "/health") {
    res.writeHead(200, { "Content-Type": "application/json" });
    res.end(JSON.stringify({ ok: true }));
    log("health check ok", { durationMs: Date.now() - start });
    return;
  }

  if (req.method !== "POST" || req.url !== "/run") {
    res.writeHead(404, { "Content-Type": "application/json" });
    res.end(JSON.stringify({ ok: false, error: "Not found" }));
    return;
  }

  let body = "";
  req.on("data", (chunk) => {
    body += chunk.toString();
  });

  req.on("end", () => {
    const commonMeta = {
      durationMs: Date.now() - start,
      length: body.length,
    };
    const { console: sandboxConsole, logs } = makeConsoleCollector();
    try {
      let payload;
      try {
        payload = JSON.parse(body || "{}");
      } catch (err) {
        throw new Error(`Invalid JSON: ${err.message}`);
      }
      if (typeof payload.code !== "string") {
        res.writeHead(400, { "Content-Type": "application/json" });
        res.end(JSON.stringify({ ok: false, error: "Missing code" }));
        log("request rejected: missing code", commonMeta);
        return;
      }

      const script = new Script(payload.code, { filename: "user-code.js" });
      const context = createContext({ console: sandboxConsole });
      const result = script.runInContext(context);

      res.writeHead(200, { "Content-Type": "application/json" });
      res.end(JSON.stringify({ ok: true, result: result ?? null, logs }));
      log("request ok", { ...commonMeta, logs: logs.length });
    } catch (error) {
      res.writeHead(500, { "Content-Type": "application/json" });
      res.end(JSON.stringify({ ok: false, error: String(error), logs }));
      logError("request failed", { ...commonMeta, error: String(error), logs });
    }
  });
});

const stop = () =>
  server.close(() => {
    process.exit(0);
  });

process.on("SIGINT", stop);
process.on("SIGTERM", stop);

server.listen(port, "127.0.0.1", () => {
  console.log(`Node benchmark server listening on http://127.0.0.1:${port}`);
});
