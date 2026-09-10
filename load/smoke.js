// k6 run -e BASE=http://localhost:8000 load/smoke.js
import http from 'k6/http';
import { check } from 'k6';
const BASE = __ENV.BASE || 'http://localhost:8000';
export const options = { vus: 2, duration: '30s' };
export default function () {
  check(http.get(`${BASE}/api/catalog/products?limit=20`), { '200': r => r.status === 200 });
  check(http.get(`${BASE}/healthz`), { 'alive': r => r.status === 200 });
}
