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
        lists = reminders.lists()
        assert lists is not None, "Reminder lists is None"
        print(f"Found {len(lists)} reminder lists")
        
        for lst in lists:
            print(f"List: {lst.title}")
    except Exception as e:
        pytest.fail(f"Failed to get reminder lists: {str(e)}")
    
    # Test accessing reminders in first list
    if lists:
        first_list = lists[0]
        try:
            print(f"Accessing reminders in list: {first_list.title}")
            reminders_in_list = first_list.reminders()
            assert reminders_in_list is not None, "Reminders in list is None"
            
            # Print some details about the reminders
            for reminder in reminders_in_list:
                print(f"Reminder: {reminder.title}")
                print(f"  Completed: {reminder.completed}")
                print(f"  Due Date: {reminder.due_date}")
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
    lists = reminders.lists()
    
    if not lists:
        pytest.skip("No reminder lists available")
    
    try:
        # Try to create a test reminder
        test_list = lists[0]
        new_reminder = test_list.create_reminder(
            "PyiCloud Test Reminder",
            description="This is a test reminder created by PyiCloud"
        )
        assert new_reminder is not None, "Failed to create reminder"
        print(f"Created reminder: {new_reminder.title}")
        
        # Clean up - delete the test reminder
        new_reminder.delete()
    except Exception as e:
        pytest.fail(f"Failed to create/delete reminder: {str(e)}") 