from pathlib import Path


def test_keywords_file_exists_and_loads() -> None:
    path = Path("monitor/keywords.txt")
    assert path.exists()
    lines = [line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert "Bitcoin" in lines
    assert "Ethereum" in lines
