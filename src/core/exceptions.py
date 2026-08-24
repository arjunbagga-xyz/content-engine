class ContentEngineError(Exception):
    """Base exception for the AI Content Engine."""
    pass

class APIKeyError(ContentEngineError):
    """Exception raised when an API key is missing, invalid, or rate limited."""
    pass

class GenerationError(ContentEngineError):
    """Exception raised during planning, scripting, image, or video generation."""
    pass

class QAValidationError(GenerationError):
    """Exception raised when a post fails QA gating after max retries."""
    pass

class PublishingError(ContentEngineError):
    """Exception raised during the publishing phase (Instagram, X)."""
    pass

class SessionError(PublishingError):
    """Exception raised when a platform session (login, cookies) fails."""
    pass

class ChallengeError(PublishingError):
    """Exception raised when a platform issues a verification checkpoint/challenge."""
    pass
