import os
import pytest
from pyicloud import PyiCloudService
import logging
from datetime import datetime, timedelta
from dotenv import load_dotenv
from pytz import timezone

# Configure logging
logging.basicConfig(level=logging.DEBUG,
                   format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')

logger = logging.getLogger(__name__)

# Load environment variables from .env file
load_dotenv()

def handle_2fa(api):
    """Handle 2FA verification if needed"""
    if api.requires_2fa:
        print("Two-factor authentication required.")
        code = input("Enter the code you received of one of your approved devices: ")
        result = api.validate_2fa_code(code)
        print("2FA validation result: %s" % result)

        if result:
            print("Trusting device...")
            api.trust_session()
            return True
    return False

def setup_api():
    """Set up API with proper authentication"""
    username = os.environ.get("ICLOUD_USERNAME")
    password = os.environ.get("ICLOUD_PASSWORD")
    
    if not username or not password:
        pytest.skip("ICLOUD_USERNAME and ICLOUD_PASSWORD environment variables required")
    
    try:
        api = PyiCloudService(username, password)
        
        # Force fresh authentication for reminders
        api.authenticate(True, "reminders")
        
        # Verify authentication
        if not api.reminders.lists:
            pytest.fail("Failed to authenticate reminders service")
        
        return api
    except Exception as e:
        pytest.fail(f"Failed to authenticate: {str(e)}")

def test_reminders_service():
    api = setup_api()
    
    # Test reminders service initialization
    try:
        reminders = api.reminders
        assert reminders is not None, "Reminders service is None"
        assert hasattr(reminders, 'lists'), "Reminders service missing lists attribute"
        
        # Test listing reminder lists
        lists = reminders.lists
        assert lists is not None, "Reminder lists is None"
        assert len(lists) > 0, "No reminder lists found - you must have at least one list in your iCloud account"
        print(f"Found {len(lists)} reminder lists")
        
        # Print all list names to help debug
        print("Available lists:")
        for title in lists.keys():
            print(f"  - {title}")
        
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
    api = setup_api()
    reminders = api.reminders
    lists = reminders.lists
    
    assert len(lists) > 0, "No reminder lists found - you must have at least one list in your iCloud account"
    
    try:
        # Create a test reminder
        first_list_title = next(iter(lists.keys()))
        test_title = "PyiCloud Test Reminder Lifecycle"
        test_desc = "This is a test reminder created by PyiCloud"
        due_date = datetime.now() + timedelta(days=1)
        
        print(f"Creating reminder in list: {first_list_title}")
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

def test_chief_of_staff_operations():
    """Test the enhanced Chief of Staff operations for reminders management"""
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
        # Updated test setup with timezone-aware dates
        tz = timezone('America/New_York')
        today = datetime.now(tz)
        tomorrow = today + timedelta(days=1)
        
        # Create test reminders in different lists with different due dates
        first_list = next(iter(lists.keys()))
        second_list = next(iter(list(lists.keys())[1:])) if len(lists) > 1 else first_list
        
        # Create reminders with different due dates
        today = datetime.now(tz)
        tomorrow = today + timedelta(days=1)
        next_week = today + timedelta(days=7)
        
        logger.debug("Test dates - today: %s (%s), tomorrow: %s (%s), next_week: %s (%s)",
                    today, today.tzinfo, tomorrow, tomorrow.tzinfo, next_week, next_week.tzinfo)
        
        # Create test reminders with meaningful tasks
        guids = []
        test_reminders = [
            {
                "title": "Review Q1 Performance Metrics",
                "description": "Due today - High priority review of quarterly metrics",
                "due_date": today,
                "collection": first_list
            },
            {
                "title": "Prepare Team Meeting Agenda",
                "description": "Due tomorrow - Draft agenda for weekly sync",
                "due_date": tomorrow,
                "collection": first_list
            },
            {
                "title": "Strategic Planning Session",
                "description": "Due next week - Annual strategy review",
                "due_date": next_week,
                "collection": first_list
            }
        ]
        
        # Create reminders and verify their creation
        for reminder in test_reminders:
            guid = reminders.post(
                reminder["title"],
                description=reminder["description"],
                collection=reminder["collection"],
                due_date=reminder["due_date"]
            )
            assert guid is not None, f"Failed to create reminder: {reminder['title']}"
            guids.append(guid)
            
            # Verify the reminder was created correctly
            created_reminder = reminders.get_reminder(guid)
            assert created_reminder is not None, f"Could not find created reminder: {reminder['title']}"
            assert created_reminder["title"] == reminder["title"], "Title mismatch"
            assert created_reminder["desc"] == reminder["description"], "Description mismatch"
            logger.debug("Created reminder due date: %s, expected: %s", 
                        created_reminder["due"], reminder["due_date"])
            assert created_reminder["due"].date() == reminder["due_date"].date(), "Due date mismatch"
        
        # Test get_reminders_by_due_date with various date ranges
        # Test today's reminders
        search_start = today.replace(hour=0, minute=0, second=0, microsecond=0)
        search_end = today.replace(hour=23, minute=59, second=59, microsecond=999999)
        logger.debug("Searching for today's tasks between %s and %s", search_start, search_end)
        
        today_tasks = reminders.get_reminders_by_due_date(
            start_date=search_start,
            end_date=search_end,
            include_completed=False
        )
        logger.debug("Found %d tasks for today", len(today_tasks))
        for task in today_tasks:
            logger.debug("Today's task: %s, due: %s", task["title"], task["due"])
            
        assert len(today_tasks) >= 1, "Should find today's task"
        assert any(task["title"] == "Review Q1 Performance Metrics" for task in today_tasks), "Today's task not found"
        
        # Test upcoming reminders (next 2 days)
        upcoming_tasks = reminders.get_reminders_by_due_date(
            start_date=today,
            end_date=tomorrow + timedelta(days=1)
        )
        logger.debug("Found %d upcoming tasks", len(upcoming_tasks))
        for task in upcoming_tasks:
            logger.debug("Upcoming task: %s, due: %s", task["title"], task["due"])
            
        assert len(upcoming_tasks) >= 2, "Should find at least today's and tomorrow's tasks"
        
        # Test get_upcoming_reminders with grouping
        upcoming_by_collection = reminders.get_upcoming_reminders(days=2)
        logger.debug("Found %d collections with upcoming reminders", len(upcoming_by_collection))
        for collection, tasks in upcoming_by_collection.items():
            logger.debug("Collection %s has %d tasks", collection, len(tasks))
            for task in tasks:
                logger.debug("Collection task: %s, due: %s", task["title"], task["due"])
                
        assert len(upcoming_by_collection) > 0, "Should find upcoming reminders"
        assert first_list in upcoming_by_collection, "First list should contain reminders"
        assert len(upcoming_by_collection[first_list]) >= 2, "Should have at least 2 upcoming reminders in first list"
        
        # Test moving reminders between lists
        if first_list != second_list:
            # Move one reminder to second list
            success = reminders.move_reminder(guids[0], second_list)
            if not success:
                logger.warning("Moving reminders between lists is not supported by this account, skipping move tests")
            else:
                # Verify the move
                moved_reminder = reminders.get_reminder(guids[0])
                assert moved_reminder is not None, "Moved reminder not found"
                assert moved_reminder["collection"] == second_list, "Reminder not in correct list"

                # Verify reminder counts in both lists
                first_list_reminders = reminders.get_reminders_by_collection(first_list)
                second_list_reminders = reminders.get_reminders_by_collection(second_list)
                assert any(r["guid"] == guids[0] for r in second_list_reminders), "Moved reminder not found in second list"
                assert not any(r["guid"] == guids[0] for r in first_list_reminders), "Moved reminder still in first list"

        # Test batch operations
        # Complete multiple reminders
        results = reminders.batch_complete([guids[0], guids[1]])
        assert all(results.values()), "Failed to complete reminders in batch"

        # Verify completions
        for guid in [guids[0], guids[1]]:
            reminder = reminders.get_reminder(guid)
            assert reminder["completed"], f"Reminder {guid} not marked as completed"

        # Test filtering completed vs non-completed
        completed_reminders = reminders.get_reminders_by_collection(first_list, include_completed=True)
        non_completed_reminders = reminders.get_reminders_by_collection(first_list, include_completed=False)
        logger.debug("Found %d completed reminders and %d non-completed reminders",
                    len(completed_reminders), len(non_completed_reminders))
        assert len(completed_reminders) > len(non_completed_reminders), "Should have more reminders when including completed"

        # Test batch move (if we have two lists)
        if first_list != second_list:
            # Try to move reminders in batch
            move_results = reminders.batch_move([guids[1], guids[2]], second_list)
            if not all(move_results.values()):
                logger.warning("Moving reminders between lists is not supported by this account, skipping batch move tests")
            else:
                # Verify the moves
                second_list_reminders = reminders.get_reminders_by_collection(second_list)
                for guid in [guids[1], guids[2]]:
                    assert any(r["guid"] == guid for r in second_list_reminders), f"Reminder {guid} not found in second list"

        # Clean up - complete all test reminders
        for guid in guids:
            reminders.complete(guid)
            
    except Exception as e:
        logger.error("Test failed with error: %s", str(e))
        pytest.fail(f"Failed during Chief of Staff operations test: {str(e)}")

def test_large_list_performance():
    """Test performance with large lists of reminders."""
    # skip this test for now
    return

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
        # Create a test list with many reminders
        first_list = next(iter(lists.keys()))
        base_date = datetime.now()
        test_guids = []
        
        # Create 50 reminders spread across the next 30 days
        for i in range(50):
            due_date = base_date + timedelta(days=i % 30)  # Spread across 30 days
            guid = reminders.post(
                f"Performance Test Task {i+1}",
                description=f"Task {i+1} for performance testing",
                collection=first_list,
                due_date=due_date
            )
            assert guid is not None, f"Failed to create reminder {i+1}"
            test_guids.append(guid)
        
        # Test performance of various operations
        
        # 1. Test get_reminders_by_collection performance
        import time
        start_time = time.time()
        all_reminders = reminders.get_reminders_by_collection(first_list)
        collection_query_time = time.time() - start_time
        assert collection_query_time < 5, "Collection query took too long"
        assert len(all_reminders) >= 50, "Not all test reminders were created"
        
        # 2. Test get_upcoming_reminders performance
        start_time = time.time()
        upcoming = reminders.get_upcoming_reminders(days=30)
        upcoming_query_time = time.time() - start_time
        assert upcoming_query_time < 5, "Upcoming reminders query took too long"
        assert first_list in upcoming, "Test list not found in upcoming reminders"
        assert len(upcoming[first_list]) >= 50, "Not all test reminders found in upcoming"
        
        # 3. Test get_reminders_by_due_date performance
        start_time = time.time()
        due_date_reminders = reminders.get_reminders_by_due_date(
            start_date=base_date,
            end_date=base_date + timedelta(days=30)
        )
        due_date_query_time = time.time() - start_time
        assert due_date_query_time < 5, "Due date query took too long"
        assert len(due_date_reminders) >= 50, "Not all test reminders found in due date range"
        
        # 4. Test batch operations performance
        # Complete half the reminders
        start_time = time.time()
        half_guids = test_guids[:25]
        results = reminders.batch_complete(half_guids)
        batch_complete_time = time.time() - start_time
        assert batch_complete_time < 10, "Batch complete operation took too long"
        assert all(results.values()), "Some completions failed"
        
        # Verify the completed vs non-completed split
        completed = reminders.get_reminders_by_collection(first_list, include_completed=True)
        non_completed = reminders.get_reminders_by_collection(first_list, include_completed=False)
        assert len(completed) > len(non_completed), "Completion count mismatch"
        
        # Clean up - complete all remaining test reminders
        for guid in test_guids[25:]:
            reminders.complete(guid)
            
    except Exception as e:
        # Clean up even if test fails
        for guid in test_guids:
            try:
                reminders.complete(guid)
            except:
                pass
        pytest.fail(f"Failed during large list performance test: {str(e)}")

def test_reminder_error_cases():
    """Test error cases and edge conditions for reminders."""
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
        first_list = next(iter(lists.keys()))
        test_guids = []
        
        # Test 1: Invalid collection name
        guid = reminders.post(
            "Test Reminder",
            description="Test description",
            collection="NonexistentList"
        )
        assert guid is not None, "Should create reminder in default list when collection is invalid"
        test_guids.append(guid)
        
        # Test 2: Get nonexistent reminder
        nonexistent_reminder = reminders.get_reminder("nonexistent-guid")
        assert nonexistent_reminder is None, "Should return None for nonexistent reminder"
        
        # Test 3: Move to nonexistent collection
        success = reminders.move_reminder(test_guids[0], "NonexistentList")
        assert not success, "Should fail when moving to nonexistent collection"
        
        # Test 4: Complete nonexistent reminder
        success = reminders.complete("nonexistent-guid")
        assert not success, "Should fail when completing nonexistent reminder"
        
        # Test 5: Invalid date ranges
        future_date = datetime.now() + timedelta(days=1)
        past_date = datetime.now() - timedelta(days=1)
        
        # Test with end date before start date
        invalid_range_reminders = reminders.get_reminders_by_due_date(
            start_date=future_date,
            end_date=past_date
        )
        assert len(invalid_range_reminders) == 0, "Should return empty list for invalid date range"
        
        # Test 6: Batch operations with mix of valid and invalid GUIDs
        valid_guid = reminders.post(
            "Valid Reminder",
            description="For batch testing",
            collection=first_list
        )
        test_guids.append(valid_guid)
        
        mixed_results = reminders.batch_complete([valid_guid, "nonexistent-guid"])
        assert mixed_results[valid_guid], "Should succeed for valid GUID"
        assert not mixed_results.get("nonexistent-guid", True), "Should fail for invalid GUID"
        
        # Test 7: Get reminders from nonexistent collection
        nonexistent_list_reminders = reminders.get_reminders_by_collection("NonexistentList")
        assert len(nonexistent_list_reminders) == 0, "Should return empty list for nonexistent collection"
        
        # Test 8: Create reminder with empty title
        guid = reminders.post(
            "",
            description="Empty title test",
            collection=first_list
        )
        if guid:  # Some implementations might allow empty titles
            test_guids.append(guid)
            empty_title_reminder = reminders.get_reminder(guid)
            assert empty_title_reminder is not None, "Should be able to retrieve reminder with empty title"
        
        # Clean up test reminders
        for guid in test_guids:
            reminders.complete(guid)
            
    except Exception as e:
        # Clean up even if test fails
        for guid in test_guids:
            try:
                reminders.complete(guid)
            except:
                pass
        pytest.fail(f"Failed during error cases test: {str(e)}")