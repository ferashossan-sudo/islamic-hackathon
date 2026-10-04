import pytest

from app.limits import LIMITER


@pytest.fixture(autouse=True)
def fresh_limiter():
    """Each test starts with empty rate-limit windows and an unused daily model budget."""
    LIMITER.windows.clear()
    LIMITER.llm_calls = 0
    yield
    LIMITER.windows.clear()
    LIMITER.llm_calls = 0
