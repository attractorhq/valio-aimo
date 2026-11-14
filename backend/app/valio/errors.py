class IntegrationError(Exception):
    """Safe-to-display supplier integration failure; never include raw response bodies."""


class ValioRateLimited(IntegrationError):
    """A shared supplier cooldown, not permission to retry or switch to recordings."""

    code = "VALIO_RATE_LIMITED"

    def __init__(self, retry_after: int):
        self.retry_after = retry_after
        super().__init__(f"Valio API rate limit: retry in {retry_after} s")
