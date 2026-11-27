"""The glass design system (ui/theme.py): CSS that survives Streamlit's HTML sanitiser."""

from planetary_defense.ui import theme


def test_css_has_nothing_the_sanitiser_would_read_as_a_tag():
    # One "<" (even "st-key-<key>" in a comment) makes Streamlit drop the whole style block; ">" as a CSS
    # child combinator is fine. The starfield's SVG is URL-encoded inside url(), so it has no "<" either.
    assert "<" not in theme.CSS


def test_every_panel_is_styled_through_its_key():
    from pathlib import Path

    ui = Path(theme.__file__).parent
    sources = [p.read_text() for p in ui.rglob("*.py") if p.name != "theme.py"]
    bordered = sum(s.count("st.container(border=True") for s in sources)
    keyed = sum(s.count('st.container(border=True, key="panel_') for s in sources)
    assert bordered == keyed == 4
    assert '[class*="st-key-panel_"]' in theme.CSS


def test_the_starfield_is_deterministic():
    assert theme.starfield(100, 10, seed=1) == theme.starfield(100, 10, seed=1)
    assert theme.starfield(100, 10, seed=1) != theme.starfield(100, 10, seed=2)
