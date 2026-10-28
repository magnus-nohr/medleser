// Small fetch wrapper for the local Medleser API.
// All state-changing requests carry the X-Medleser header (required by the server).

export class ApiError extends Error {
  constructor(status, error) {
    super(error?.message || "Noe gikk galt.");
    this.status = status;
    this.code = error?.code || "error";
    this.details = error?.details || null;
    this.payload = error;
  }
}

async function request(method, path, body, { raw = false } = {}) {
  const init = { method, headers: { "X-Medleser": "1" }, credentials: "same-origin" };
  if (body instanceof FormData) {
    init.body = body;
  } else if (body !== undefined) {
    init.headers["Content-Type"] = "application/json";
    init.body = JSON.stringify(body);
  }
  let response;
  try {
    response = await fetch(path, init);
  } catch (err) {
    throw new ApiError(0, { code: "offline", message: "Fikk ikke kontakt med Medleser. Start programmet på nytt.", details: String(err) });
  }
  if (raw) return response;
  let data = null;
  const text = await response.text();
  if (text) {
    try { data = JSON.parse(text); } catch { data = null; }
  }
  if (!response.ok) {
    const err = new ApiError(response.status, data?.error || { message: "Noe gikk galt.", details: `HTTP ${response.status}` });
    err.extra = data;
    throw err;
  }
  return data;
}

export const api = {
  get: (p) => request("GET", p),
  post: (p, b = {}) => request("POST", p, b),
  put: (p, b = {}) => request("PUT", p, b),
  patch: (p, b = {}) => request("PATCH", p, b),
  del: (p) => request("DELETE", p),
  upload: (p, formData) => request("POST", p, formData),
};

// Poll a background job until it finishes. onUpdate(job) is called on every change.
export async function waitForJob(jobId, onUpdate, { interval = 600, signal } = {}) {
  let last = "";
  for (;;) {
    if (signal?.aborted) return null;
    const job = await api.get(`/api/jobs/${jobId}`);
    const key = `${job.state}|${job.progress}|${job.message}`;
    if (key !== last) {
      last = key;
      onUpdate?.(job);
    }
    if (!["queued", "running"].includes(job.state)) return job;
    await new Promise((r) => setTimeout(r, interval));
  }
}
