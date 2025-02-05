"""Test fixtures."""
import pytest
from pyicloud.services.reminders import RemindersService
from pyicloud.services.notes import NotesService

@pytest.fixture
def reminders():
    """Get a reminders service instance."""
    return RemindersService()

@pytest.fixture
def notes():
    """Get a notes service instance."""
    return NotesService() 