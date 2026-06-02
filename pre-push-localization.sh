#!/bin/bash
# Pre-push hook to update Django localization
# If localization generates new files, fail the push so user can commit them

set -e

cd django
# Run localization without translation API calls (just check for missing strings)
python manage.py load_app_localization

echo "" >&2

# Check for changes in translations.json only
if ! git diff --quiet locale/translation; then
    echo "❌ Localization updated translations.json. Commit it first:" >&2
    git --no-pager diff --stat locale/translation >&2
    exit 1
fi

echo "✅ No localization changes" >&2
exit 0
