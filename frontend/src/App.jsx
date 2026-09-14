import { useCallback, useEffect, useState } from "react";

import { api } from "./api";
import { SITUATIONS } from "./situations";
import {
  ApprovalQueue,
  Checks,
  PositionBar,
  Trace,
  Verdict,
  Verification,
} from "./components/Panels";

export default function App() {
  const [health, setHealth] = useState(null);
  const [selected, setSelected] = useState(SITUATIONS[0]);
  const [run, setRun] = useState(null);
  const [tasks, setTasks] = useState([]);
  const [orders, setOrders] = useState([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const [freshSeed, setFreshSeed] = useState(true);

  const refresh = useCallback(async () => {
    const [approvals, purchaseOrders] = await Promise.all([
      api.approvals(),
      api.purchaseOrders(),
    ]);
    setTasks(approvals);
    setOrders(purchaseOrders);
  }, []);

  useEffect(() => {
    api.health().then(setHealth).catch((e) => setError(e.message));
    refresh().catch((e) => setError(e.message));
  }, [refresh]);

  async function runSituation(situation) {
    setBusy(true);
    setError(null);
    try {
      if (freshSeed) await api.seed();
      const result = await api.run({ ...situation.request, scenario: "S1" });
      setRun(result);
      await refresh();
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }

  async function resolveTask(taskId, payload) {
    setBusy(true);
    setError(null);
    try {
      const result = await api.resolve(taskId, payload);
      await refresh();
      if (run && result.verification) {
        setRun({ ...run, verification: result.verification, status: "COMPLETED" });
      }
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="shell">
      <header className="masthead">
        <h1>Turbo purchasing console</h1>
        <p>Every recommendation is checked before it is bought.</p>
        <span className={`mode-chip ${health?.llm_configured ? "live" : ""}`}>
          {health
            ? health.llm_configured
              ? `Model reasoning · ${health.mode}`
              : "Policy engine only · no API key set"
            : "connecting"}
        </span>
      </header>

      <section style={{ marginTop: 22 }}>
        <h2 style={{ marginBottom: 10 }}>Pick a situation</h2>
        <div className="situations">
          {SITUATIONS.map((situation) => (
            <button
              key={situation.id}
              className="situation"
              aria-pressed={selected.id === situation.id}
              onClick={() => setSelected(situation)}
            >
              <span className="who">{situation.who}</span>
              <span className="what">{situation.what}</span>
              <span className="what" style={{ color: "var(--ink-soft)" }}>
                {situation.tests}
              </span>
            </button>
          ))}
        </div>

        <div className="run-bar">
          <button className="primary" disabled={busy} onClick={() => runSituation(selected)}>
            {busy ? "Working…" : "Run the agent"}
          </button>
          <label className="toggle">
            <input
              type="checkbox"
              checked={freshSeed}
              onChange={(event) => setFreshSeed(event.target.checked)}
            />
            Reset the data first
          </label>
          <button
            className="ghost"
            disabled={busy}
            onClick={async () => {
              await api.seed();
              setRun(null);
              await refresh();
            }}
          >
            Reset now
          </button>
          {orders.length > 0 && (
            <span style={{ color: "var(--ink-faint)", fontSize: 13 }}>
              {orders.length} purchase order{orders.length === 1 ? "" : "s"} on file
            </span>
          )}
        </div>

        {error && <div className="banner">{error}</div>}
      </section>

      <div className="columns">
        <div className="stack">
          {run ? (
            <>
              <Verdict run={run} />

              <section className="panel">
                <div className="panel-head">
                  <h2>Inventory position</h2>
                  <span className="spacer" />
                  <span style={{ color: "var(--ink-faint)", fontSize: 12.5 }}>
                    {run.request.sku} at {run.request.node_id}
                  </span>
                </div>
                <div className="panel-body">
                  <PositionBar computed={run.computed} evidence={run.evidence} />
                </div>
              </section>

              <section className="panel">
                <div className="panel-head">
                  <h2>What the agent did</h2>
                  <span className="spacer" />
                  <span style={{ color: "var(--ink-faint)", fontSize: 12.5 }}>
                    tap a step for the figures behind it
                  </span>
                </div>
                <div className="panel-body">
                  <Trace trace={run.trace} />
                </div>
              </section>
            </>
          ) : (
            <section className="panel">
              <p className="empty">
                Choose a situation above and run it. The agent investigates, decides,
                gets checked, and then either acts or hands the decision to you.
              </p>
            </section>
          )}
        </div>

        <div className="stack">
          <section className="panel">
            <div className="panel-head">
              <h2>Waiting on you</h2>
              <span className="spacer" />
              <span className="tag">{tasks.length}</span>
            </div>
            <div className="panel-body" style={{ padding: tasks.length ? 16 : 0 }}>
              <ApprovalQueue tasks={tasks} onResolve={resolveTask} busy={busy} />
            </div>
          </section>

          {run && (
            <>
              <section className="panel">
                <div className="panel-head">
                  <h2>Checks</h2>
                  <span className="spacer" />
                  <span
                    className={`tag ${
                      run.verdict?.deterministic?.all_passed ? "pass" : "fail"
                    }`}
                  >
                    {run.verdict?.deterministic?.all_passed ? "all clear" : "needs review"}
                  </span>
                </div>
                <div className="panel-body">
                  <Checks verdict={run.verdict} />
                </div>
              </section>

              <section className="panel">
                <div className="panel-head">
                  <h2>What actually happened</h2>
                </div>
                <div className="panel-body">
                  <Verification verification={run.verification} />
                </div>
              </section>
            </>
          )}

          {orders.length > 0 && (
            <section className="panel">
              <div className="panel-head">
                <h2>Purchase orders</h2>
              </div>
              <div className="panel-body scroll">
                <table>
                  <thead>
                    <tr>
                      <th>Order</th>
                      <th>SKU</th>
                      <th>Asked</th>
                      <th>Got</th>
                      <th>Status</th>
                    </tr>
                  </thead>
                  <tbody>
                    {orders.slice(0, 12).map((order) => (
                      <tr key={order.po_id}>
                        <td>{order.po_id}</td>
                        <td>{order.sku}</td>
                        <td>{order.quantity.toLocaleString()}</td>
                        <td>{order.confirmed_quantity.toLocaleString()}</td>
                        <td>
                          <span
                            className={`tag ${
                              order.status === "CONFIRMED"
                                ? "pass"
                                : order.status === "PENDING_APPROVAL"
                                  ? "hold"
                                  : order.status === "PARTIALLY_CONFIRMED"
                                    ? "hold"
                                    : "fail"
                            }`}
                          >
                            {order.status.replace(/_/g, " ").toLowerCase()}
                          </span>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </section>
          )}
        </div>
      </div>
    </div>
  );
}
