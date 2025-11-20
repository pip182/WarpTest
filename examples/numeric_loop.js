// Tight numeric loop to stress arithmetic and bitwise performance.
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

  function numericLoop(iterations = 20_000_000, context = {}) {
    const { log, logs } = makeLogger("numeric_loop");
    try {
      const iter = iterations ?? context.iterations ?? 20_000_000;
      const mod = context.modulus ?? 1_000_000_007;
      const mask = context.mask ?? 97;
      let acc = 0;
      let actualIterations = 0;
      const start = Date.now();
      log(`numeric loop started (iterations=${iter.toLocaleString()})`);
      for (let i = 0; i < iter; i += 1) {
        acc = (acc + ((i << 1) ^ (i % mask))) % mod;
        actualIterations += 1;
      }
      const durationMs = Date.now() - start;
      log(`numeric loop finished in ${durationMs.toFixed(2)}ms (acc=${acc})`);
      log(`numeric loop summary (logs=${logs.length}, duration_ms=${durationMs.toFixed(2)})`);
      return {
        result: acc,
        logs,
        verification: {
          iterations: actualIterations,
          expectedIterations: iter,
          modulus: mod,
          mask: mask,
          finalAccumulator: acc,
          completed: actualIterations === iter,
        },
      };
    } catch (error) {
      const errorMsg = `numeric loop error: ${error}`;
      const stackMsg = error.stack ? ` | stack: ${error.stack}` : "";
      log(`error: ${errorMsg}${stackMsg}`);
      if (typeof console !== "undefined" && console.error) {
        console.error(`[numeric_loop:error] ${errorMsg}${stackMsg}`);
      }
      throw error;
    }
  }

  benchRunners.numericLoop = (context = {}, iterations) => numericLoop(iterations, context);

  const shouldAutoRun = !(
    typeof module !== "undefined" &&
    module.parent
  );
  if (shouldAutoRun) {
    try {
      const result = numericLoop();
      // Return the result so Python can verify it
      if (typeof module !== "undefined" && module.exports) {
        module.exports = result;
      }
      return result;
    } catch (error) {
      const errorMsg = `[numeric_loop:error] Execution failed: ${error}`;
      const stackMsg = error.stack ? ` | stack: ${error.stack}` : "";
      if (typeof console !== "undefined") {
        console.error(errorMsg + stackMsg);
      }
      throw error;
    }
  }
})();
