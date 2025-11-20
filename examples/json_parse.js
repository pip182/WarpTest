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
    log(`json parse finished in ${durationMs.toFixed(2)}ms (sum=${sum})`);
    log(`json parse summary (logs=${logs.length}, duration_ms=${durationMs.toFixed(2)})`);
    return { result: sum, logs };
  }

  benchRunners.jsonParse = (context = {}, iterations) => jsonParseBench(iterations, context);

  const shouldAutoRun = !(
    typeof module !== "undefined" &&
    module.parent
  );
  if (shouldAutoRun) {
    jsonParseBench();
  }
})();
