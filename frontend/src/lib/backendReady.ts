import { readJsonResponse } from './jsonResponse';

export interface BackendHealth {
  ready: boolean;
  mongodb: boolean;
  cameraCount: number;
  phase?: string;
  error?: string | null;
}

const DEFAULT_MAX_WAIT_MS = 180_000;
const POLL_MS = 2_000;

/** Poll /api/health until the backend finishes MongoDB startup and migrations. */
export async function waitForBackendReady(maxWaitMs = DEFAULT_MAX_WAIT_MS): Promise<BackendHealth> {
  const deadline = Date.now() + maxWaitMs;
  let last: BackendHealth | null = null;

  while Date.now() < deadline) {
    try {
      const res = await fetch('/api/health', { cache: 'no-store', credentials: 'include' });
      if (res.ok) {
        last = await readJsonResponse<BackendHealth>(res);
        if (last.ready) {
          return last;
        }
        if (last.error) {
          throw new Error(`Backend startup failed: ${last.error}`);
        }
      }
    } catch (err) {
      if (err instanceof Error && err.message.startsWith('Backend startup failed:')) {
        throw err;
      }
      // Network / proxy not up yet — keep polling
    }
    await new Promise((resolve) => setTimeout(resolve, POLL_MS));
  }

  const hint = last?.phase
    ? ` Last phase: ${last.phase}.`
    : ' Is the backend running on port 10000?';
  throw new Error(`Backend did not become ready in time.${hint}`);
}
