// Steady background load. Used during a deploy to measure requests lost.
import http from 'k6/http';
import { check } from 'k6';
const BASE = __ENV.BASE;
export const options = { vus: 20, duration: __ENV.DUR || '12m' };
export default function () {
  check(http.get(`${BASE}/api/catalog/products?limit=20`), { ok: (r) => r.status === 200 });
}
