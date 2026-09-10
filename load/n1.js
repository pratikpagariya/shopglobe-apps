// Proves the deliberate N+1 in catalog-svc. Run both, compare p95.
// The gap between them is what an N+1 costs, in milliseconds, on your hardware.
import http from 'k6/http';
import { Trend } from 'k6/metrics';
const BASE = __ENV.BASE || 'http://localhost:8000';
const good = new Trend('latency_eager_load', true);
const bad = new Trend('latency_n_plus_one', true);
export const options = { vus: 10, duration: '1m' };
export default function () {
  good.add(http.get(`${BASE}/api/catalog/products?limit=20`).timings.duration);
  bad.add(http.get(`${BASE}/api/catalog/products-n1?limit=20`).timings.duration);
}
