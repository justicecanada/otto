import {check, sleep} from 'k6';
import http from 'k6/http';

export const options = {
  stages: [
    {duration: '10s', target: 20}, // Ramp-up
    {duration: '10s', target: 100}, // Ramp-up
    // {duration: '10s', target: 0}, // Ramp-down
  ],
  // vus: 100, // 10 virtual users
  // duration: '20s', // Duration of the test
};

// The function that defines VU logic.
//  
// See https://grafana.com/docs/k6/latest/examples/get-started-with-k6/ to learn more
// about authoring k6 scripts.
//
export default function () {
  const base_url = 'http://localhost:8000';
  // Basic load tests
  // let res = http.get(`${base_url}/load_test/`); // Basic request
  // let res = http.get(`${base_url}/load_test/?user_library_permissions`); // Simple Django DB query
  // let res = http.get(`${base_url}/load_test/?user_library_permissions&heavy`); // Heavy Django DB query (takes 40s on local)
  // let res = http.get(`${base_url}/load_test/?sleep=10`); // Sleep for 10 seconds. Can be combined with other parameters.
  // let res = http.get(`${base_url}/load_test/?celery_sleep=10`); // Adds a mock celery task that just sleeps for 10 seconds
  // let res = http.get(`${base_url}/load_test/?error`); // Raise an error in Django (returns 500)
  // let res = http.get(`${base_url}/load_test/?query_laws`); // Large vector DB query
  // let res = http.get(`${base_url}/load_test/?llm_call`); // LLM call for 1 word response
  // let res = http.get(`${base_url}/load_test/?llm_call&long_response`); // LLM call for 5 paragraph essay
  // let res = http.get(`${base_url}/load_test/?llm_call=gpt-4o`); // Specific LLM deployment
  // let res = http.get(`${base_url}/load_test/?embed_text`); // Embedding request with short input
  // let res = http.get(`${base_url}/load_test/?embed_text&long_input`); // Embedding request with long input
  // let res = http.get(`${base_url}/load_test/?mock_document_loading`); // Mocks embeddings but otherwise loads example.pdf

  // Chat/File Processing load test (memory intensive)
  // let res = http.get(`${base_url}/load_test/?summarize_pdf`); // Full PDF summarization (text extraction + LLM processing)
  // let res = http.get(`${base_url}/load_test/?summarize_pdf&mock_llm`); // Mock LLM
  // let res = http.get(`${base_url}/load_test/?summarize_pdf&num_files=3`); // 3 small files
  let res = http.get(`${base_url}/load_test/?summarize_pdf&num_files=3&mock_llm`);
  // let res = http.get(`${base_url}/load_test/?summarize_pdf=large.pdf`); // 1 large file
  // let res = http.get(`${base_url}/load_test/?summarize_pdf=large.pdf&mock_llm`);
  // let res = http.get(`${base_url}/load_test/?summarize_pdf=large.pdf&num_files=3&mock_llm`); // 3 large files, mock LLM

  check(res, {
    'status is 200': (r) => r.status === 200,
  });
  sleep(1);
} 
