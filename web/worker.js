// Web worker that runs the Python physics engine in Pyodide, off the page's main thread,
// so the interface (and the probe) stays responsive while a calculation runs.
importScripts("https://cdn.jsdelivr.net/pyodide/v0.28.3/full/pyodide.js");

let bridge = null;

async function init() {
  postMessage({ type: "progress", text: "Downloading Python (WebAssembly)…", pct: 8 });
  const py = await loadPyodide();
  postMessage({ type: "progress", text: "Loading NumPy…", pct: 55 });
  await py.loadPackage("numpy");
  postMessage({ type: "progress", text: "Loading the physics engine…", pct: 85 });
  const resp = await fetch("windtunnel.zip");
  if (!resp.ok) throw new Error(`could not download windtunnel.zip (${resp.status})`);
  py.unpackArchive(await resp.arrayBuffer(), "zip", { extractDir: "/home/pyodide/app" });
  py.runPython("import sys; sys.path.insert(0, '/home/pyodide/app')");
  bridge = py.pyimport("bridge");
  postMessage({ type: "ready" });
}

const ready = init().catch((err) => postMessage({ type: "error", text: String(err) }));

onmessage = async (event) => {
  await ready;
  const { id, fn, args } = event.data;
  try {
    const out = bridge[fn](JSON.stringify(args));
    postMessage({ type: "result", id, ok: true, data: out });
  } catch (err) {
    postMessage({ type: "result", id, ok: false, error: String(err) });
  }
};
