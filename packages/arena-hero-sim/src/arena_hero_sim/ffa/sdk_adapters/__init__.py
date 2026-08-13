"""Standalone third-party SDK-agent adapters.

Each adapter imports one public third-party agent module (available only inside
that repo's own venv) and exposes ``make_adapter() -> object`` plus
``adapter.run_turn(turn)``.  The subprocess runner imports these files by path so
it never triggers the heavy ``arena_hero_sim`` package ``__init__``.
"""
