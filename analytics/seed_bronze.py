from pathlib import Path

def seed_if_missing_or_empty(bronze_path: Path, seed_path: Path) -> bool:
    """
    If bronze_path is missing or empty, seed it from seed_path.
    Returns True if we seeded, False otherwise.
    """
    try:
        if bronze_path.exists() and bronze_path.stat().st_size > 0:
            return False

        bronze_path.parent.mkdir(parents=True, exist_ok=True)

        seed = seed_path.read_text(encoding="utf-8")
        bronze_path.write_text(seed, encoding="utf-8")
        return True
    except Exception:
        # Seeding should never crash the healer
        return False