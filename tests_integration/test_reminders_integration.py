import os
import pytest
from pyicloud import PyiCloudService
import logging
from datetime import datetime, timedelta
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

def test_reminder_lifecycle():
    """Test creating, updating, and completing a reminder"""
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
        # Create a test reminder
        first_list_title = next(iter(lists.keys()))
        test_title = "PyiCloud Test Reminder Lifecycle"
        test_desc = "This is a test reminder created by PyiCloud"
        due_date = datetime.now() + timedelta(days=1)
        
        guid = reminders.post(
            test_title,
            description=test_desc,
            collection=first_list_title,
            due_date=due_date
        )
        assert guid is not None, "Failed to create reminder"
        print(f"Created reminder with GUID: {guid}")
        
        # Verify the reminder was created
        reminder = reminders.get_reminder(guid)
        assert reminder is not None, "Could not find newly created reminder"
        assert reminder["title"] == test_title, "Title does not match"
        assert reminder["desc"] == test_desc, "Description does not match"
        assert reminder["due"].date() == due_date.date(), "Due date does not match"
        
        # Update the reminder
        new_title = "Updated Test Reminder"
        new_desc = "This reminder has been updated"
        new_due_date = datetime.now() + timedelta(days=2)
        
        success = reminders.update(
            guid,
            title=new_title,
            description=new_desc,
            due_date=new_due_date
        )
        assert success, "Failed to update reminder"
        
        # Find the updated reminder by title
        found_updated = False
        for title, lst in reminders.lists.items():
            for reminder in lst:
                if reminder["title"] == new_title:
                    assert reminder["desc"] == new_desc, "Updated description does not match"
                    assert reminder["due"].date() == new_due_date.date(), "Updated due date does not match"
                    found_updated = True
                    guid = reminder["guid"]  # Update GUID to the new reminder
                    break
            if found_updated:
                break
        assert found_updated, "Could not find updated reminder"
        
        # Complete the reminder
        success = reminders.complete(guid)
        assert success, "Failed to complete reminder"
        
        # Find the completed reminder by title
        found_completed = False
        for title, lst in reminders.lists.items():
            for reminder in lst:
                if reminder["title"] == new_title and reminder["completed"]:
                    found_completed = True
                    break
            if found_completed:
                break
        assert found_completed, "Could not find completed reminder"
        
    except Exception as e:
        pytest.fail(f"Failed during reminder lifecycle test: {str(e)}")

def test_reminder_creation():
    """Test basic reminder creation"""
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
        guid = reminders.post(
            "PyiCloud Test Reminder",
            description="This is a test reminder created by PyiCloud",
            collection=first_list_title
        )
        assert guid is not None, "Failed to create reminder"
        print(f"Created reminder in list: {first_list_title}")
        
        # Verify the reminder was created
        reminder = reminders.get_reminder(guid)
        assert reminder is not None, "Could not find newly created reminder"
        assert reminder["title"] == "PyiCloud Test Reminder", "Title does not match"
    except Exception as e:
        pytest.fail(f"Failed to create/verify reminder: {str(e)}") 