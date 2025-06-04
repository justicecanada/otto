// Template wizard scripts
function setActiveTab(e) {
  const tabs = document.querySelectorAll('#top-tabs .nav-link');
  tabs.forEach(tab => {
    tab.classList.remove('active');
    tab.classList.remove('fw-semibold');
  });
  e.classList.add('active');
  e.classList.add('fw-semibold');
}

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
