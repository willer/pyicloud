import os
import pytest
from pyicloud import PyiCloudService

def test_drive_connection():
    username = os.environ.get("ICLOUD_USERNAME")
    password = os.environ.get("ICLOUD_PASSWORD")
    
    if not username or not password:
        pytest.skip("ICLOUD_USERNAME and ICLOUD_PASSWORD environment variables required")
    
    api = PyiCloudService(username, password)
    drive = api.drive
    
    # Basic test to verify connection
    assert drive.dir() is not None 