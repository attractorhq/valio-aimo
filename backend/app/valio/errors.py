class IntegrationError(Exception):
    """Safe-to-display supplier integration failure; never include raw response bodies."""
