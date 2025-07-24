document.body.addEventListener('htmx:afterSwap', function (event) {
  if (event.detail?.target?.id != "presets-modal-body") return;
  const hasOverflow = (element) => {
    return element.scrollHeight > element.clientHeight;
  };

  const elements = document.querySelectorAll('#preset-card-list .preset-description');
  elements.forEach((element) => {
    if (hasOverflow(element)) {
      element.title = element.innerText;
    }
  });

  const cards = document.querySelectorAll('#preset-card-list li.card');
  cards.forEach((card) => {
    // When card is clicked (except for buttons and links),
    // trigger click event on child a.preset-load-link
    card.addEventListener('click', (event) => {
      const target = event.target;
      // Ensure target is not a button or link, or WITHIN a button or link
      if (target.tagName !== 'A' && target.tagName !== 'BUTTON' && !target.closest('a') && !target.closest('button')) {
        const link = card.querySelector('a.preset-load-link');
        if (link) {
          link.click();
        }
      }
    });
  });

  const modeFilter = document.getElementById('filter-mode');
  const sharingFilter = document.getElementById('filter-sharing');
  const languageFilter = document.getElementById('filter-language');
  const searchInput = document.getElementById('filter-search');
  const clearBtn = document.getElementById('clear-filters');

  function getCardText(card) {
    return card.innerText.toLowerCase();
  }

  function filterCards() {
    const mode = modeFilter.value;
    const sharing = sharingFilter.value;
    const language = languageFilter ? languageFilter.value : '';
    const search = normalize(searchInput.value);

    cards.forEach(card => {
      let show = true;
      if (mode && card.dataset.mode !== mode) show = false;
      if (sharing && card.dataset.sharing !== sharing) show = false;
      // Language filter logic
      if (language === 'en' && card.dataset.language === 'fr') show = false;
      if (language === 'fr' && card.dataset.language === 'en') show = false;
      if (search && !getCardText(card).includes(search)) show = false;
      card.style.display = show ? '' : 'none';
    });
  }

  modeFilter.addEventListener('change', filterCards);
  sharingFilter.addEventListener('change', filterCards);
  languageFilter.addEventListener('change', filterCards);
  searchInput.addEventListener('input', filterCards);
  clearBtn.addEventListener('click', function () {
    modeFilter.value = '';
    sharingFilter.value = '';
    languageFilter.value = '';
    searchInput.value = '';
    filterCards();
  });

  filterCards();
});
