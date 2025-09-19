
// Paste markdown text into the chat input
(function () {
  'use strict';

  // http://pandoc.org/README.html#pandocs-markdown
  var pandoc = [
    {
      filter: 'h1',
      replacement: function (content, node) {
        var underline = Array(content.length + 1).join('=');
        return '\n\n' + content + '\n' + underline + '\n\n';
      }
    },

    {
      filter: 'h2',
      replacement: function (content, node) {
        var underline = Array(content.length + 1).join('-');
        return '\n\n' + content + '\n' + underline + '\n\n';
      }
    },

    {
      filter: 'sup',
      replacement: function (content) {
        return '^' + content + '^';
      }
    },

    {
      filter: 'sub',
      replacement: function (content) {
        return '~' + content + '~';
      }
    },

    {
      filter: 'br',
      replacement: function () {
        return '\\\n';
      }
    },

    {
      filter: 'hr',
      replacement: function () {
        return '\n\n* * * * *\n\n';
      }
    },

    {
      filter: ['em', 'i', 'cite', 'var'],
      replacement: function (content) {
        return '*' + content + '*';
      }
    },

    {
      filter: function (node) {
        var hasSiblings = node.previousSibling || node.nextSibling;
        var isCodeBlock = node.parentNode.nodeName === 'PRE' && !hasSiblings;
        var isCodeElem = node.nodeName === 'CODE' ||
          node.nodeName === 'KBD' ||
          node.nodeName === 'SAMP' ||
          node.nodeName === 'TT';

        return isCodeElem && !isCodeBlock;
      },
      replacement: function (content) {
        return '`' + content + '`';
      }
    },

    {
      filter: function (node) {
        return node.nodeName === 'A' && node.getAttribute('href');
      },
      replacement: function (content, node) {
        var url = node.getAttribute('href');
        var titlePart = node.title ? ' "' + node.title + '"' : '';
        if (content === url) {
          return '<' + url + '>';
        } else if (url === ('mailto:' + content)) {
          return '<' + content + '>';
        } else {
          return '[' + content + '](' + url + titlePart + ')';
        }
      }
    },

    {
      filter: 'li',
      replacement: function (content, node) {
        content = content.replace(/^\s+/, '').replace(/\n/gm, '\n    ');
        var prefix = '-   ';
        var parent = node.parentNode;

        if (/ol/i.test(parent.nodeName)) {
          var index = Array.prototype.indexOf.call(parent.children, node) + 1;
          prefix = index + '. ';
          while (prefix.length < 4) {
            prefix += ' ';
          }
        }

        return prefix + content;
      }
    }
  ];

  // http://pandoc.org/README.html#smart-punctuation
  var escape = function (str) {
    return str.replace(/[\u2018\u2019\u00b4]/g, "'")
      .replace(/[\u201c\u201d\u2033]/g, '"')
      .replace(/[\u2212\u2022\u00b7\u25aa]/g, '-')
      .replace(/[\u2013\u2015]/g, '--')
      .replace(/\u2014/g, '---')
      .replace(/\u2026/g, '...')
      .replace(/[ ]+\n/g, '\n')
      .replace(/\s*\\\n/g, '\\\n')
      .replace(/\s*\\\n\s*\\\n/g, '\n\n')
      .replace(/\s*\\\n\n/g, '\n\n')
      .replace(/\n-\n/g, '\n')
      .replace(/\n\n\s*\\\n/g, '\n\n')
      .replace(/\n\n\n*/g, '\n\n')
      .replace(/[ ]+$/gm, '')
      .replace(/^\s+|[\s\\]+$/g, '');
  };

  var cleanHtml = function (html) {
    // Remove the CSS/font definition prelude that Word adds before the actual content
    // This removes everything from the start up to the first actual content tag
    html = html
      // Remove HTML comments (which contain the CSS font definitions)
      .replace(/<!--[\s\S]*?-->/g, '')
      // Remove style blocks
      .replace(/<style[\s\S]*?<\/style>/gi, '')
      .trim();

    return html;
  };

  var convert = function (str) {
    // Clean the HTML first to remove Microsoft Word cruft
    str = cleanHtml(str);

    // Use Turndown.js with GFM plugin for better table support
    var turndownService = new TurndownService({
      headingStyle: 'setext',
      hr: '* * * * *',
      bulletListMarker: '-',
      codeBlockStyle: 'fenced',
      emDelimiter: '*'
    });

    // Use the GFM plugin for tables, strikethrough, etc.
    if (typeof TurndownPluginGfmService !== 'undefined') {
      turndownService.use(TurndownPluginGfmService.tables);
      turndownService.use(TurndownPluginGfmService.strikethrough);
    }

    // Add custom rules for pandoc-style conversions
    pandoc.forEach(function (rule) {
      if (rule.filter && rule.replacement) {
        turndownService.addRule('custom_' + Math.random().toString(36).substr(2, 9), {
          filter: rule.filter,
          replacement: rule.replacement
        });
      }
    });

    return escape(turndownService.turndown(str));
  };

  var insert = function (myField, myValue) {
    if (document.selection) {
      myField.focus();
      sel = document.selection.createRange();
      sel.text = myValue;
      sel.select();
    } else {
      if (myField.selectionStart || myField.selectionStart == "0") {
        var startPos = myField.selectionStart;
        var endPos = myField.selectionEnd;
        var beforeValue = myField.value.substring(0, startPos);
        var afterValue = myField.value.substring(endPos, myField.value.length);
        myField.value = beforeValue + myValue + afterValue;
        myField.selectionStart = startPos + myValue.length;
        myField.selectionEnd = startPos + myValue.length;
        myField.focus();
      } else {
        myField.value += myValue;
        myField.focus();
      }
    }
  };

  // Handle paste events for the chat prompt to convert rich text to markdown
  document.addEventListener('DOMContentLoaded', function () {
    var chatPrompt = document.querySelector('#chat-prompt');

    if (chatPrompt) {
      chatPrompt.addEventListener('paste', function (event) {
        // Get clipboard data
        var clipboardData = event.clipboardData || window.clipboardData;

        if (clipboardData) {
          var htmlData = clipboardData.getData('text/html');
          var plainData = clipboardData.getData('text/plain');

          // If there's HTML data and it's different from plain text (indicating rich content)
          if (htmlData && htmlData.trim() !== '' && isRichContent(htmlData, plainData)) {
            event.preventDefault(); // Prevent default paste behavior

            // Convert HTML to markdown
            var markdown = convert(htmlData);

            // Insert the markdown at the current cursor position
            insert(chatPrompt, markdown);
          }
          // If it's plain text or no HTML, let the default paste behavior handle it
        }
      });
    }
  });

  // Helper function to determine if content is rich (contains HTML formatting)
  var isRichContent = function (html, plain) {
    if (!html || html.trim() === '') return false;

    // Create a temporary element to parse the HTML
    var temp = document.createElement('div');
    temp.innerHTML = html;

    // Get the text content without HTML tags
    var textContent = temp.textContent || temp.innerText || '';

    // If the HTML contains actual formatting elements beyond just text, it's rich content
    // Check for common formatting tags
    var hasFormatting = /<(b|i|strong|em|u|strike|del|sup|sub|h[1-6]|p|br|div|span|a|ul|ol|li|blockquote|code|pre)[\s>]/i.test(html);

    // Also check if the text differs significantly (could indicate formatting was stripped)
    var textMatches = textContent.trim() === (plain || '').trim();

    return hasFormatting || !textMatches;
  };
})();
