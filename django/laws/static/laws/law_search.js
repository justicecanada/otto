function setActiveTab(e) {
  const tabs = document.querySelectorAll('.nav-link');
  tabs.forEach(tab => {
    tab.classList.remove('active');
    tab.classList.remove('fw-semibold');
  });
  e.classList.add('active');
  e.classList.add('fw-semibold');
  if (e.id === 'basic-search-tab') {
    document.getElementById('advanced-search-outer').classList.add('d-none');
    document.getElementById('advanced-toggle').value = 'false';
  } else {
    document.getElementById('advanced-search-outer').classList.remove('d-none');
    document.getElementById('advanced-toggle').value = 'true';
  }
}

document.addEventListener("DOMContentLoaded", function () {
  setTimeout(() => {
    document.getElementById('basic-search-input').focus();
  }, 100);

  const textarea = document.getElementById("basic-search-input");
  const clearButton = document.getElementById("clear-button");
  const searchButton = document.getElementById("basic-search-button");

  textarea.addEventListener("input", function () {
    if (textarea.value) {
      clearButton.style.display = "inline";
      if (textarea.value.trim() !== "") {
        searchButton.disabled = false;
      } else {
        searchButton.disabled = true;
      }
    } else {
      clearButton.style.display = "none";
      searchButton.disabled = true;
    }
  });

  // When "Enter" is pressed in textarea (except when Shift is held), submit the form
  // then clear the textarea
  textarea.addEventListener("keydown", function (e) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      searchButton.click();
      textarea.value = "";
      clearButton.style.display = "none";
      searchButton.disabled = true;
    }
  });

  clearButton.addEventListener("click", function () {
    textarea.value = "";
    textarea.focus();
    clearButton.style.display = "none";
    searchButton.disabled = true;
  });

  const selectLawsOption = document.getElementById("id_search_laws_option");
  selectLawsOption.addEventListener("change", function (e) {
    const selectedOption = e.target.value;
    document.querySelectorAll(".acts-regs").forEach(el => {
      el.classList.add("d-none");
    });
    document.querySelector(".enabling-acts").classList.add("d-none");
    if (selectedOption === "specific_laws") {
      document.querySelectorAll(".acts-regs").forEach(el => {
        el.classList.remove("d-none");
      });
    } else if (selectedOption === "enabling_acts") {
      document.querySelector(".enabling-acts").classList.remove("d-none");
    }
  });

  const dateFilterOption = document.getElementById("id_date_filter_option");
  dateFilterOption.addEventListener("change", function (e) {
    const selectedOption = e.target.value;
    document.querySelector("#date-filters").classList.add("d-none");
    if (selectedOption === "filter_dates") {
      document.querySelector("#date-filters").classList.remove("d-none");
    }
  });

});

function showSourceDetails(button) {
  const details = document.getElementById('source-details');
  details.querySelector("#source-details-inner").innerHTML = '';
  document.querySelectorAll("#sources-container .card").forEach(card => {
    card.classList.remove("highlight");
  });
  if (button === null) {
    details.classList.add('d-none');
    return;
  }
  const card = button.closest('.card');
  card.classList.add("highlight");
  details.classList.remove('d-none');
  scrollToSource(card, false);
}

function scrollToSource(targetElement, smooth = true) {
  // Scroll to the element with the id of the href, leaving appropriate space
  const y = targetElement.getBoundingClientRect().top + window.pageYOffset - 16;
  window.scrollTo({top: y, behavior: smooth ? "smooth" : "instant"});
}

function findSimilar(el) {
  const text = el.getAttribute('data-text');
  // Populate the search form with the text
  document.getElementById('basic-search-input').value = text;
  // Trigger the input event so that the search button is enabled
  document.getElementById('basic-search-input').dispatchEvent(new Event('input'));
  // Change the tab to basic search
  setActiveTab(document.getElementById('basic-search-tab'));
  // Disable AI answer
  document.getElementById('ai_answer').checked = false;
  // Enable bilingual results
  document.getElementById('bilingual_results').checked = true;
  // Submit the form
  document.getElementById('basic-search-button').click();
}

const md = markdownit({
  highlight: function (str, lang) {
    if (lang && hljs.getLanguage(lang)) {
      try {
        return '<pre><code class="hljs">' +
          hljs.highlight(str, {language: lang, ignoreIllegals: true}).value +
          '</code></pre>';
      } catch (__) { }
    }

    return '<pre><code class="hljs">' + md.utils.escapeHtml(str) + '</code></pre>';
  }
});

md.use(katexPlugin);

function render_markdown(element) {
  // Render markdown in the element
  const markdown_text = element.querySelector(".markdown-text");
  if (markdown_text) {
    let to_parse = markdown_text.dataset.md;
    try {
      to_parse = JSON.parse(to_parse);
    } catch (e) {
      to_parse = false;
    }
    if (to_parse) {
      const parent = markdown_text.parentElement;
      parent.innerHTML = md.render(to_parse);
    }
  }
}

// Global cache for ID mappings and fuzzy matching
let idCache = new Map();
let actualIds = new Set();

function initializeIdCache() {
  // Cache all existing IDs on the page for fuzzy matching
  actualIds.clear();
  idCache.clear();
  document.querySelectorAll('[id]').forEach(el => {
    actualIds.add(el.id);
  });
}

function normalizeId(id) {
  // Normalize for comparison: lowercase, replace special chars
  return id.toLowerCase().replace(/[-_\s]/g, '').replace(/[()]/g, '');
}

function findBestIdMatch(targetId) {
  // Check cache first
  if (idCache.has(targetId)) {
    return idCache.get(targetId);
  }

  // Exact match
  if (actualIds.has(targetId)) {
    idCache.set(targetId, targetId);
    return targetId;
  }

  // Fuzzy match using normalization
  const normalizedTarget = normalizeId(targetId);
  let bestMatch = null;
  let bestScore = 0;

  for (const actualId of actualIds) {
    const normalizedActual = normalizeId(actualId);

    // Check if normalized versions match exactly
    if (normalizedTarget === normalizedActual) {
      idCache.set(targetId, actualId);
      return actualId;
    }

    // Simple similarity scoring for partial matches
    const similarity = calculateSimilarity(normalizedTarget, normalizedActual);
    if (similarity > 0.8 && similarity > bestScore) {
      bestScore = similarity;
      bestMatch = actualId;
    }
  }

  if (bestMatch) {
    idCache.set(targetId, bestMatch);
    return bestMatch;
  }

  // No match found
  idCache.set(targetId, null);
  return null;
}

function calculateSimilarity(str1, str2) {
  // Simple similarity based on longest common subsequence ratio
  if (str1 === str2) return 1;
  if (str1.length === 0 || str2.length === 0) return 0;

  // Check if one is contained in the other (common with ID variations)
  if (str1.includes(str2) || str2.includes(str1)) {
    return Math.min(str1.length, str2.length) / Math.max(str1.length, str2.length);
  }

  // Simple character overlap ratio
  const chars1 = new Set(str1);
  const chars2 = new Set(str2);
  const intersection = new Set([...chars1].filter(x => chars2.has(x)));
  const union = new Set([...chars1, ...chars2]);

  return intersection.size / union.size;
}

function update_anchor_links() {
  // Within the answer, find all anchor links. HTML escape the href apart from the #
  // and set the href to the escaped value
  const anchors = document.querySelectorAll("#answer a[href^='#']");
  anchors.forEach(anchor => {
    const href = anchor.getAttribute("href").replace("(", "%28").replace(")", "%29").replace("*", "%2A").replace(",", "%2C").replace(" ", "%20");
    anchor.setAttribute("href", href);

    // Remove any existing click listeners to prevent duplicates during streaming
    const newAnchor = anchor.cloneNode(true);
    anchor.replaceWith(newAnchor);

    // Override the default behaviour of anchor links. Scroll to the element with the id
    // of the href
    newAnchor.addEventListener("click", function (e) {
      e.preventDefault();

      // Decode the URL-encoded href back to original format for matching
      let targetId = href.substring(1);
      try {
        targetId = decodeURIComponent(targetId);
      } catch (err) {
        // If decoding fails, use original
        console.warn("Failed to decode href:", targetId);
      }

      // Use fuzzy matching to find the best ID match
      const actualTargetId = findBestIdMatch(targetId);
      const targetElement = actualTargetId ? document.getElementById(actualTargetId) : null;

      if (targetElement) {
        // Collapse details and remove the border from all other elements
        showSourceDetails(null);
        targetElement.classList.add("highlight");
        scrollToSource(targetElement);
      } else {
        console.warn("No element found for target:", targetId, "tried fuzzy match, got:", actualTargetId);
      }
    });
  });
}

// When streaming response is updated
document.addEventListener("htmx:sseMessage", function (event) {
  if (!(event.target.id === "answer-sse")) return;

  render_markdown(event.target);
  update_anchor_links();
});

// When streaming response is finished
document.addEventListener("htmx:oobAfterSwap", function (event) {
  if (!(event.target.id === "answer-sse")) return;
  render_markdown(event.target);
  update_anchor_links();
});

// When page loaded with existing answer
document.addEventListener("DOMContentLoaded", function () {
  // Initialize ID cache when page loads
  initializeIdCache();

  const answer = document.querySelector("#answer");
  if (answer) render_markdown(answer);
  update_anchor_links();
});

function toggleAnswer(show) {
  const answer = document.querySelector("#answer-column");
  const showBtn = document.querySelector("#show-answer-button");
  if (answer) answer.classList.toggle("d-none", !show);
  if (showBtn) showBtn.classList.toggle("d-none", show);
}

function hideAnswer() {
  toggleAnswer(false);
}

function showAnswer() {
  toggleAnswer(true);
}

// Show/hide back-to-top button on scroll
document.addEventListener('scroll', function () {
  const btn = document.getElementById('back-to-top');
  if (window.scrollY > 200) {
    btn.classList.remove('d-none');
  } else {
    btn.classList.add('d-none');
  }
});
// Scroll to top on click
document.getElementById('back-to-top').addEventListener('click', function () {
  window.scrollTo({top: 0, behavior: 'smooth'});
});
