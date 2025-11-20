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
    const iter = iterations ?? context.iterations ?? 20_000_000;
    const mod = context.modulus ?? 1_000_000_007;
    const mask = context.mask ?? 97;
    let acc = 0;
    const start = Date.now();
    log(`numeric loop started (iterations=${iter.toLocaleString()})`);
    for (let i = 0; i < iter; i += 1) {
      acc = (acc + ((i << 1) ^ (i % mask))) % mod;
    }
    const durationMs = Date.now() - start;
    log(`numeric loop finished in ${durationMs.toFixed(2)}ms (acc=${acc})`);
    log(`numeric loop summary (logs=${logs.length}, duration_ms=${durationMs.toFixed(2)})`);
    return { result: acc, logs };
  }

  benchRunners.numericLoop = (context = {}, iterations) => numericLoop(iterations, context);

  const shouldAutoRun = !(
    typeof module !== "undefined" &&
    module.parent
  );
  if (shouldAutoRun) {
    numericLoop();
  }
})();
