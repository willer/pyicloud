"""Integration tests for Notes service."""
import logging
import pytest
from datetime import datetime, timedelta
import uuid
from typing import List, Dict

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.DEBUG)

def ensure_test_folder(notes):
    """Ensure the 'Test' folder exists and return it."""
    lists = notes.lists
    
    # Check if 'Test' folder exists
    if "Test" not in lists:
        # Try to create the Test folder
        if hasattr(notes, 'create_folder'):
            logger.info("Creating Test folder...")
            if notes.create_folder("Test"):
                logger.info("Test folder created successfully")
                return "Test"
        # If we can't create it, use the default folder
        logger.info("Using default Notes folder")
        return notes._default_folder
    
    return "Test"

def test_chief_of_staff_operations(notes):
    """Test Chief of Staff operations with Notes."""
    note_ids = []  # Initialize note_ids before the try block
    
    try:
        # Get initial lists to work with
        test_folder = ensure_test_folder(notes)
        logger.info("Using test folder: %s", test_folder)
        
        # Test data
        test_notes = [
            {
                "title": "Meeting Notes: Q1 Review",
                "body": "<html><head><meta charset=\"UTF-8\"><meta name=\"apple-notes-version\" content=\"3.0\"><meta name=\"apple-notes-editable\" content=\"true\"></head><body style=\"word-wrap: break-word; -webkit-nbsp-mode: space; -webkit-line-break: after-white-space;\">Key points to discuss:\n1. Performance metrics\n2. Project status\n3. Next quarter goals</body></html>",
                "tags": ["meeting", "quarterly-review"]
            },
            {
                "title": "Project Brainstorm",
                "body": "<html><head><meta charset=\"UTF-8\"><meta name=\"apple-notes-version\" content=\"3.0\"><meta name=\"apple-notes-editable\" content=\"true\"></head><body style=\"word-wrap: break-word; -webkit-nbsp-mode: space; -webkit-line-break: after-white-space;\">Ideas for new features:\n- AI integration\n- Better sync\n- Improved UI</body></html>",
                "tags": ["project", "planning"]
            },
            {
                "title": "Action Items",
                "body": "<html><head><meta charset=\"UTF-8\"><meta name=\"apple-notes-version\" content=\"3.0\"><meta name=\"apple-notes-editable\" content=\"true\"></head><body style=\"word-wrap: break-word; -webkit-nbsp-mode: space; -webkit-line-break: after-white-space;\">TODO:\n1. Follow up with team\n2. Schedule next meeting\n3. Send summary</body></html>",
                "tags": ["todo", "follow-up"]
            }
        ]
        
        # Create notes and verify their creation
        for note in test_notes:
            note_id = notes.create(
                title=note["title"],
                body=note["body"],
                collection=test_folder,
                tags=note["tags"]
            )
            assert note_id is not None, f"Failed to create note: {note['title']}"
            note_ids.append(note_id)
            
            # Verify the note was created correctly
            created_note = notes.get_note(note_id)
            assert created_note is not None, f"Could not find created note: {note['title']}"
            assert created_note["subject"] == note["title"], "Title mismatch"
            assert created_note["content"] == note["body"], "Body mismatch"
            assert created_note["folderName"] == test_folder, "Collection mismatch"
            assert set(created_note["tags"]) == set(note["tags"]), "Tags mismatch"
            logger.debug("Created note: %s", created_note)
        
        # Test searching notes
        search_results = notes.search("quarterly-review")
        assert len(search_results) >= 1, "Should find note with 'quarterly-review' tag"
        assert any(note["subject"] == "Meeting Notes: Q1 Review" for note in search_results)
        
        # Test getting notes by collection
        test_folder_notes = notes.get_notes_by_collection(test_folder)
        assert len(test_folder_notes) >= len(test_notes), f"Should find at least {len(test_notes)} notes in test folder"
        
        # Test updating a note
        update_id = note_ids[0]
        updated_body = test_notes[0]["body"].replace("</body></html>", "\n4. Review budget</body></html>")
        success = notes.update(
            update_id,
            body=updated_body
        )
        assert success, "Failed to update note"
        
        # Verify the update
        updated_note = notes.get_note(update_id)
        assert updated_note["content"] == updated_body, "Body not updated correctly"
        
        # Clean up - delete test notes
        for note_id in note_ids:
            success = notes.delete_note(note_id)
            assert success, f"Failed to delete note {note_id}"
            
        # Verify deletion
        for note_id in note_ids:
            deleted_note = notes.get_note(note_id)
            assert deleted_note is None, f"Note {note_id} still exists after deletion"
            
    except Exception as e:
        logger.error("Test failed with error: %s", str(e))
        # Clean up even if test fails
        for note_id in note_ids:
            try:
                notes.delete_note(note_id)
            except:
                pass
        pytest.fail(f"Failed during Notes operations test: {str(e)}")

def test_notes_error_cases(notes):
    """Test error cases and edge conditions for notes."""
    test_ids = []  # Initialize test_ids before the try block
    
    try:
        test_folder = ensure_test_folder(notes)
        
        # Test 1: Invalid collection name
        note_id = notes.create(
            title="Test Note",
            body="<html><head><meta charset=\"UTF-8\"><meta name=\"apple-notes-version\" content=\"3.0\"><meta name=\"apple-notes-editable\" content=\"true\"></head><body style=\"word-wrap: break-word; -webkit-nbsp-mode: space; -webkit-line-break: after-white-space;\">Test body</body></html>",
            collection="NonexistentFolder"  # Should fall back to default folder
        )
        assert note_id is not None, "Should create note in default folder when collection is invalid"
        test_ids.append(note_id)
        
        created_note = notes.get_note(note_id)
        assert created_note["folderName"] == notes._default_folder, "Note should be created in default folder"
        
        # Test 2: Get nonexistent note
        nonexistent_note = notes.get_note("nonexistent-id")
        assert nonexistent_note is None, "Should return None for nonexistent note"
        
        # Test 3: Update nonexistent note
        success = notes.update("nonexistent-id", title="New Title")
        assert not success, "Should fail when updating nonexistent note"
        
        # Test 4: Delete nonexistent note
        success = notes.delete_note("nonexistent-id")
        assert not success, "Should fail when deleting nonexistent note"
        
        # Test 5: Empty title/body
        note_id = notes.create(
            title="",  # Empty title
            body="<html><head><meta charset=\"UTF-8\"><meta name=\"apple-notes-version\" content=\"3.0\"><meta name=\"apple-notes-editable\" content=\"true\"></head><body style=\"word-wrap: break-word; -webkit-nbsp-mode: space; -webkit-line-break: after-white-space;\">Note with empty title</body></html>",
            collection=test_folder
        )
        if note_id:  # Some implementations might allow empty titles
            test_ids.append(note_id)
            empty_title_note = notes.get_note(note_id)
            assert empty_title_note is not None, "Should be able to retrieve note with empty title"
        
        # Test 6: Get notes from nonexistent collection
        nonexistent_folder_notes = notes.get_notes_by_collection("NonexistentFolder")
        assert len(nonexistent_folder_notes) == 0, "Should return empty list for nonexistent collection"
        
        # Clean up test notes
        for note_id in test_ids:
            try:
                notes.delete_note(note_id)
            except:
                pass
            
    except Exception as e:
        # Clean up even if test fails
        for note_id in test_ids:
            try:
                notes.delete_note(note_id)
            except:
                pass
        pytest.fail(f"Failed during error cases test: {str(e)}") 