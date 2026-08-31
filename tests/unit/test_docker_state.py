from qualock.run.docker import parse_nul_paths


def test_parse_nul_paths_deduplicates_and_skips_empty_entries() -> None:
    assert parse_nul_paths("src/a.py\0tests/x.py\0src/a.py\0\0") == ("src/a.py", "tests/x.py")
