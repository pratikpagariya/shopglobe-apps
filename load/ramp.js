// THE BASELINE TEST. Day 2 (EC2) and Day 5 (EKS) both run this, unchanged.
// Record: p50, p95, p99, error rate, and the highest RPS held under 1% errors.
// k6 run -e BASE=http://... load/ramp.js
import http from 'k6/http';
import { check, group } from 'k6';
const BASE = __ENV.BASE || 'http://localhost:8000';
export const options = {
  stages: [
    { duration: '1m', target: 10 },
    { duration: '2m', target: 50 },
    { duration: '2m', target: 120 },
    { duration: '2m', target: 200 },
    { duration: '1m', target: 0 },
  ],
  thresholds: {
    // These are your SLO, asserted. A failing threshold fails the run.
    'http_req_duration{expected_response:true}': ['p(95)<300', 'p(99)<800'],
    http_req_failed: ['rate<0.01'],
  },
};
export default function () {
  group('browse', () => {
    check(http.get(`${BASE}/api/catalog/products?limit=20`), { ok: r => r.status === 200 });
  });
  group('cart', () => {
    const id = Math.floor(Math.random() * 500) + 1;
    check(http.post(`${BASE}/api/cart/cart/u1/items`, JSON.stringify({ product_id: id }),
      { headers: { 'Content-Type': 'application/json' } }), { ok: r => r.status === 200 });
  });
  group('order', () => {
    // idempotency_key must be unique per logical order, or order-svc dedupes it
    check(http.post(`${BASE}/api/order/orders`, JSON.stringify({
      user_id: 'u1', amount: 42.5, idempotency_key: `k6-${__VU}-${__ITER}`,
    }), { headers: { 'Content-Type': 'application/json' } }), { accepted: r => r.status === 202 });
  });
}
