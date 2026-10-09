import { useState } from "react";

const n = (value) =>
  value === null || value === undefined || Number.isNaN(value)
    ? "—"
    : Math.round(value).toLocaleString();

const money = (value) =>
  value === null || value === undefined
    ? "—"
    : `$${Number(value).toLocaleString(undefined, { maximumFractionDigits: 0 })}`;

const STATUS_TAG = {
  AUTO_EXECUTED: "pass",
  COMPLETED: "pass",
  AWAITING_APPROVAL: "hold",
  ESCALATED: "fail",
  REJECTED_BY_JUDGE: "fail",
  FAILED: "fail",
};

const STATUS_LABEL = {
  AUTO_EXECUTED: "Executed without a human",
  COMPLETED: "Closed",
  AWAITING_APPROVAL: "Waiting on a buyer",
  ESCALATED: "Escalated to a buyer",
  REJECTED_BY_JUDGE: "Blocked before writing",
  FAILED: "Failed",
};

/* -------------------------------------------------------------- verdict */

export function Verdict({ run }) {
  const decision = run.decision || {};
  const verdict = run.verdict || {};
  const recommended = run.request?.recommended_quantity;
  const action = (decision.action || "").toLowerCase();
  const changed = decision.quantity !== recommended;

  const headline =
    decision.action === "REJECT"
      ? "Buy nothing"
      : decision.action === "INVESTIGATE"
        ? "Send to a buyer"
        : `${decision.action === "ACCEPT" ? "Buy" : "Buy instead"} ${n(decision.quantity)}`;

  return (
    <section className={`panel verdict ${action}`}>
      <div className="verdict-line">
        <span className="verdict-action">{headline}</span>
        {changed && (
          <span className="verdict-was">
            planner asked for <s>{n(recommended)}</s>
          </span>
        )}
      </div>

      <p className="verdict-reasoning">{decision.reasoning}</p>

      {decision.key_factors?.length > 0 && (
        <ul className="factors">
          {decision.key_factors.map((factor, index) => (
            <li key={index}>{factor}</li>
          ))}
        </ul>
      )}

      {decision.open_questions?.length > 0 && (
        <div className="banner">
          {decision.open_questions.map((question, index) => (
            <div key={index}>{question}</div>
          ))}
        </div>
      )}

      <div className="meta-row">
        <span>
          <b className={`tag ${STATUS_TAG[run.status] || ""}`}>
            {STATUS_LABEL[run.status] || run.status}
          </b>
        </span>
        <span>
          Confidence <b>{(verdict.effective_confidence ?? 0).toFixed(2)}</b>
        </span>
        {decision.binding_constraint && (
          <span>
            Limited by <b>{decision.binding_constraint.replace(/_/g, " ")}</b>
          </span>
        )}
        <span>
          Supplier <b>{decision.supplier_id || "—"}</b>
        </span>
        <span>
          Passes <b>{run.attempts}</b>
        </span>
        <span>
          Decided by <b>{decision.source === "llm" ? "model" : "policy engine"}</b>
        </span>
      </div>
    </section>
  );
}

/* ------------------------------------------------------------- position */

export function PositionBar({ computed, evidence }) {
  if (!computed) return null;

  const onHand = (evidence?.inventory?.on_hand ?? 0) - (evidence?.inventory?.reserved ?? 0);
  const incoming = evidence?.open_pos?.total_incoming ?? 0;
  const target = computed.target_position ?? 0;
  const position = computed.current_position ?? 0;
  const gap = Math.max(target - position, 0);
  const scale = Math.max(target, position) * 1.08 || 1;
  const pct = (value) => `${Math.min((value / scale) * 100, 100)}%`;

  return (
    <div className="position">
      <div className="position-track">
        <div className="seg on-hand" style={{ left: 0, width: pct(onHand) }} />
        <div
          className="seg incoming"
          style={{ left: pct(onHand), width: pct(incoming) }}
        />
        <div className="seg gap" style={{ left: pct(position), width: pct(gap) }} />
        <div className="target-mark" style={{ left: pct(target) }} />
      </div>

      <div className="position-legend">
        <span>
          <i className="swatch" style={{ background: "var(--seg-on-hand)" }} />
          On hand, unreserved <b>{n(onHand)}</b>
        </span>
        <span>
          <i className="swatch" style={{ background: "var(--seg-inbound)" }} />
          Already inbound <b>{n(incoming)}</b>
        </span>
        <span>
          <i className="swatch" style={{ background: "var(--signal-wash)" }} />
          Gap to target <b>{n(gap)}</b>
        </span>
        <span>
          Target over {computed.cover_horizon_days} days <b>{n(target)}</b>
        </span>
        <span>
          Planning at <b>{computed.demand_rate_used}/day</b>
        </span>
      </div>
    </div>
  );
}

/* ---------------------------------------------------------------- trace */

function StepDetail({ data }) {
  const entries = Object.entries(data || {}).filter(
    ([, value]) => value !== null && value !== undefined && value !== "" &&
      !(Array.isArray(value) && value.length === 0),
  );
  if (entries.length === 0) return null;

  return (
    <div className="step-detail">
      <dl className="kv">
        {entries.map(([key, value]) => (
          <div key={key} style={{ display: "contents" }}>
            <dt>{key.replace(/_/g, " ")}</dt>
            <dd>
              {typeof value === "object" ? JSON.stringify(value) : String(value)}
            </dd>
          </div>
        ))}
      </dl>
    </div>
  );
}

export function Trace({ trace }) {
  const [open, setOpen] = useState(() => new Set());

  const toggle = (step) => {
    setOpen((current) => {
      const next = new Set(current);
      next.has(step) ? next.delete(step) : next.add(step);
      return next;
    });
  };

  return (
    <ol className="spine">
      {(trace || []).map((entry) => (
        <li key={entry.step}>
          <div className="rail">
            <span className={`dot ${entry.node}`} />
          </div>
          <div className="step">
            <button
              className="step-head"
              onClick={() => toggle(entry.step)}
              aria-expanded={open.has(entry.step)}
            >
              <span className="step-node">{entry.node.replace(/_/g, " ")}</span>
              <span className="step-summary">{entry.summary}</span>
            </button>
            {open.has(entry.step) && <StepDetail data={entry.data} />}
          </div>
        </li>
      ))}
    </ol>
  );
}

/* --------------------------------------------------------------- checks */

export function Checks({ verdict }) {
  const deterministic = verdict?.deterministic?.checks || [];
  const judgement = verdict?.judgement || {};

  return (
    <>
      <ul className="checks">
        {deterministic.map((check) => {
          const state = check.passed
            ? ""
            : check.severity === "warning"
              ? "warned"
              : "failed";
          return (
            <li className={`check ${state}`} key={check.id}>
              <span className="mark">{check.passed ? "✓" : check.severity === "warning" ? "!" : "✕"}</span>
              <span>
                <span className="id">{check.id.replace(/_/g, " ")}</span>
                <br />
                <span className="detail">{check.detail}</span>
              </span>
            </li>
          );
        })}
      </ul>

      {judgement.available && judgement.checks?.length > 0 && (
        <>
          <h3 style={{ margin: "16px 0 6px" }}>Reasoning review</h3>
          <ul className="checks">
            {judgement.checks.map((check) => (
              <li className={`check ${check.passed ? "" : "failed"}`} key={check.id}>
                <span className="mark">{check.passed ? "✓" : "✕"}</span>
                <span>
                  <span className="id">{check.id.replace(/_/g, " ")}</span>
                  <br />
                  <span className="detail">{check.detail}</span>
                </span>
              </li>
            ))}
          </ul>
        </>
      )}

      {!judgement.available && (
        <p className="detail" style={{ marginTop: 14, color: "var(--ink-faint)" }}>
          {judgement.summary || "Reasoning review runs when an API key is configured."}
        </p>
      )}
    </>
  );
}

/* ---------------------------------------------------------- verification */

export function Verification({ verification }) {
  if (!verification?.verified) {
    return (
      <p className="empty" style={{ padding: 0 }}>
        {verification?.reason || "Nothing was written, so there is nothing to check."}
      </p>
    );
  }

  const rows = [
    ["Order", verification.po_id],
    ["Intended", n(verification.intended_quantity)],
    ["Supplier confirmed", n(verification.confirmed_quantity)],
    ["Shortfall", n(verification.shortfall)],
    ["Need still open", n(verification.residual_need)],
    ["Matches intent", verification.matched_intent ? "yes" : "no"],
  ];

  return (
    <>
      <dl className="kv">
        {rows.map(([label, value]) => (
          <div key={label} style={{ display: "contents" }}>
            <dt>{label}</dt>
            <dd>{value ?? "—"}</dd>
          </div>
        ))}
      </dl>
      {verification.supplier_message && (
        <p style={{ marginBottom: 0, color: "var(--ink-faint)", fontSize: 12.5 }}>
          Supplier said: {verification.supplier_message}
        </p>
      )}
      {verification.note && (
        <p style={{ marginBottom: 0, color: "var(--ink-faint)", fontSize: 12.5 }}>
          {verification.note}
        </p>
      )}
    </>
  );
}

/* ---------------------------------------------------------------- queue */

export function ApprovalQueue({ tasks, onResolve, busy }) {
  const [edits, setEdits] = useState({});

  if (!tasks?.length) {
    return <p className="empty">Nothing waiting. Runs that clear every check execute on their own.</p>;
  }

  return (
    <div>
      {tasks.map((task) => {
        const action = task.proposed_action || {};
        const proposed = action.quantity ?? action.suggested_quantity ?? 0;
        const value = edits[task.id] ?? proposed;
        const hasOrder = Boolean(action.po_id);

        return (
          <div className="task" key={task.id}>
            <div>
              <b>{action.sku}</b> at {action.node_id} · {n(proposed)} units
              {action.order_value ? ` · ${money(action.order_value)}` : ""}
            </div>
            <div className="why">{task.reason}</div>
            {action.reasoning && (
              <div style={{ color: "var(--ink-soft)", fontSize: 12.5 }}>{action.reasoning}</div>
            )}
            <div className="task-actions">
              {hasOrder && (
                <input
                  type="number"
                  value={value}
                  aria-label="Quantity to approve"
                  onChange={(event) =>
                    setEdits({ ...edits, [task.id]: Number(event.target.value) })
                  }
                />
              )}
              <button
                className="approve"
                disabled={busy}
                onClick={() =>
                  onResolve(task.id, {
                    approved: true,
                    quantity: hasOrder ? Number(value) : null,
                  })
                }
              >
                {hasOrder ? "Approve and send" : "Acknowledge"}
              </button>
              <button
                className="reject"
                disabled={busy}
                onClick={() => onResolve(task.id, { approved: false })}
              >
                Reject
              </button>
            </div>
          </div>
        );
      })}
    </div>
  );
}
