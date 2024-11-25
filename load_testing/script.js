import {check, sleep} from 'k6';
import http from 'k6/http';

export const options = {
  // A number specifying the number of VUs to run concurrently.
  vus: 100,
  // A string specifying the total d  uration   of the test run.
  duration: '30s',
};

// The function that defines VU logic.
//  
// See https://grafana.com/docs/k6/latest/examples/get-started-with-k6/ to learn more
// about authoring k6 scripts.
//
export default function () {
  let res = http.get('http://localhost:8000/stress_test/?query_vector_db');
  check(res, {
    'status is 200': (r) => r.status === 200,
  });
  sleep(1);
}
