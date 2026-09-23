"""
CAD engine exceptions.

Kept separate from interface.py and sections.py so both can import
them without a circular dependency. Every one of these is raised
deliberately, per this project's validation philosophy (see
app/validation/rules.py): fail loudly and specifically rather than
guessing or silently producing inaccurate geometry.
"""


class GeometryValidationError(ValueError):
    """A member or its matched section data doesn't meet the preconditions for geometry generation."""


class UnsupportedSectionFamilyError(GeometryValidationError):
    """The member's section family has no supported profile builder yet."""
