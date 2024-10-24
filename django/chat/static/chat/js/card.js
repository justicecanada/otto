var hasOverflow = (element) => {
    return element.scrollHeight > element.clientHeight;
};

var elements = document.querySelectorAll('.preset-description');
elements.forEach((element) => {
    if (hasOverflow(element)) {
        element.setAttribute('title', element.innerText);
    }
});
