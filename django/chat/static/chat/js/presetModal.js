(function () {
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
})();
