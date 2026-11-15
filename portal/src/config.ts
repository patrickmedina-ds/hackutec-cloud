import type { Config } from './types';

const VACIA: Config = { apiUrl: '', region: 'us-east-1', cognitoDomain: '', clientId: '', redirectUri: '' };

export async function cargarConfig(): Promise<Config> {
  try {
    const r = await fetch(`${import.meta.env.BASE_URL}config.json`, { cache: 'no-store' });
    if (!r.ok) return VACIA;
    return { ...VACIA, ...(await r.json()) };
  } catch {
    return VACIA;
  }
}

export function esModoDemo(cfg: Config, search = window.location.search): boolean {
  return !cfg.apiUrl || new URLSearchParams(search).get('demo') === '1';
}
