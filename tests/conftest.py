import sys
from pathlib import Path

import pytest

# Permite importar el paquete sin instalarlo.
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from hd_scraper.config import settings  # noqa: E402
from hd_scraper.db.database import Database  # noqa: E402


@pytest.fixture()
def db():
    d = Database(":memory:")
    d.init_schema()
    yield d
    d.close()


@pytest.fixture(autouse=True)
def entorno_test(tmp_path):
    """Redirige el crudo a un tmp y elimina las esperas de rate limiting.

    ``settings`` es frozen; usamos object.__setattr__ para el override de test.
    """
    object.__setattr__(settings, "raw_dir", tmp_path / "raw")
    prev_interval = settings.min_interval_s
    object.__setattr__(settings, "min_interval_s", 0.0)
    yield
    object.__setattr__(settings, "min_interval_s", prev_interval)
