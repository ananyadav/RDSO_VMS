/** Parse JSON safely — surfaces HTML/proxy misroutes instead of opaque parse errors. */
export async function readJsonResponse<T>(response: Response): Promise<T> {
  const contentType = response.headers.get('content-type') ?? '';
  if (contentType.includes('application/json')) {
    return (await response.json()) as T;
  }
  const text = (await response.text()).trimStart();
  if (text.startsWith('<!')) {
    const hostHint =
      typeof window !== 'undefined' && window.location.hostname === 'localhost'
        ? ' Use http://127.0.0.1:8080/ for Live View — localhost is hijacked by Cursor on this machine.'
        : ' Open http://127.0.0.1:8080/ (or :3000 with Vite) and ensure the backend is on port 10000.';
    throw new Error(`API returned HTML instead of JSON.${hostHint}`);
  }
  throw new Error(`Unexpected API response (${response.status})`);
}
