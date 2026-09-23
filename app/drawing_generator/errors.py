"""Drawing generator exceptions — mirrors app/cad_engine/errors.py's pattern."""


class DrawingValidationError(ValueError):
    """The supplied geometry doesn't meet the preconditions for drawing generation."""
