// THE BASELINE TEST. Run against EC2 (day 2) and EKS (day 5) UNCHANGED.
//
// It deliberately exercises ONLY the catalog path through edge-gateway, because
// that is the one journey both stacks serve. The legacy stack runs 3 services;
// EKS will run 8. If this script hit cart or order it would pass on EKS and 404
// on EC2, and the comparison would be meaningless. A baseline is only valid if
// the workload is identical on both sides.
//
// k6 run -e BASE=http://<alb-or-ingress> load/ramp.js
import http from 'k6/http';
import { check, group } from 'k6';

const BASE = __ENV.BASE;

export const options = {
  stages: [
    { duration: '1m', target: 10 },
    { duration: '2m', target: 50 },
    { duration: '2m', target: 120 },
    { duration: '2m', target: 200 },
    { duration: '1m', target: 0 },
  ],
  thresholds: {
    // Your SLO, asserted. A breached threshold fails the run.
    'http_req_duration{expected_response:true}': ['p(95)<300', 'p(99)<800'],
    http_req_failed: ['rate<0.01'],
  },
};

export default function () {
  group('browse', () => {
    check(http.get(`${BASE}/api/catalog/products?limit=20`), { ok: (r) => r.status === 200 });
  });
  group('product', () => {
    const id = Math.floor(Math.random() * 500) + 1;
    check(http.get(`${BASE}/api/catalog/products/${id}`), { ok: (r) => r.status === 200 });
  });
}
