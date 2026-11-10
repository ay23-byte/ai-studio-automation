"""MuAPI is intentionally unavailable under the project's zero-paid policy."""
from pathlib import Path


def generate_muapi_video(prompt: str, path: str | Path) -> bool:
    """Never call MuAPI: it is a metered hosted service and is prohibited."""
    print("MuAPI disabled permanently by the zero-paid project policy.")
    return False
