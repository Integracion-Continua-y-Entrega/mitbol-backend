// Puede apuntar al backend local o a la API Render de pruebas con cookies CHIPS.
export class ApiError extends Error {
  constructor(public status: number, public detail: unknown, public retryAfter?: number) {
    super(`La API respondió ${status}`);
  }
}

export function adminApi(baseUrl = "http://localhost:8000") {
  let csrf: string | undefined; // Memoria; la cookie HttpOnly sólo la maneja el navegador.

  async function request(path: string, method = "GET", body?: object | FormData) {
    const headers: Record<string, string> = {};
    const isForm = body instanceof FormData;
    if (body && !isForm) headers["Content-Type"] = "application/json";
    if (method !== "GET" && path !== "/api/auth/login") {
      if (!csrf) throw new Error("Recuperar la sesión y CSRF antes de modificar datos");
      headers["X-CSRF-Token"] = csrf;
    }
    if (path === "/api/auth/login") headers["X-CSRF-Protection"] = "1";
    const response = await fetch(`${baseUrl}${path}`, {
      method, credentials: "include", headers,
      body: isForm ? body as FormData : body ? JSON.stringify(body) : undefined,
    });
    if (!response.ok) {
      const payload = await response.json().catch(() => ({}));
      if (response.status === 401) csrf = undefined;
      const retry = Number(response.headers.get("Retry-After"));
      throw new ApiError(response.status, payload.detail, retry > 0 ? retry : undefined);
    }
    return response.status === 204 ? undefined : response.json();
  }

  return {
    async login(email: string, password: string) {
      const result = await request("/api/auth/login", "POST", { email, password });
      csrf = result.csrf_token;
      // Detecta cookie bloqueada: 200 de login por sí solo no confirma persistencia.
      try {
        await request("/api/auth/me");
      } catch (error) {
        csrf = undefined;
        if (error instanceof ApiError && error.status === 401) {
          throw new Error("El navegador no conservó la cookie de sesión. Comprueba compatibilidad/configuración de cookies; no repitas el login automáticamente.");
        }
        throw error;
      }
      return result.user;
    },
    async restore() {
      const me = await request("/api/auth/me");
      csrf = (await request("/api/auth/csrf")).csrf_token;
      return me.user;
    },
    async logout(all = false) {
      await request(all ? "/api/auth/logout-all" : "/api/auth/logout", "POST", {});
      csrf = undefined;
    },
    availability: (id: number, available: boolean) => request(`/api/admin/products/${id}/availability`, "PATCH", { is_available: available }),
    description: (id: number, description: string | null) => request(`/api/admin/products/${id}/description`, "PATCH", { description }),
    createTag: (name: string) => request("/api/admin/tags", "POST", { name }),
    attachTag: (variantId: number, tagId: number) => request(`/api/admin/variants/${variantId}/tags/${tagId}`, "PUT"),
    detachTag: (variantId: number, tagId: number) => request(`/api/admin/variants/${variantId}/tags/${tagId}`, "DELETE"),
    variantAvailability: (id: number, available: boolean) => request(`/api/admin/variants/${id}/availability`, "PATCH", { is_available: available }),
    changePassword: async (current: string, next: string) => {
      await request("/api/auth/change-password", "POST", { current_password: current, new_password: next });
      csrf = undefined;
    },
    request,
  };
}

// Menú del entorno académico; puede indicarse otra URL explícita para un laboratorio local.
export async function publicMenu(baseUrl = "https://mitbol-backend.onrender.com") {
  const response = await fetch(`${baseUrl}/api/menu`, { credentials: "omit" });
  if (!response.ok) throw new ApiError(response.status, "No se pudo consultar el menú");
  return response.json();
}
// UI: 401 -> login; 403 -> acceso/CSRF/contexto; 429 -> espera indicada, sin bucle automático.
// No reenviar automáticamente POST/PATCH tras 503: un commit puede ser incierto.
