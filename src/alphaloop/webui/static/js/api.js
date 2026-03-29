/**
 * Fetch wrapper with auth token support.
 */

let _authToken = localStorage.getItem('auth_token') || '';

export function setAuthToken(token) {
  _authToken = token;
  localStorage.setItem('auth_token', token);
}

export function getAuthToken() {
  return _authToken;
}

/**
 * Make an authenticated API request.
 * @param {string} url
 * @param {object} [options]
 * @returns {Promise<any>}
 */
export async function apiFetch(url, options = {}) {
  const headers = {
    'Content-Type': 'application/json',
    ...(options.headers || {}),
  };
  if (_authToken) {
    headers['Authorization'] = `Bearer ${_authToken}`;
  }

  const resp = await fetch(url, {
    ...options,
    headers,
  });

  if (!resp.ok) {
    const body = await resp.json().catch(() => ({ detail: resp.statusText }));
    throw new Error(body.detail || `HTTP ${resp.status}`);
  }

  return resp.json();
}

/**
 * Shorthand GET.
 */
export function apiGet(url) {
  return apiFetch(url);
}

/**
 * Shorthand POST with JSON body.
 */
export function apiPost(url, data) {
  return apiFetch(url, {
    method: 'POST',
    body: JSON.stringify(data),
  });
}

/**
 * Shorthand PUT with JSON body.
 */
export function apiPut(url, data) {
  return apiFetch(url, {
    method: 'PUT',
    body: JSON.stringify(data),
  });
}

/**
 * Shorthand PATCH with optional JSON body.
 */
export function apiPatch(url, data) {
  return apiFetch(url, {
    method: 'PATCH',
    body: data ? JSON.stringify(data) : undefined,
  });
}

/**
 * Shorthand DELETE.
 */
export function apiDelete(url) {
  return apiFetch(url, { method: 'DELETE' });
}
