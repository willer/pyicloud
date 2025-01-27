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

def test_reminders_service():
    username = os.environ.get("ICLOUD_USERNAME")
    password = os.environ.get("ICLOUD_PASSWORD")
    
    if not username or not password:
        pytest.skip("ICLOUD_USERNAME and ICLOUD_PASSWORD environment variables required")
    
    # Test initial authentication
    try:
        api = PyiCloudService(username, password)
        
        # Handle 2FA if needed
        if handle_2fa(api):
            print("2FA completed successfully")
        
    except Exception as e:
        pytest.fail(f"Failed to authenticate: {str(e)}")
    
    # Test reminders service initialization
    try:
        reminders = api.reminders
        assert reminders is not None, "Reminders service is None"
    except Exception as e:
        pytest.fail(f"Failed to initialize reminders service: {str(e)}")
    
    # Test listing reminder lists
    try:
        lists = reminders.lists
        assert lists is not None, "Reminder lists is None"
        print(f"Found {len(lists)} reminder lists")
        
        for title, lst in lists.items():
            print(f"List: {title}")
            for reminder in lst:
                print(f"  - {reminder['title']}")
    except Exception as e:
        pytest.fail(f"Failed to get reminder lists: {str(e)}")
    
    # Test accessing reminders in first list
    if lists:
        first_list_title = next(iter(lists.keys()))
        first_list = lists[first_list_title]
        try:
            print(f"Accessing reminders in list: {first_list_title}")
            assert first_list is not None, "Reminders in list is None"
            
            # Print some details about the reminders
            for reminder in first_list:
                print(f"Reminder: {reminder['title']}")
                print(f"  Description: {reminder.get('desc', 'No description')}")
                print(f"  Due Date: {reminder.get('due', 'No due date')}")
        except Exception as e:
            pytest.fail(f"Failed to access reminders in list: {str(e)}")

def test_reminder_creation():
    """Only run this if basic access works"""
    username = os.environ.get("ICLOUD_USERNAME")
    password = os.environ.get("ICLOUD_PASSWORD")
    
    if not username or not password:
        pytest.skip("ICLOUD_USERNAME and ICLOUD_PASSWORD environment variables required")
    
    api = PyiCloudService(username, password)
    reminders = api.reminders
    lists = reminders.lists
    
    if not lists:
        pytest.skip("No reminder lists available")
    
    try:
        # Try to create a test reminder
        first_list_title = next(iter(lists.keys()))
        success = reminders.post(
            "PyiCloud Test Reminder",
            description="This is a test reminder created by PyiCloud",
            collection=first_list_title
        )
        assert success, "Failed to create reminder"
        print(f"Created reminder in list: {first_list_title}")
        
        # Refresh to see the new reminder
        reminders.refresh()
        
        # Verify the reminder was created
        new_list = reminders.lists[first_list_title]
        found = False
        for reminder in new_list:
            if reminder['title'] == "PyiCloud Test Reminder":
                found = True
                break
        assert found, "Could not find newly created reminder"
    except Exception as e:
        pytest.fail(f"Failed to create/verify reminder: {str(e)}") 