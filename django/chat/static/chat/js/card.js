var hasOverflow = (element) => {
    return element.scrollHeight > element.clientHeight;
};

var wrappers = document.querySelectorAll('.text-wrapper');
wrappers.forEach((wrapper) => {
    let element = wrapper.querySelector('.preset-description');
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
            expandedText.style.boxShadow = '0px 4px 6px rgba(0, 0, 0, 0.1)';
            expandedText.whiteSpace = 'normal';

            // Prevent the overlay from capturing mouse events
            expandedText.style.pointerEvents = 'none';

            document.querySelector('.card-body').appendChild(expandedText);
        });

        wrapper.addEventListener('mouseleave', () => {
            // Remove the overlay and restore the truncated text visibility
            let overlay = document.querySelector('.expanded-text-overlay');
            if (overlay) {
                overlay.remove();
            }
            element.style.opacity = '1'; // Restore visibility
        });
    }
});
