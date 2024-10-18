const hasOverflow = (element) => {
    return element.scrollHeight > element.clientHeight;
};

const elements = document.querySelectorAll('.preset-description');
elements.forEach((element) => {
    if (hasOverflow(element)) {
        element.setAttribute('title', element.innerText);
    }
});
