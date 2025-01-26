import os
import pytest
from pyicloud import PyiCloudService
import logging
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# Set up logging to see detailed API interactions
logging.basicConfig(level=logging.DEBUG)

def handle_2fa(api):
    """Handle 2FA verification if needed"""
    if api.requires_2fa:
        print("Two-factor authentication required.")
        code = input("Enter the code you received of one of your approved devices: ")
        result = api.validate_2fa_code(code)
        print("2FA validation result: %s" % result)

        # Trust this device not to ask for 2FA again
        if result:
            print("Trusting device...")
            api.trust_session()
            return True
    return False

def test_contacts_service():
    username = os.environ.get("ICLOUD_USERNAME")
    password = os.environ.get("ICLOUD_PASSWORD")
    
    if not username or not password:
        pytest.skip("ICLOUD_USERNAME and ICLOUD_PASSWORD environment variables required")
    
    try:
        api = PyiCloudService(username, password)
        
        # Handle 2FA if needed
        if handle_2fa(api):
            print("2FA completed successfully")
            
    except Exception as e:
        pytest.fail(f"Failed to authenticate: {str(e)}")
    
    # Test contacts access - this should work based on your experience
    try:
        contacts = api.contacts.all()
        assert contacts is not None, "Contacts list is None"
        print(f"Found {len(contacts)} contacts")
    except Exception as e:
        pytest.fail(f"Failed to access contacts: {str(e)}") 