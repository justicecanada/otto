
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
        if (!node.nextSibling && !node.previousSibling) {
          return url;
        }
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
        content = content.replace(/^\s+/, '').replace(/\n+/gm, '\n    ');
        var parent = node.parentNode;

        // Default unordered list
        var prefix = '\n-   ';

        if (/ol/i.test(parent.nodeName)) {
          // Ordered list: ensure each item starts on its own line and has minimal spacing
          var index = Array.prototype.indexOf.call(parent.children, node) + parent.start;
          var base = index + '. ';
          while (base.length < 4) {
            base += ' ';
          }
          prefix = '\n' + base;
        }

        return prefix + content;
      }
    }
  ];

  // http://pandoc.org/README.html#smart-punctuation
  var escape = function (str) {
    return str.replace(/[\u2018\u2019\u00b4]/g, "'")
      .replace(/[\u201c\u201d\u2033]/g, '"')
      .replace(/[\u2212\u2022\u00b7\u25aa]/g, '\n-')
      .replace(/[\u2013\u2015]/g, '--')
      .replace(/\u2014/g, '---')
      .replace(/\u2026/g, '...')
      .replace(/[ ]+\n/g, '\n')
      // Remove backslashes that Turndown.js adds to escape markdown characters
      .replace(/\\([_*\[\]()~`>#+-=|{}\.!])/g, '$1')
      // Simplified line break handling - remove excessive backslashes
      .replace(/\\\n/g, '\n')
      .replace(/\n\n\n+/g, '\n\n')
      .replace(/[ ]+$/gm, '')
      .replace(/^\s+|\s+$/g, '');
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

  var removeBase64Images = function (html) {
    // TODO: image support - implement proper handling of images in messages
    // For now, remove base64 image data but keep alt text and other descriptive info
    html = html
      // Replace img tags with base64 data URIs with just the alt text or filename info
      .replace(/<img([^>]*?)src="data:image\/[^"]*"([^>]*?)>/gi, function (match, beforeSrc, afterSrc) {
        var altMatch = (beforeSrc + afterSrc).match(/alt="([^"]*)"/i);
        var titleMatch = (beforeSrc + afterSrc).match(/title="([^"]*)"/i);
        var altText = altMatch ? altMatch[1] : '';
        var titleText = titleMatch ? titleMatch[1] : '';

        // Create a descriptive placeholder
        var placeholder = '';
        if (altText) {
          placeholder = '[Image: ' + altText + ']';
        } else if (titleText) {
          placeholder = '[Image: ' + titleText + ']';
        } else {
          placeholder = '[Image]';
        }

        return placeholder;
      })
      // Remove any remaining standalone data URI references
      .replace(/data:image\/[^;\s)]+;base64,[A-Za-z0-9+\/=]+/g, '[Image data removed]')
      // Clean up any empty paragraphs or divs left behind
      .replace(/<(p|div)(\s[^>]*)?>[\s&nbsp;]*<\/\1>/gi, '')
      .trim();

    return html;
  };

  var normalizeInlineStyles = function (html) {
    if (!html || typeof document === 'undefined') {
      return html;
    }

    var container = document.createElement('div');
    container.innerHTML = html;

    // OneNote/Office often encode emphasis in CSS on span tags instead of semantic
    // tags (<strong>, <em>, etc.). Convert common style hints to semantic HTML first
    // so Turndown preserves formatting in markdown output.
    var spans = Array.prototype.slice.call(container.querySelectorAll('span'));
    for (var i = spans.length - 1; i >= 0; i--) {
      var span = spans[i];
      var style = (span.getAttribute('style') || '').toLowerCase();
      if (!style) {
        continue;
      }

      var hasBold = /font-weight\s*:\s*(bold|[7-9]00)/i.test(style);
      var hasItalic = /font-style\s*:\s*italic/i.test(style);
      var hasUnderline = /text-decoration(?:-line)?\s*:[^;]*underline/i.test(style);
      var hasStrike = /text-decoration(?:-line)?\s*:[^;]*line-through/i.test(style);

      if (!hasBold && !hasItalic && !hasUnderline && !hasStrike) {
        continue;
      }

      var inner = span.innerHTML;
      if (hasBold) {
        inner = '<strong>' + inner + '</strong>';
      }
      if (hasItalic) {
        inner = '<em>' + inner + '</em>';
      }
      if (hasUnderline) {
        inner = '<u>' + inner + '</u>';
      }
      if (hasStrike) {
        inner = '<del>' + inner + '</del>';
      }

      var temp = document.createElement('div');
      temp.innerHTML = inner;
      var fragment = document.createDocumentFragment();
      while (temp.firstChild) {
        fragment.appendChild(temp.firstChild);
      }
      span.parentNode.replaceChild(fragment, span);
    }

    return container.innerHTML;
  };

  var ELEMENT_NODE = 1;
  var TEXT_NODE = 3;

  var listPrefixRegex = /^[\s\u00A0]*((?:\(?\d+[\.\)]|\(?[A-Za-z][\.\)]|[•·▪◦●○■□◆◇▶\-\u2022\u25CF\u25A0\u25AA\u25AB\u25BA\u25C6\u25C7]))[\s\u00A0]*/;

  var normalizeWordLists = function (html) {
    if (!html || typeof document === 'undefined') {
      return html;
    }

    var container = document.createElement('div');
    container.innerHTML = html;

    transformWordListParagraphs(container);

    return container.innerHTML;
  };

  var transformWordListParagraphs = function (root) {
    var paragraphs = Array.prototype.slice.call(root.querySelectorAll('p'));

    paragraphs.forEach(function (paragraph) {
      if (!paragraph.parentNode || !isWordListParagraph(paragraph)) {
        return;
      }

      var listTag = detectListType(paragraph) === 'ol' ? 'ol' : 'ul';
      var listSignature = getListSignature(paragraph, listTag);
      var listElement = findSiblingList(paragraph, listTag, listSignature);

      if (!listElement) {
        listElement = document.createElement(listTag);
        listElement.setAttribute('data-word-list', 'true');
        listElement.setAttribute('data-word-list-signature', listSignature);
        paragraph.parentNode.insertBefore(listElement, paragraph);
      }

      var listItem = document.createElement('li');
      listItem.innerHTML = extractListContent(paragraph);
      listElement.appendChild(listItem);

      paragraph.remove();
    });

    Array.prototype.slice.call(root.querySelectorAll('[data-word-list]')).forEach(function (list) {
      list.removeAttribute('data-word-list');
      list.removeAttribute('data-word-list-signature');
    });
  };

  var findSiblingList = function (paragraph, tagName, signature) {
    var previous = paragraph.previousSibling;

    while (previous && previous.nodeType === TEXT_NODE && previous.textContent.trim() === '') {
      var toRemove = previous;
      previous = previous.previousSibling;
      toRemove.remove();
    }

    if (previous && previous.nodeType === ELEMENT_NODE && previous.tagName.toLowerCase() === tagName && previous.getAttribute('data-word-list') === 'true' && previous.getAttribute('data-word-list-signature') === signature) {
      return previous;
    }

    return null;
  };

  var isWordListParagraph = function (node) {
    if (!node || node.nodeName !== 'P') {
      return false;
    }

    var className = node.className || '';
    var style = node.getAttribute('style') || '';
    if (/MsoListParagraph/i.test(className) || /mso-list:/i.test(style)) {
      return true;
    }

    var text = (node.textContent || '').trim();
    return /^[•·▪◦●○■□◆◇▶\-\u2022\u25CF\u25A0\u25AA\u25AB\u25BA\u25C6\u25C7]/.test(text) || /^\(?\d+[\.\)]/.test(text) || /^\(?[A-Za-z][\.\)]/.test(text);
  };

  var detectListType = function (paragraph) {
    var text = (paragraph.textContent || '').trim();
    if (/^\(?[0-9]+[\.\)]/.test(text) || /^\(?[A-Za-z][\.\)]/.test(text)) {
      return 'ol';
    }
    return 'ul';
  };

  var getListSignature = function (paragraph, tagName) {
    var text = (paragraph.textContent || '').trim();

    if (tagName === 'ol') {
      if (/^\(?[A-Z][\.\)]/.test(text)) {
        return tagName + '|upper-alpha';
      }
      if (/^\(?[a-z][\.\)]/.test(text)) {
        return tagName + '|lower-alpha';
      }
      return tagName + '|decimal';
    }

    var prefixMatch = text.match(listPrefixRegex);
    var marker = prefixMatch ? prefixMatch[1] : '';
    marker = marker.replace(/[\s\u00A0]/g, '');
    return tagName + '|' + marker;
  };

  var extractListContent = function (paragraph) {
    var clone = paragraph.cloneNode(true);

    Array.prototype.slice.call(clone.querySelectorAll('span')).forEach(function (span) {
      var style = span.getAttribute('style') || '';
      var text = span.textContent || '';
      if (/mso-list:\s*Ignore/i.test(style) || /font-family:\s*(Symbol|Wingdings)/i.test(style) || /^[\s\u00A0]*[•·▪◦●○■□◆◇▶\-\u2022\u25CF\u25A0\u25AA\u25AB\u25BA\u25C6\u25C7]/.test(text)) {
        span.remove();
      }
    });

    removeListPrefix(clone);

    return clone.innerHTML.trim();
  };

  var removeListPrefix = function (element) {
    var textContent = element.textContent || '';
    var prefixMatch = textContent.match(listPrefixRegex);

    if (!prefixMatch) {
      return;
    }

    var charsToRemove = prefixMatch[0].length;

    trimLeadingCharacters(element, charsToRemove);
  };

  var trimLeadingCharacters = function (node, remaining) {
    if (!node || remaining <= 0) {
      return remaining;
    }

    var child = node.firstChild;
    while (child && remaining > 0) {
      var nextSibling = child.nextSibling;

      if (child.nodeType === TEXT_NODE) {
        var text = child.textContent;
        if (!text) {
          child.remove();
          child = nextSibling;
          continue;
        }

        if (text.length <= remaining) {
          remaining -= text.length;
          child.remove();
        } else {
          child.textContent = text.substring(remaining);
          remaining = 0;
        }
      } else if (child.nodeType === ELEMENT_NODE) {
        remaining = trimLeadingCharacters(child, remaining);
        if (!child.textContent || child.textContent.trim() === '') {
          child.remove();
        }
      }

      child = nextSibling;
    }

    return remaining;
  };

  var convert = function (str) {
    // Clean the HTML first to remove Microsoft Word cruft
    str = cleanHtml(str);

    // Convert style-based inline emphasis (common in OneNote/Office HTML)
    // to semantic tags so markdown conversion can preserve formatting.
    str = normalizeInlineStyles(str);

    // Remove base64 images before conversion
    str = removeBase64Images(str);

    // Normalize paragraphs representing Word lists into semantic lists
    str = normalizeWordLists(str);

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
        if (!clipboardData) return;

        var hasFiles = clipboardData.files && clipboardData.files.length > 0;
        var mdData = clipboardData.getData('text/markdown');
        var htmlData = clipboardData.getData('text/html');
        var plainData = clipboardData.getData('text/plain');

        // 1. Non-image files (e.g. .docx, .pdf) always take priority
        if (hasFiles) {
          var onlyImages = true;
          for (var i = 0; i < clipboardData.files.length; i++) {
            if (!clipboardData.files[i].type.startsWith('image/')) {
              onlyImages = false;
              break;
            }
          }
          if (!onlyImages) {
            var fileInput = document.querySelector('#id_chat-input_file');
            if (fileInput) {
              event.preventDefault();
              fileInput.files = clipboardData.files;
              fileInput.dispatchEvent(new Event('change'));
              return;
            }
          }
        }

        // 2. Prefer native Markdown if provided by the source app
        if (mdData && mdData.trim() !== '') {
          event.preventDefault();
          insert(chatPrompt, mdData);
          return;
        }

        // Detect Microsoft Office HTML (Word, OneNote, Outlook, Excel)
        var isOfficeHtml = htmlData && /class="?Mso|urn:schemas-microsoft-com:office|xmlns:o=/.test(htmlData);

        // 3. If what's on the clipboard looks like code (skip for Office HTML), prefer plain text
        if (!isOfficeHtml && htmlData && htmlData.trim() !== '' && isCodeLike(htmlData, plainData)) {
          event.preventDefault();
          insert(chatPrompt, plainData || '');
          return;
        }

        // 4. Rich HTML content — convert to markdown
        // When clipboard has both images and HTML (e.g. OneNote, Outlook), always
        // prefer the HTML — the image is a visual fallback, not the intended content.
        if (htmlData && htmlData.trim() !== '' && (isOfficeHtml || hasFiles || isRichContent(htmlData, plainData))) {
          event.preventDefault();
          var markdown = convert(htmlData);
          insert(chatPrompt, markdown);
          return;
        }

        // 5. Plain text — let the default paste behavior handle it
        if (plainData && plainData.trim() !== '') {
          return;
        }

        // 6. Image-only clipboard (e.g. screenshot, snipping tool) — upload as file
        if (hasFiles) {
          var fileInput = document.querySelector('#id_chat-input_file');
          if (fileInput) {
            event.preventDefault();
            fileInput.files = clipboardData.files;
            fileInput.dispatchEvent(new Event('change'));
            return;
          }
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

  // Heuristic to detect "code-like" clipboard content
  // Prefer plain-text paste to avoid extra blank lines or altered spacing
  var isCodeLike = function (html, plain) {
    if (!html) return false;

    var lower = html.toLowerCase();
    // Explicit code blocks or styling that preserves whitespace
    // Note: match white-space:pre but NOT pre-wrap or pre-line (used by Word/OneNote)
    if (/<pre[\s>]/.test(lower) || /<code[\s>]/.test(lower) || /white-space\s*:\s*pre(?!\s*[-a-z])/.test(lower)) {
      return true;
    }

    // Large proportion of lines starting with common code indentation or symbols (heuristic)
    var temp = document.createElement('div');
    temp.innerHTML = html;
    var text = (temp.textContent || '').replace(/\r\n?/g, '\n');
    var lines = text.split('\n').filter(function (l) {return l.trim() !== '';});
    if (lines.length >= 3) {
      var codey = 0;
      for (var i = 0; i < lines.length; i++) {
        var l = lines[i];
        if (/^\s{2,}\S/.test(l) || /[{};<>]=?$/.test(l) || /\b(function|def|class|if|for|while|return|import|from|const|let|var)\b/.test(l)) {
          codey++;
        }
      }
      if (codey / lines.length >= 0.5) {
        return true;
      }
    }

    return false;
  };
})();
