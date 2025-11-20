// Stress nested object access with a large context exposed at global scope,
// then accessed via `with` to test scope-chain performance.
(() => {
  if (typeof console !== "undefined" && console.log) {
    console.log("[context_with:init]", "preparing context");
  }
  function makeLogger(label) {
    const logs = [];
    const log = (...args) => {
      const msg = `[${label}] ${args.map((a) => String(a)).join(" ")}`;
      logs.push(msg);
      if (typeof console !== "undefined" && console.log) console.log(msg);
    };
    return { log, logs };
  }

  const benchRunners = (globalThis.benchRunners = globalThis.benchRunners || {});

  function buildContext(overrides = {}) {
    const base = {
      meta: { env: "bench", version: 1 },
      config: {
        features: {
          a: true,
          b: false,
          c: true,
          deep: { level1: { level2: { level3: { flag: true, count: 0 } } } },
        },
        limits: { maxUsers: 5000, maxItems: 20000 },
      },
      users: Array.from({ length: 150 }, (_, i) => ({
        id: `user-${i}`,
        profile: {
          name: `User ${i}`,
          settings: { theme: i % 2 ? "dark" : "light", notifications: i % 3 === 0 },
        },
        stats: {
          score: (i * 17) % 1000,
          visits: i * 3,
          purchases: i % 7,
          history: Array.from({ length: 20 }, (__, j) => ({ t: j, v: (i + j) % 13 })),
        },
      })),
      inventory: Array.from({ length: 500 }, (_, i) => ({
        id: `item-${i}`,
        price: (i * 1.37) % 250,
        tags: ["a", "b", "c", "d"].slice(0, (i % 4) + 1),
        dims: { w: (i % 10) + 1, h: (i % 7) + 1, d: (i % 5) + 1 },
      })),
      // Large nested "user-supplied" document to push scope and property lookups.
      largeDoc: {
        sections: Array.from({ length: 800 }, (_, i) => ({
          key: `section-${i}`,
          summary: { total: i * 13, label: `L${i}` },
          data: Array.from({ length: 30 }, (__, j) => ({
            idx: j,
            checksum: (i * j) % 97,
            path: `/${i}/${j}`,
            values: Array.from({ length: 16 }, (___, k) => (i + j + k) % 31),
          })),
        })),
      },
    };
    return {
      ...base,
      ...overrides,
      config: { ...base.config, ...(overrides.config || {}) },
      meta: { ...base.meta, ...(overrides.meta || {}) },
    };
  }

  // Expose on global for engines that treat top-level vars as globals.
  globalThis.benchContext = buildContext();
  if (typeof console !== "undefined" && console.log) {
    console.log(
      "[context_with:init]",
      "users=",
      benchContext.users.length,
      "inventory=",
      benchContext.inventory.length,
    );
  }

  function runWith(iterations = 4_000) {
    const { log, logs } = makeLogger("context_with:runWith");
    try {
      let acc = 0;
      const start = Date.now();
      log(`with-scope started (iterations=${iterations.toLocaleString()})`);
      with (benchContext) {
        for (let i = 0; i < iterations; i += 1) {
          const u = users[i % users.length];
          const item = inventory[i % inventory.length];
          const history = u.stats.history[i % u.stats.history.length];
          const featureOn = config.features.deep.level1.level2.level3.flag;
          acc += u.stats.score + u.stats.visits + history.v;
          acc += featureOn ? item.price : 0;
          config.features.deep.level1.level2.level3.count += 1;
          if (i % 50 === 0) {
            inventory[i % inventory.length].price += i % 3;
          }
        }
      }
      const durationMs = Date.now() - start;
      log(`with-scope finished in ${durationMs.toFixed(2)}ms (acc=${acc})`);
      return { result: acc, logs };
    } catch (error) {
      const errorMsg = `runWith error: ${error}`;
      const stackMsg = error.stack ? ` | stack: ${error.stack}` : "";
      log(`error: ${errorMsg}${stackMsg}`);
      if (typeof console !== "undefined" && console.error) {
        console.error(`[context_with:runWith:error] ${errorMsg}${stackMsg}`);
      }
      throw error;
    }
  }

  // Heavier pass that combines deep lookups, array churn, and string work.
  function runHeavyWith(iterations = 15_000) {
    const { log, logs } = makeLogger("context_with:runHeavyWith");
    try {
      let acc = 0;
      const temp = [];
      const start = Date.now();
      log(`heavy with-scope started (iterations=${iterations.toLocaleString()})`);
      with (benchContext) {
        for (let i = 0; i < iterations; i += 1) {
          const sec = largeDoc.sections[i % largeDoc.sections.length];
          const row = sec.data[(i * 3) % sec.data.length];
          const value = row.values[(i + row.values.length - 1) % row.values.length];
          const user = users[(i * 5) % users.length];
          const inv = inventory[(i * 7) % inventory.length];
          const deepFlag = config.features.deep.level1.level2.level3.flag;

          acc += value + row.checksum + (deepFlag ? 3 : 1);
          acc += user.stats.score + inv.dims.w + inv.dims.h + inv.dims.d;

          // Create and reuse some small objects to simulate user data churn.
          const payload = {
            id: `${sec.key}-${row.idx}-${i}`,
            price: inv.price + (i % 5),
            note: `${user.profile.name}-${row.path}-${i % 10}`,
          };
          temp.push(payload);
          if (temp.length > 128) temp.shift();
          acc += temp[(i + temp.length - 1) % temp.length].price % 17;

          // Mutate nested counters to keep write pressure in the scope chain.
          config.features.deep.level1.level2.level3.count += 2;
          if (i % 40 === 0) {
            inventory[(i * 11) % inventory.length].price = Math.max(0, inv.price - 1);
          }
        }
      }
      const durationMs = Date.now() - start;
      log(
        `heavy with-scope finished in ${durationMs.toFixed(2)}ms (acc=${acc}, logs=${logs.length})`
      );
      return { result: acc, logs };
    } catch (error) {
      const errorMsg = `runHeavyWith error: ${error}`;
      const stackMsg = error.stack ? ` | stack: ${error.stack}` : "";
      log(`error: ${errorMsg}${stackMsg}`);
      if (typeof console !== "undefined" && console.error) {
        console.error(`[context_with:runHeavyWith:error] ${errorMsg}${stackMsg}`);
      }
      throw error;
    }
  }

  function loadPeerRunners() {
    if (benchRunners.heavy && benchRunners.jsonParse && benchRunners.numericLoop) {
      return;
    }
    if (typeof require !== "function") return;
    try {
      require("./heavy.js");
      require("./json_parse.js");
      require("./numeric_loop.js");
    } catch (error) {
      // Best effort; individual functions will be checked before use.
      // eslint-disable-next-line no-console
      if (typeof console !== "undefined") console.warn("Peer runner import skipped:", error);
    }
  }

  function runContextWith(options = {}) {
    try {
      const {
        context = null,
        iterationsWith = 4_000,
        iterationsHeavy = 15_000,
        invokePeerRunners = true,
        peerIterations = {},
      } = options;

      const aggregateLogs = [];
      const mergeLogs = (logs) => {
        if (Array.isArray(logs)) aggregateLogs.push(...logs);
      };

      if (context && typeof context === "object") {
        globalThis.benchContext = context;
      }

      const res1 = runWith(iterationsWith);
      mergeLogs(res1?.logs);
      const res2 = runHeavyWith(iterationsHeavy);
      mergeLogs(res2?.logs);

      if (invokePeerRunners) {
        loadPeerRunners();
        const peers = ["heavy", "jsonParse", "numericLoop"];
        for (const name of peers) {
          const runner = benchRunners[name];
          if (typeof runner === "function") {
            try {
              const peerResult = runner(benchContext, peerIterations[name]);
              mergeLogs(peerResult?.logs);
            } catch (peerError) {
              const errorMsg = `Peer runner ${name} failed: ${peerError}`;
              const stackMsg = peerError.stack ? ` | stack: ${peerError.stack}` : "";
              aggregateLogs.push(`error: ${errorMsg}${stackMsg}`);
              if (typeof console !== "undefined" && console.error) {
                console.error(`[context_with:error] ${errorMsg}${stackMsg}`);
              }
            }
          }
        }
      }
      const deepCount = benchContext.config.features.deep.level1.level2.level3.count;
      mergeLogs([
        `context_with_result=${deepCount}`,
      ]);
      if (typeof console !== "undefined" && console.log) {
        console.log(
          "Context summary:",
          `with_runs=${iterationsWith.toLocaleString()}`,
          `heavy_runs=${iterationsHeavy.toLocaleString()}`,
          `peer_logs=${aggregateLogs.length}`,
          `deep_count=${deepCount}`,
        );
      }
      return {
        result: deepCount,
        logs: aggregateLogs,
        verification: {
          iterationsWith: iterationsWith,
          iterationsHeavy: iterationsHeavy,
          deepCount: deepCount,
          userCount: benchContext.users.length,
          inventoryCount: benchContext.inventory.length,
          sectionsCount: benchContext.largeDoc.sections.length,
          peerRunnersInvoked: invokePeerRunners,
          completed: true,
        },
      };
    } catch (error) {
      const errorMsg = `runContextWith error: ${error}`;
      const stackMsg = error.stack ? ` | stack: ${error.stack}` : "";
      if (typeof console !== "undefined" && console.error) {
        console.error(`[context_with:error] ${errorMsg}${stackMsg}`);
      }
      throw error;
    }
  }

  benchRunners.contextWith = runContextWith;

  const shouldAutoRun = !(
    typeof module !== "undefined" &&
    module.parent
  );
  if (shouldAutoRun) {
    try {
      if (typeof console !== "undefined" && console.log) {
        console.log("Context script start (autoRun=true)");
      }
      const result = runContextWith();
      if (typeof console !== "undefined" && console.log) {
        console.log("Context script end (autoRun=true)");
      }
      // Return the result so Python can verify it
      if (typeof module !== "undefined" && module.exports) {
        module.exports = result;
      }
      return result;
    } catch (error) {
      const errorMsg = `[context_with:error] Execution failed: ${error}`;
      const stackMsg = error.stack ? ` | stack: ${error.stack}` : "";
      if (typeof console !== "undefined") {
        console.error(errorMsg + stackMsg);
      }
      throw error;
    }
  }
})();
