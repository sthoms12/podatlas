import type { Category } from '../types/podatlas';

export const CATEGORY_COLORS: Record<Category, string> = {
  Travel: '#38bdf8',   // sky-400
  History: '#f59e0b',  // amber-500
  Food: '#fb7185',     // rose-400
  Culture: '#34d399',  // emerald-400
};

export function formatTime(sec: number): string {
  const clamped = Math.max(0, Math.floor(sec));
  const hours = Math.floor(clamped / 3600);
  const minutes = Math.floor((clamped % 3600) / 60);
  const seconds = clamped % 60;
  if (hours > 0) {
    return `${hours}:${String(minutes).padStart(2, '0')}:${String(seconds).padStart(2, '0')}`;
  }
  return `${String(minutes).padStart(2, '0')}:${String(seconds).padStart(2, '0')}`;
}

export function formatCoords(lat: number, lon: number): string {
  const latStr = `${Math.abs(lat).toFixed(2)}°${lat >= 0 ? 'N' : 'S'}`;
  const lonStr = `${Math.abs(lon).toFixed(2)}°${lon >= 0 ? 'E' : 'W'}`;
  return `${latStr}, ${lonStr}`;
}
