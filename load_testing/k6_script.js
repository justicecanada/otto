import {check, sleep} from 'k6';
import http from 'k6/http';

export const options = {
  // stages: [
  //   {duration: '10s', target: 50}, // Ramp-up to 50 VUs in 10 seconds
  //   {duration: '10s', target: 100}, // Ramp-up to 100 VUs in the next 10 seconds
  //   {duration: '10s', target: 150}, // Ramp-up to 150 VUs in the next 10 seconds
  //   {duration: '10s', target: 150}, // Stay at 150 VUs for 10 seconds
  //   {duration: '10s', target: 0}, // Ramp-down to 0 VUs in 10 seconds
  // ],
  vus: 150, // 10 virtual users
  duration: '20s', // Duration of the test
};

// The function that defines VU logic.
//  
// See https://grafana.com/docs/k6/latest/examples/get-started-with-k6/ to learn more
// about authoring k6 scripts.
//
export default function () {
  const base_url = 'http://localhost:8000';
  // let res = http.get(`${base_url}/load_test/`); // Basic request
  // let res = http.get(`${base_url}/load_test/?user_library_permissions`); // Simple Django DB query
  // let res = http.get(`${base_url}/load_test/?user_library_permissions&heavy`); // Heavy Django DB query (takes 40s on local)
  // let res = http.get(`${base_url}/load_test/?sleep=10`); // Sleep for 10 seconds. Can be combined with other parameters.
  // let res = http.get(`${base_url}/load_test/?celery_sleep=10`); // Adds a mock celery task that just sleeps for 10 seconds
  // let res = http.get(`${base_url}/load_test/?error`); // Raise an error in Django (returns 500)
  let res = http.get(`${base_url}/load_test/?query_laws`); // Large vector DB query
  // let res = http.get(`${base_url}/load_test/?llm_call`); // LLM call for 1 word response
  // let res = http.get(`${base_url}/load_test/?llm_call&long_response`); // LLM call for 5 paragraph essay
  // let res = http.get(`${base_url}/load_test/?llm_call=gpt-4o`); // Specific LLM deployment
  // let res = http.get(`${base_url}/load_test/?embed_text`); // Embedding request with short input
  // let res = http.get(`${base_url}/load_test/?embed_text&long_input`); // Embedding request with long input
  check(res, {
    'status is 200': (r) => r.status === 200,
  });
  sleep(1);
}
