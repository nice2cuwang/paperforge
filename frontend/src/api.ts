const apiBase = (import.meta.env.VITE_API_BASE ?? "http://localhost:8010").replace(/\/$/, "");

async function readErrorMessage(response: Response): Promise<string> {
  const raw = await response.text();
  if (!raw) return `Request failed with ${response.status}`;

  try {
    const parsed = JSON.parse(raw) as { detail?: unknown; message?: unknown };
    if (typeof parsed.message === "string" && parsed.message.trim()) return parsed.message;
    if (typeof parsed.detail === "string" && parsed.detail.trim()) return parsed.detail;
    if (parsed.detail && typeof parsed.detail === "object") {
      const detail = parsed.detail as Record<string, unknown>;
      const code = typeof detail.code === "string" ? detail.code : "";
      const title = typeof detail.title === "string" ? detail.title : "";
      const message = typeof detail.message === "string" ? detail.message : "";
      if (message) return `${code ? `[${code}] ` : ""}${message}`;
      if (title) return `${code ? `[${code}] ` : ""}${title}`;
    }
  } catch {
    // Ignore parse error and return raw text below.
  }

  return raw;
}

export async function apiRequest<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${apiBase}${path}`, {
    headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
    ...init
  });
  if (!response.ok) {
    throw new Error(await readErrorMessage(response));
  }
  if (response.status === 204) {
    return undefined as T;
  }
  return (await response.json()) as T;
}

export async function uploadFile<T>(path: string, file: File, fields?: Record<string, string>): Promise<T> {
  const form = new FormData();
  form.append("file", file);
  Object.entries(fields ?? {}).forEach(([key, value]) => form.append(key, value));

  const response = await fetch(`${apiBase}${path}`, {
    method: "POST",
    body: form
  });
  if (!response.ok) {
    throw new Error(await readErrorMessage(response));
  }
  return (await response.json()) as T;
}

export function getApiBase(): string {
  return apiBase;
}
