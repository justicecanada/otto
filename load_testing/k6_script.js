import {check, sleep} from 'k6';
import http from 'k6/http';

export const options = {
  // A number specifying the number of VUs to run concurrently.
  vus: 100,
  // A string specifying the total duration of the test run.
  duration: '30s',
};

// The function that defines VU logic.
//  
// See https://grafana.com/docs/k6/latest/examples/get-started-with-k6/ to learn more
// about authoring k6 scripts.
//
export default function () {
  const base_url = 'https://otto-sandbox.canadacentral.cloudapp.azure.com';
  // let res = http.get(`${base_url}/load_test/`); // Basic request
  // let res = http.get(`${base_url}/load_test/?user_library_permissions`); // Simple Django DB query
  // let res = http.get(`${base_url}/load_test/?user_library_permissions&heavy`); // Heavy Django DB query (takes 40s on local)
  // let res = http.get(`${base_url}/load_test/?sleep=10`); // Sleep for 10 seconds. Can be combined with other parameters.
  // let res = http.get(`${base_url}/load_test/?sleep=10&show_queue`); // Returns the queue lengths in the response
  // let res = http.get(`${base_url}/load_test/?error`); // Raise an error in Django (returns 500)
  // let res = http.get(`${base_url}/load_test/?query_laws`); // Large vector DB query
  // let res = http.get(`${base_url}/load_test/?llm_call`); // LLM call
  // let res = http.get(`${base_url}/load_test/?llm_call=gpt-4o`); // Specific LLM deployment
  // let res = http.get(`${base_url}/load_test/?embed_text`); // Embedding request
  let res = http.get(`${base_url}/load_test/?celery_sleep=5`); // Add mock Celery task and see queue lengths
  check(res, {
    'status is 200': (r) => r.status === 200,
  });
  sleep(1);
}
