// Mixed workload to stress math, string, object, regex, JSON, and large-object manipulation.
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

  function defaultBigObject(seedUsers = 50) {
    return {
      meta: { created: Date.now(), tags: ["alpha", "beta", "gamma"] },
      users: Array.from({ length: seedUsers }, (_, i) => ({
        id: `user-${i}`,
        score: i * 3,
        prefs: { flags: { a: i % 2 === 0, b: i % 3 === 0 }, threshold: i % 7 },
      })),
    };
  }

  function heavyWork(iterations = 500_000, context = {}) {
    const { log, logs } = makeLogger("heavy");
    const iter = iterations ?? context.iterations ?? 500_000;
    let acc = 0;
    const start = Date.now();
    const re =
      context.regex instanceof RegExp
        ? context.regex
        : /([A-Z]{2,4})(\d{3})/;
    const template =
      typeof context.template === "string"
        ? context.template
        : "ABCD123 XYZ789 HELLO456 WORLD000";
    const arr = [];
    const sourceBigObject =
      context.bigObject && context.bigObject.users
        ? context.bigObject
        : defaultBigObject(context.userCount || 50);
    let bigObject = {
      ...sourceBigObject,
      users: sourceBigObject.users.map((u) => ({
        ...u,
        prefs: { ...(u.prefs || {}), flags: { ...(u.prefs?.flags || {}) } },
      })),
    };

    log(`heavy workload started (iterations=${iter.toLocaleString()})`);
    for (let i = 0; i < iter; i += 1) {
      // Math and bitwise
      acc += Math.imul(i, i % 97) ^ (i << 1);
      acc += Math.sin(i % 360) + Math.log1p((i % 1000) + 1);

      // String slicing/concat
      const str = `${template}-${i}`;
      acc += str.charCodeAt(i % str.length);

      // Regex extraction
      const match = re.exec(str);
      if (match) acc += parseInt(match[2], 10);

      // Object/array churn
      const obj = { val: i, nested: { v: i % 5 } };
      arr.push(obj);
      if (arr.length > 128) arr.shift();
      acc += arr[(i + arr.length - 1) % arr.length].nested.v;

      // JSON encode/decode overhead
      const json = JSON.stringify(arr[arr.length - 1]);
      acc += JSON.parse(json).val % 10;

      // Large object manipulation: clone, update, aggregate
      const cloned = { ...bigObject, users: bigObject.users.map((u) => ({ ...u })) };
      const idx = i % cloned.users.length;
      cloned.users[idx].score += i % 5;
      cloned.meta.lastUpdated = i;
      bigObject = cloned;

      const totalScore = bigObject.users.reduce((sum, u) => sum + u.score, 0);
      acc += totalScore % 1000;
    }

    const durationMs = Date.now() - start;
    log(`heavy workload finished in ${durationMs.toFixed(2)}ms (acc=${acc})`);
    log(`heavy workload summary (logs=${logs.length}, duration_ms=${durationMs.toFixed(2)})`);
    return { result: acc, logs };
  }

  benchRunners.heavy = (context = {}, iterations) =>
    heavyWork(iterations, context);

  const shouldAutoRun = !(
    typeof module !== "undefined" &&
    module.parent
  );
  if (shouldAutoRun) {
    heavyWork();
  }
})();
