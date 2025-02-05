"""Test fixtures."""
import os
import pytest
from dotenv import load_dotenv
from pyicloud.services.reminders import RemindersService
from pyicloud.services.notes import NotesService
from pyicloud import PyiCloudService

# Load environment variables from .env file
load_dotenv()

@pytest.fixture
def icloud():
    """Get an authenticated iCloud service instance."""
    username = os.getenv("ICLOUD_USERNAME")
    password = os.getenv("ICLOUD_PASSWORD")
    
    if not username or not password:
        pytest.skip("iCloud credentials not found in environment variables")
    
    try:
        api = PyiCloudService(username, password)
        return api
    except Exception as e:
        pytest.skip(f"Failed to authenticate with iCloud: {e}")

@pytest.fixture
def reminders(icloud):
    """Get a reminders service instance."""
    return RemindersService(
        service_root=icloud.SETUP_ENDPOINT,
        session=icloud.session,
        params=icloud.params
    )

@pytest.fixture
def notes(icloud):
    """Get a notes service instance."""
    return icloud.notes 