document.body.addEventListener('htmx:afterSwap', function (event) {
  if (event.detail?.target?.id != "presets-modal-body") return;
  console.log('presetModal.js loaded');
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
  const searchInput = document.getElementById('filter-search');
  const clearBtn = document.getElementById('clear-filters');

  console.log('modeFilter', modeFilter);
  console.log('sharingFilter', sharingFilter);
  console.log('searchInput', searchInput);
  console.log('clearBtn', clearBtn);

  function normalize(str) {
    return (str || '').toLowerCase();
  }

  function getCardText(card) {
    return card.innerText.toLowerCase();
  }

  function filterCards() {
    console.log('Filtering cards');
    const mode = modeFilter.value;
    const sharing = sharingFilter.value;
    const search = normalize(searchInput.value);

    cards.forEach(card => {
      let show = true;
      if (mode && card.dataset.mode !== mode) show = false;
      if (sharing && card.dataset.sharing !== sharing) show = false;
      if (search && !getCardText(card).includes(search)) show = false;
      card.style.display = show ? '' : 'none';
    });
  }

  modeFilter.addEventListener('change', filterCards);
  sharingFilter.addEventListener('change', filterCards);
  searchInput.addEventListener('input', filterCards);
  clearBtn.addEventListener('click', function () {
    modeFilter.value = '';
    sharingFilter.value = '';
    searchInput.value = '';
    filterCards();
  });
});
