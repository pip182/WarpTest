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
  // Ensure response is always sent, even on unexpected errors
  const sendError = (status, error) => {
    try {
      if (!res.headersSent) {
        res.writeHead(status, { "Content-Type": "application/json" });
        res.end(JSON.stringify({ ok: false, error: String(error), logs: [] }));
      }
    } catch (e) {
      logError("Failed to send error response:", e);
    }
  };

  try {
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

    req.on("error", (err) => {
      logError("Request error:", err);
      sendError(500, err);
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
      // Create context with require support for loading peer modules
      const fs = require("fs");
      const path = require("path");
      // Get the directory where node_server.js is located
      // Use require.main.filename if available, otherwise try __filename
      let serverDir;
      try {
        if (require.main && require.main.filename) {
          serverDir = path.dirname(require.main.filename);
        } else if (typeof __filename !== "undefined") {
          serverDir = path.dirname(__filename);
        } else {
          // Fallback: use process.cwd() and assume server is in root
          serverDir = process.cwd();
        }
      } catch (e) {
        // Fallback: use process.cwd() and assume server is in root
        serverDir = process.cwd();
      }
      const examplesPath = path.join(serverDir, "examples");

      // Create a safe require function for the VM context
      const safeRequire = (id) => {
        try {
          // Support relative requires (e.g., "./heavy.js") by resolving from examples directory
          if (id.startsWith("./") || id.startsWith("../")) {
            // Try to resolve relative to examples directory
            const resolvedPath = path.resolve(examplesPath, id);
            if (fs.existsSync(resolvedPath)) {
              // Clear require cache to allow re-requiring
              const realPath = fs.realpathSync(resolvedPath);
              delete require.cache[realPath];
              const result = require(realPath);
              if (verbose) {
                log(`require("${id}") resolved to ${realPath}`);
              }
              return result;
            }
            // If not found in examples, try relative to current working directory
            const cwdPath = path.resolve(process.cwd(), id);
            if (fs.existsSync(cwdPath)) {
              const realPath = fs.realpathSync(cwdPath);
              delete require.cache[realPath];
              const result = require(realPath);
              if (verbose) {
                log(`require("${id}") resolved to ${realPath}`);
              }
              return result;
            }
            // If still not found, log and throw
            const error = new Error(`Cannot find module '${id}'`);
            logError("require failed for", id, "resolved paths:", resolvedPath, cwdPath);
            throw error;
          }
          // Fall back to normal require
          return require(id);
        } catch (err) {
          // If require fails, log and rethrow
          logError("require failed for", id, err);
          throw err;
        }
      };

      const context = createContext({
        console: sandboxConsole,
        require: safeRequire,
        module: { exports: {} },
        exports: {},
      });
      let result;
      try {
        result = script.runInContext(context);
      } catch (scriptError) {
        const errorMsg = String(scriptError);
        const errorStack = scriptError.stack ? ` | stack: ${scriptError.stack}` : "";
        const fullError = errorMsg + errorStack;
        logs.push({ level: "error", message: `Script execution error: ${fullError}` });
        throw scriptError;
      }

      res.writeHead(200, { "Content-Type": "application/json" });
      res.end(JSON.stringify({ ok: true, result: result ?? null, logs }));
      log("request ok", { ...commonMeta, logs: logs.length });
    } catch (error) {
      const errorMsg = String(error);
      const errorStack = error.stack ? ` | stack: ${error.stack}` : "";
      const fullError = errorMsg + errorStack;
      // Add error to logs if not already captured
      if (logs.length === 0 || !logs.some((log) => log.message && log.message.includes(errorMsg))) {
        logs.push({ level: "error", message: `Server execution error: ${fullError}` });
      }
      sendError(500, fullError);
      logError("request failed", { ...commonMeta, error: fullError, logs: logs.length });
    }
    });
  } catch (error) {
    logError("Unexpected server error:", error);
    sendError(500, error);
  }
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

// Handle uncaught exceptions to prevent server crashes
process.on("uncaughtException", (error) => {
  logError("Uncaught exception:", error);
  // Don't exit - let the server continue running
});

process.on("unhandledRejection", (reason, promise) => {
  logError("Unhandled rejection at:", promise, "reason:", reason);
  // Don't exit - let the server continue running
});
