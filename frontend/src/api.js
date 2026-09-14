const BASE = import.meta.env.VITE_API_BASE || "http://localhost:8000";

async function request(path, options = {}) {
  const response = await fetch(`${BASE}${path}`, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!response.ok) {
    let detail = `${response.status} ${response.statusText}`;
    try {
      const body = await response.json();
      detail = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail);
    } catch {
      /* response had no JSON body */
    }
    throw new Error(detail);
  }
  return response.json();
}

export const api = {
  health: () => request("/api/health"),
  seed: () => request("/api/seed", { method: "POST" }),
  catalogue: () => request("/api/catalogue"),
  runs: () => request("/api/runs"),
  run: (payload) =>
    request("/api/runs", { method: "POST", body: JSON.stringify(payload) }),
  approvals: () => request("/api/approvals"),
  resolve: (taskId, payload) =>
    request(`/api/approvals/${taskId}`, {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  purchaseOrders: () => request("/api/purchase-orders"),
  policy: () => request("/api/policy"),
};
