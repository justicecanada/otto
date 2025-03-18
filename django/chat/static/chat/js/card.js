var hasOverflow = (element) => {
    return element.scrollHeight > element.clientHeight;
};

var elements = document.querySelectorAll('.preset-description');
elements.forEach((element) => {
    if (hasOverflow(element)) {
        element.addEventListener('mouseenter', () => {
            element.style.height = element.scrollHeight + 'px'; // Set height to full content height
            element.style.position = 'absolute';
            element.style.backgroundColor = 'rgb(255, 255, 255)';
            element.style.width = '89%';
            element.style.border = '1px solid #e0e0e0';

        });
        element.addEventListener('mouseleave', () => {
            element.style.height = ''; // Reset height on mouse leave
            element.style.position = ''; // Reset position
            element.style.backgroundColor = ''; // Reset background color
            element.style.width = ''; // Reset width
            element.style.border = ''; // Reset border

        });
    }
});
