// Parse/stringify a medium payload repeatedly to measure JSON overhead.
(() => {
  const benchRunners = (globalThis.benchRunners = globalThis.benchRunners || {});

  function makeLogger(label) {
    const logs = [];
    const log = (...args) => {
      const msg = `[${label}] ${args.map((a) => String(a)).join(" ")}`;
      logs.push(msg);
      if (typeof console !== "undefined" && console.log) console.log(msg);
    };
    return { log, logs };
  }

  function defaultPayload() {
    return JSON.stringify({
      meta: { source: "bench", timestamp: Date.now(), flags: [true, false, true] },
      users: Array.from({ length: 300 }, (_, i) => ({
        id: `user-${i}`,
        score: (i * 7) % 1000,
        tags: ["a", "b", "c"].slice(0, (i % 3) + 1),
        prefs: { dark: i % 2 === 0, email: i % 5 === 0 },
      })),
    });
  }

  function jsonParseBench(iterations = 4_000, context = {}) {
    const { log, logs } = makeLogger("json_parse");
    try {
      const iter = iterations ?? context.iterations ?? 4_000;
      let sum = 0;
      const payload = typeof context.payload === "string" ? context.payload : defaultPayload();
      let current = payload;
      const start = Date.now();
      log(`json parse started (iterations=${iter.toLocaleString()})`);
      for (let i = 0; i < iter; i += 1) {
        const obj = JSON.parse(current);
        const idx = i % obj.users.length;
        obj.users[idx].score += i % 11;
        sum += obj.users[idx].score;
        current = JSON.stringify(obj);
      }
      const durationMs = Date.now() - start;
      // Parse final payload to get verification data
      const finalObj = JSON.parse(current);
      const finalUserCount = finalObj.users.length;
      const finalSum = finalObj.users.reduce((s, u) => s + u.score, 0);
      log(`json parse finished in ${durationMs.toFixed(2)}ms (sum=${sum})`);
      log(`json parse summary (logs=${logs.length}, duration_ms=${durationMs.toFixed(2)})`);
      return {
        result: sum,
        logs,
        verification: {
          iterations: iter,
          computedSum: sum,
          finalUserCount: finalUserCount,
          finalSumFromObject: finalSum,
          completed: true,
        },
      };
    } catch (error) {
      const errorMsg = `json parse error: ${error}`;
      const stackMsg = error.stack ? ` | stack: ${error.stack}` : "";
      log(`error: ${errorMsg}${stackMsg}`);
      if (typeof console !== "undefined" && console.error) {
        console.error(`[json_parse:error] ${errorMsg}${stackMsg}`);
      }
      throw error;
    }
  }

  benchRunners.jsonParse = (context = {}, iterations) => jsonParseBench(iterations, context);

  const shouldAutoRun = !(
    typeof module !== "undefined" &&
    module.parent
  );
  if (shouldAutoRun) {
    try {
      const result = jsonParseBench();
      // Return the result so Python can verify it
      if (typeof module !== "undefined" && module.exports) {
        module.exports = result;
      }
      return result;
    } catch (error) {
      const errorMsg = `[json_parse:error] Execution failed: ${error}`;
      const stackMsg = error.stack ? ` | stack: ${error.stack}` : "";
      if (typeof console !== "undefined") {
        console.error(errorMsg + stackMsg);
      }
      throw error;
    }
  }
})();
