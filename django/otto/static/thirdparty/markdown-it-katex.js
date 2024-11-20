// Modified from https://github.com/SchneeHertz/markdown-it-katex-gpt/blob/master/index.js
// to work in-browser (without Node.js require & export)

const katex = window.katex;

const defaultOptions = {
  delimiters: [
    {left: '\\[', right: '\\]', display: true},
    {left: '\\(', right: '\\)', display: false}
  ]
};

function escapedBracketRule(options) {
  return (state, silent) => {
    const max = state.posMax;
    const start = state.pos;

    for (const {left, right, display} of options.delimiters) {

      // Check if it starts with the left delimiter
      if (!state.src.slice(start).startsWith(left)) continue;

      // Skip the length of the left delimiter
      let pos = start + left.length;

      // Find the matching right delimiter
      while (pos < max) {
        if (state.src.slice(pos).startsWith(right)) {
          break;
        }
        pos++;
      }

      // If no matching right delimiter is found, skip to the next match
      if (pos >= max) continue;

      // If not in silent mode, convert LaTeX to MathML
      if (!silent) {
        const content = state.src.slice(start + left.length, pos);
        try {
          const renderedContent = katex.renderToString(content, {
            throwOnError: false,
            output: 'mathml',
            displayMode: display
          });
          const token = state.push('html_inline', '', 0);
          token.content = renderedContent;
        } catch (e) {
          console.error(e);
        }
      }

      // Update position, skipping the length of the right delimiter
      state.pos = pos + right.length;
      return true;
    }
  };
}

function katexPlugin(md, options = defaultOptions) {
  md.inline.ruler.after('text', 'escaped_bracket', escapedBracketRule(options));
}
