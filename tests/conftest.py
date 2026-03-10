"""Global test fixtures.

Prevents leaked EventBus subscriptions (e.g. AlertBroker from scheduler tests)
from triggering real Telegram alerts in unrelated tests.
"""

import pytest
from core.events import event_bus


@pytest.fixture(autouse=True)
def _clean_global_event_bus():
    """Clear global event_bus after every test to prevent cross-test leaks."""
    yield
    event_bus.clear()
