class AuthenticationError(Exception):
    """Raised when authentication credentials are missing, invalid, or expired."""


class AuthorizationError(Exception):
    """Raised when an authenticated principal attempts an unauthorized operation."""
