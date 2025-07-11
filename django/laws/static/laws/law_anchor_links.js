
function scrollToSource(targetElement, smooth = true) {
  // Scroll to the element with the id of the href, leaving appropriate space
  const y = targetElement.getBoundingClientRect().top + window.pageYOffset - 16;
  window.scrollTo({top: y, behavior: smooth ? "smooth" : "instant"});
}

// Global cache for ID mappings and fuzzy matching
let idCache = new Map();
let actualIds = new Set();

function initializeIdCache() {
  // Cache all existing IDs on the page for fuzzy matching
  console.log("Initializing ID cache...");
  actualIds.clear();
  idCache.clear();
  document.querySelectorAll('#sources-container [id]').forEach(el => {
    actualIds.add(el.id);
  });
  console.log("Cached IDs:", Array.from(actualIds));
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

// Use event delegation - single listener on document for all answer links
document.addEventListener('click', function (e) {
  // Check if clicked element is an anchor link within #answer
  if (e.target.matches('#answer a[href^="#"]') || e.target.closest('#answer a[href^="#"]')) {
    console.log("Anchor link clicked:", e.target);
    const anchor = e.target.matches('a') ? e.target : e.target.closest('a');
    e.preventDefault();

    // Get the raw href and encode it if needed
    let href = anchor.getAttribute("href");

    // Encode common chars if not already encoded
    const encodedHref = href.replace(/[\(\)\*, ]/g, m => ({'(': '%28', ')': '%29', '*': '%2A', ',': '%2C', ' ': '%20'})[m]);
    if (href !== encodedHref) {
      anchor.setAttribute("href", encodedHref);
      href = encodedHref;
    }

    // Get and decode target id
    let id = href.slice(1);
    try {
      id = decodeURIComponent(id);
    } catch { }

    // Fuzzy match to real element
    const actual = findBestIdMatch(id);
    const target = actual && document.getElementById(actual);
    console.log("Actual link target:", actual, target);
    if (target) {
      showSourceDetails(null);
      target.classList.add("highlight");
      scrollToSource(target);
    }
  }
});

// When #result-container is swapped in (first search or any replace)
document.addEventListener('htmx:afterSwap', function (event) {
  if (event.detail.target && event.detail.target.id === 'result-container') {
    initializeIdCache();
  }
});

document.addEventListener("DOMContentLoaded", () => {
  initializeIdCache();
});
