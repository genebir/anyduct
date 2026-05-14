"""Connector contract test suite.

New connectors must subclass the relevant ``_*Contract`` mixin in their own
test module — pytest will pick up the inherited test methods and run them
against the subclass's fixtures.

The mixin classes start with ``_`` so pytest does not collect them directly.
"""
