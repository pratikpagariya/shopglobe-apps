// Sudden 10x. Watch HPA react, then Cluster Autoscaler add nodes, and measure
// TIME-TO-CAPACITY -- that number is a migration scorecard row.
import http from 'k6/http';
const BASE = __ENV.BASE || 'http://localhost:8000';
export const options = {
  stages: [
    { duration: '30s', target: 10 },
    { duration: '10s', target: 300 },   // the spike
    { duration: '2m', target: 300 },
    { duration: '30s', target: 10 },
  ],
};
export default function () { http.get(`${BASE}/api/catalog/products?limit=20`); }
