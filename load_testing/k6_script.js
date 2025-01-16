import {textSummary} from 'https://jslib.k6.io/k6-summary/0.0.1/index.js';
import {check, sleep} from 'k6';
import http from 'k6/http';

const appGithubHash = __ENV.APP_GITHUB_HASH || 'Not provided';
const infraGithubHash = __ENV.INFRA_GITHUB_HASH || 'Not provided';
const outputDir = __ENV.OUTPUT_DIR;
const base_url = __ENV.BASE_URL;
const testCase = __ENV.TEST_CASE;
const testCaseNumber = parseInt(testCase, 10);

const test_cases = [
  {
    name: 'basic_request',
    url: `${base_url}/load_test/`,
    description: "Simulates a basic request to the application without any specific operations.",
    rationale: "Establishes a baseline for system response under minimal load.",
    probability: "Low (5-10% of interactions, mostly for system checks)",
    scenarios: {
      constant_load: {
        executor: 'constant-vus',
        vus: 50,
        duration: '2m',
        startTime: '0s',
      },
      ramp_up: {
        executor: 'ramping-vus',
        startVUs: 0,
        stages: [
          {duration: '1m', target: 10},
          {duration: '1m', target: 100},
        ],
        startTime: '2m',
      }
    }
  },
  {
    name: 'user_library_permissions',
    url: `${base_url}/load_test/?user_library_permissions`,
    description: "Tests user library permission checks, simulating access control operations.",
    rationale: "Ensures the system can handle frequent permission checks without degradation.",
    probability: "High (70-80% of user sessions will involve permission checks)",
    scenarios: {
      constant_load: {
        executor: 'constant-vus',
        vus: 75,
        duration: '3m',
        startTime: '0s',
      },
      ramp_up: {
        executor: 'ramping-vus',
        startVUs: 0,
        stages: [
          {duration: '1m30s', target: 20},
          {duration: '1m30s', target: 150},
        ],
        startTime: '3m',
      }
    }
  },
  {
    name: 'heavy_db_query',
    url: `${base_url}/load_test/?user_library_permissions&heavy`,
    description: "Simulates complex database queries that might occur during detailed searches or reports.",
    rationale: "Tests the system's ability to handle resource-intensive database operations.",
    probability: "Medium (30-40% of interactions might involve heavy queries)",
    scenarios: {
      constant_load: {
        executor: 'constant-vus',
        vus: 30,
        duration: '4m',
        startTime: '0s',
      },
      ramp_up: {
        executor: 'ramping-vus',
        startVUs: 0,
        stages: [
          {duration: '2m', target: 10},
          {duration: '2m', target: 60},
        ],
        startTime: '4m',
      }
    }
  },
  {
    name: 'sleep_10',
    url: `${base_url}/load_test/?sleep=10`,
    description: "Simulates long-running operations with a 10-second delay.",
    rationale: "Tests how the system handles and manages long-running requests.",
    probability: "Low-Medium (10-20% of operations might be long-running)",
    scenarios: {
      constant_load: {
        executor: 'constant-vus',
        vus: 20,
        duration: '3m',
        startTime: '0s',
      },
      ramp_up: {
        executor: 'ramping-vus',
        startVUs: 0,
        stages: [
          {duration: '1m30s', target: 5},
          {duration: '1m30s', target: 40},
        ],
        startTime: '3m',
      }
    }
  },
  {
    name: 'celery_sleep',
    url: `${base_url}/load_test/?celery_sleep=10`,
    description: "Tests the system's handling of asynchronous tasks using Celery.",
    rationale: "Ensures the system can manage and process background tasks effectively.",
    probability: "Medium (40-50% of operations might involve background processing)",
    scenarios: {
      constant_load: {
        executor: 'constant-vus',
        vus: 40,
        duration: '3m',
        startTime: '0s',
      },
      ramp_up: {
        executor: 'ramping-vus',
        startVUs: 0,
        stages: [
          {duration: '1m30s', target: 10},
          {duration: '1m30s', target: 80},
        ],
        startTime: '3m',
      }
    }
  },
  {
    name: 'raise_error',
    url: `${base_url}/load_test/?error`,
    description: "Deliberately triggers errors to test error handling and logging.",
    rationale: "Ensures the system gracefully handles and logs errors without overall degradation.",
    probability: "Low (5-10% of requests might result in errors)",
    scenarios: {
      constant_load: {
        executor: 'constant-vus',
        vus: 10,
        duration: '2m',
        startTime: '0s',
      },
      ramp_up: {
        executor: 'ramping-vus',
        startVUs: 0,
        stages: [
          {duration: '1m', target: 5},
          {duration: '1m', target: 20},
        ],
        startTime: '2m',
      }
    }
  },
  {
    name: 'query_laws',
    url: `${base_url}/load_test/?query_laws`,
    description: "Simulates queries to the law database, likely involving vector searches.",
    rationale: "Tests the performance of vector database searches, a critical operation for the application.",
    probability: "High (60-70% of user interactions might involve law queries)",
    scenarios: {
      constant_load: {
        executor: 'constant-vus',
        vus: 60,
        duration: '4m',
        startTime: '0s',
      },
      ramp_up: {
        executor: 'ramping-vus',
        startVUs: 0,
        stages: [
          {duration: '2m', target: 20},
          {duration: '2m', target: 120},
        ],
        startTime: '4m',
      }
    }
  },
  {
    name: 'llm_call',
    url: `${base_url}/load_test/?llm_call`,
    description: "Simulates calls to the Language Model for generating responses.",
    rationale: "Tests the system's integration with the LLM and its ability to handle concurrent LLM requests.",
    probability: "High (70-80% of interactions will likely involve LLM calls)",
    scenarios: {
      constant_load: {
        executor: 'constant-vus',
        vus: 50,
        duration: '3m',
        startTime: '0s',
      },
      ramp_up: {
        executor: 'ramping-vus',
        startVUs: 0,
        stages: [
          {duration: '1m30s', target: 10},
          {duration: '1m30s', target: 100},
        ],
        startTime: '3m',
      }
    }
  },
  {
    name: 'long_response',
    url: `${base_url}/load_test/?llm_call&long_response`,
    description: "Tests LLM calls that generate longer responses, such as summaries or detailed explanations.",
    rationale: "Ensures the system can handle resource-intensive LLM operations and manage longer response times.",
    probability: "Medium (30-40% of LLM calls might require longer responses)",
    scenarios: {
      constant_load: {
        executor: 'constant-vus',
        vus: 30,
        duration: '4m',
        startTime: '0s',
      },
      ramp_up: {
        executor: 'ramping-vus',
        startVUs: 0,
        stages: [
          {duration: '2m', target: 10},
          {duration: '2m', target: 60},
        ],
        startTime: '4m',
      }
    }
  },
  {
    name: 'specific_llm',
    url: `${base_url}/load_test/?llm_call=gpt-4o`,
    description: "Tests calls to a specific LLM model (in this case, GPT-4).",
    rationale: "Evaluates the performance and capacity of a particular LLM deployment.",
    probability: "Medium-High (50-60% of LLM calls might use this specific model)",
    scenarios: {
      constant_load: {
        executor: 'constant-vus',
        vus: 40,
        duration: '3m',
        startTime: '0s',
      },
      ramp_up: {
        executor: 'ramping-vus',
        startVUs: 0,
        stages: [
          {duration: '1m30s', target: 10},
          {duration: '1m30s', target: 80},
        ],
        startTime: '3m',
      }
    }
  },
  {
    name: 'embed_text',
    url: `${base_url}/load_test/?embed_text`,
    description: "Simulates text embedding operations, likely for vector database indexing.",
    rationale: "Tests the system's ability to handle text embedding tasks, which are crucial for document indexing and searching.",
    probability: "Medium (40-50% of document operations might involve embedding)",
    scenarios: {
      constant_load: {
        executor: 'constant-vus',
        vus: 35,
        duration: '3m',
        startTime: '0s',
      },
      ramp_up: {
        executor: 'ramping-vus',
        startVUs: 0,
        stages: [
          {duration: '1m30s', target: 10},
          {duration: '1m30s', target: 70},
        ],
        startTime: '3m',
      }
    }
  },
  {
    name: 'long_input',
    url: `${base_url}/load_test/?embed_text&long_input`,
    description: "Tests embedding operations with longer text inputs.",
    rationale: "Ensures the system can handle embedding of larger documents or text chunks efficiently.",
    probability: "Low-Medium (20-30% of embedding operations might involve long inputs)",
    scenarios: {
      constant_load: {
        executor: 'constant-vus',
        vus: 25,
        duration: '4m',
        startTime: '0s',
      },
      ramp_up: {
        executor: 'ramping-vus',
        startVUs: 0,
        stages: [
          {duration: '2m', target: 5},
          {duration: '2m', target: 50},
        ],
        startTime: '4m',
      }
    }
  },
  {
    name: 'mock_document_loading',
    url: `${base_url}/load_test/?mock_document_loading`,
    description: "Simulates the process of loading and processing documents.",
    rationale: "Tests the system's document handling capabilities, including potential embedding and indexing operations.",
    probability: "Medium-High (50-60% of user sessions might involve document loading)",
    scenarios: {
      constant_load: {
        executor: 'constant-vus',
        vus: 45,
        duration: '4m',
        startTime: '0s',
      },
      ramp_up: {
        executor: 'ramping-vus',
        startVUs: 0,
        stages: [
          {duration: '2m', target: 15},
          {duration: '2m', target: 90},
        ],
        startTime: '4m',
      }
    }
  },
  {
    name: 'mixed_workload_light',
    url: `${base_url}/load_test/?query_laws&llm_call`,
    description: "Simulates a mix of law queries and short LLM responses, representing typical user interactions.",
    rationale: "Tests the system's performance under a realistic combination of common operations.",
    probability: "High (This represents a common usage pattern)",
    scenarios: {
      constant_load: {
        executor: 'constant-vus',
        vus: 70,
        duration: '5m',
        startTime: '0s',
      },
      ramp_up: {
        executor: 'ramping-vus',
        startVUs: 0,
        stages: [
          {duration: '2m30s', target: 20},
          {duration: '2m30s', target: 140},
        ],
        startTime: '5m',
      }
    }
  },
  {
    name: 'mixed_workload_heavy',
    url: `${base_url}/load_test/?query_laws&llm_call=gpt-4o&long_response&mock_document_loading`,
    description: "Simulates a complex mix of operations including law queries, long LLM responses, and document processing.",
    rationale: "Stress tests the system under a combination of resource-intensive operations.",
    probability: "Medium (Represents more intensive user sessions)",
    scenarios: {
      constant_load: {
        executor: 'constant-vus',
        vus: 40,
        duration: '6m',
        startTime: '0s',
      },
      ramp_up: {
        executor: 'ramping-vus',
        startVUs: 0,
        stages: [
          {duration: '2m', target: 10},
          {duration: '2m', target: 40},
          {duration: '2m', target: 80},
        ],
        startTime: '6m',
      }
    }
  },
  {
    name: 'peak_usage_simulation',
    url: `${base_url}/load_test/?query_laws&llm_call&user_library_permissions`,
    description: "Simulates peak usage conditions with a mix of common operations.",
    rationale: "Tests the system's ability to handle high concurrent load of diverse operations.",
    probability: "Low (Represents occasional peak usage periods)",
    scenarios: {
      constant_high_load: {
        executor: 'constant-vus',
        vus: 150,
        duration: '5m',
        startTime: '0s',
      },
      rapid_ramp_up_and_down: {
        executor: 'ramping-vus',
        startVUs: 0,
        stages: [
          {duration: '2m', target: 50},
          {duration: '2m', target: 200},
          {duration: '2m', target: 50},
        ],
        startTime: '5m',
      }
    }
  }
];

const currentTest = !isNaN(testCaseNumber) ? test_cases[testCaseNumber] : test_cases.find(test => test.name === testCase);

export const options = currentTest ? {
  scenarios: currentTest.scenarios,
} : {};

if (!currentTest) {
  console.log("No test case found");
}

export function handleSummary(data) {
  const summaries = {};
  const fileName = `${currentTest.name}.txt`;

  const now = new Date();
  const date = now.toISOString().split('T')[0];
  const time = now.toTimeString().split(' ')[0];

  let summaryText = `Test Case: ${currentTest.name}

Description: ${currentTest.description}
Rationale: ${currentTest.rationale}
Probability: ${currentTest.probability}

URL: ${currentTest.url}

Date: ${date}
Time: ${time}

Application GitHub Hash: ${appGithubHash}
Infrastructure GitHub Hash: ${infraGithubHash}

`;

  for (const [scenarioName, scenario] of Object.entries(currentTest.scenarios)) {
    summaryText += `----------------------------------------------------------
Scenario: ${scenarioName}
Options: ${JSON.stringify(scenario, null, 2)}

Results:
${textSummary(data, {indent: " ", enableColors: false})}

`;
  }

  summaries[`${outputDir}/${fileName}`] = summaryText;

  return summaries;
}

export default function () {
  let res = http.get(currentTest.url);
  check(res, {
    'status is 200': (r) => r.status === 200,
  });
  sleep(1);
}
