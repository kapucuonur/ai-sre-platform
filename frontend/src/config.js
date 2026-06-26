let apiBase = import.meta.env.VITE_API_URL || '';
if (apiBase && !apiBase.startsWith('http://') && !apiBase.startsWith('https://') && !apiBase.startsWith('/')) {
  apiBase = 'https://' + apiBase;
}
export const API_BASE = apiBase;

