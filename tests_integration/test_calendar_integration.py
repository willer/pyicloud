import os
import pytest
from pyicloud import PyiCloudService

def test_calendar_service():
    username = os.environ.get("ICLOUD_USERNAME")
    password = os.environ.get("ICLOUD_PASSWORD")
    
    if not username or not password:
        pytest.skip("ICLOUD_USERNAME and ICLOUD_PASSWORD environment variables required")
    
    api = PyiCloudService(username, password)
    
    # Test calendar access - this might fail based on your experience
    calendars = api.calendar.events()
    assert calendars is not None 