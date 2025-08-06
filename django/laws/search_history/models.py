from django.conf import settings
from django.db import models
from django.utils import timezone


class LawSearch(models.Model):
    """
    Stores a user's search query and parameters for the laws search feature.
    Results are not stored to save space - only the AI answer if generated.
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="law_searches"
    )

    # Basic search info
    query = models.TextField()
    created_at = models.DateTimeField(default=timezone.now)

    # Form parameters stored as JSON
    search_parameters = models.JSONField(default=dict)

    # AI answer if generated (to save LLM costs on repeat access)
    ai_answer = models.TextField(blank=True, null=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["user", "-created_at"]),
        ]

    def __str__(self):
        return f"{self.user.username}: {self.query[:50]}..."

    @property
    def is_advanced_search(self):
        """Check if this was an advanced search based on parameters."""
        return self.search_parameters.get("advanced", False)

    def get_form_data(self):
        """Return form data suitable for repopulating the search form."""
        return {"query": self.query, **self.search_parameters}
