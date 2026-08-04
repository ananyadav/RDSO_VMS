/** Parse JSON safely — surfaces HTML/proxy misroutes instead of opaque parse errors. */
export async function readJsonResponse<T>(response: Response): Promise<T> {
  const contentType = response.headers.get('content-type') ?? '';
  if (contentType.includes('application/json')) {
    return (await response.json()) as T;
  }
  const text = (await response.text()).trimStart();
  if (text.startsWith('<!')) {
    throw new Error(
      'API returned HTML instead of JSON. Open http://127.0.0.1:3000 and ensure the backend is running.',
    );
  }
  throw new Error(`Unexpected API response (${response.status})`);
}
