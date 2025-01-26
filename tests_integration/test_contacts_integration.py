import os
import pytest
from pyicloud import PyiCloudService

def test_contacts_service():
    username = os.environ.get("ICLOUD_USERNAME")
    password = os.environ.get("ICLOUD_PASSWORD")
    
    if not username or not password:
        pytest.skip("ICLOUD_USERNAME and ICLOUD_PASSWORD environment variables required")
    
    api = PyiCloudService(username, password)
    
    # Test contacts access - this should work based on your experience
    contacts = api.contacts.all()
    assert contacts is not None 