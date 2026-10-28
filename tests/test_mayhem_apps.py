from pp_dashboard.mayhem.apps import resolve_featured_short_name


def test_resolve_looking_glass_alias() -> None:
    shorts = {"lookingglass", "jammer", "replay"}
    assert resolve_featured_short_name("looking_glass", shorts) == "lookingglass"
    assert resolve_featured_short_name("jammer", shorts) == "jammer"
