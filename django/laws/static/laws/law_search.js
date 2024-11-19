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
  document.getElementById('basic-search-input').focus();

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
    card.classList.remove("border-4");
  });
  if (button === null) {
    details.classList.add('d-none');
    return;
  }
  const card = button.closest('.card');
  card.classList.add("border-4");
  details.classList.remove('d-none');
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
