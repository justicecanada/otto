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

  textarea.addEventListener("keypress", e => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      if (textarea.value.trim() !== "") {
        document.getElementById("basic-search-button").click();
      }
    }
  });

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
