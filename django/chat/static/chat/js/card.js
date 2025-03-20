var hasOverflow = (element) => {
    return element.scrollHeight > element.clientHeight;
};

var wrappers = document.querySelectorAll('.text-wrapper');
wrappers.forEach((wrapper) => {
    let element = wrapper.querySelector('.preset-description');
    if (hasOverflow(element)) {
        console.log('Overflow detected');
        let card = wrapper.closest('.card');
        let cardBody = card.querySelector('.card-body');
        if (card) {
            card.querySelectorAll('.fs-5').forEach((fs4Element) => {
                fs4Element.classList.remove('fs-5');
                fs4Element.classList.add('fs-6');
            });
            card.querySelectorAll('.fs-4').forEach((fs4Element) => {
                fs4Element.classList.remove('fs-4');
                fs4Element.classList.add('fs-6');
            });

            card.style.height = card.offsetHeight + 'px'; // Lock the card's current height
            element.style.display = '-webkit-box'; // Required for line clamping
            element.style.webkitBoxOrient = 'vertical'; // Required for line clamping
            element.style.overflow = 'hidden'; // Required for line clamping
            element.style.webkitLineClamp = '3'; // Allow up to 4 lines
        }

        let buttons = card.querySelectorAll('.btn-lg');
        buttons.forEach((button) => {
            button.classList.remove('btn-lg');
            button.classList.add('btn-small');
        });

        if (hasOverflow(element)) {
            wrapper.addEventListener('mouseenter', () => {
                // Create an overlay for the expanded text
                let expandedText = document.createElement('div');
                expandedText.className = 'expanded-text-overlay';
                expandedText.textContent = element.textContent;
                expandedText.style.position = 'absolute';
                expandedText.style.top = element.offsetTop + 'px';
                expandedText.style.left = element.offsetLeft + 'px';
                expandedText.style.width = element.offsetWidth + 'px';
                expandedText.style.backgroundColor = 'rgb(255, 255, 255)';
                expandedText.style.border = '1px solid rgb(148, 148, 148)';
                expandedText.style.padding = '0.5em';
                expandedText.style.boxShadow = '0px 4px 6px rgb(0 0 0 / 0.1)';
                expandedText.whiteSpace = 'normal';

                // Prevent the overlay from capturing mouse events
                expandedText.style.pointerEvents = 'none';

                cardBody.appendChild(expandedText);

            });

            wrapper.addEventListener('mouseleave', () => {
                // Remove the overlay and restore the truncated text visibility
                let overlay = cardBody.querySelector('.expanded-text-overlay');
                if (overlay) {
                    overlay.remove();
                }
                element.style.opacity = '1'; // Restore visibility
            });
        };
    }
});
