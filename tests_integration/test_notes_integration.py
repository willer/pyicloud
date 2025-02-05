"""Integration tests for Notes service."""
import logging
import pytest
from datetime import datetime, timedelta
import uuid
from typing import List, Dict

logger = logging.getLogger(__name__)

def test_chief_of_staff_operations(notes):
    """Test Chief of Staff operations with Notes."""
    try:
        # Get initial lists to work with
        all_lists = notes.lists
        assert len(all_lists) >= 1, "Need at least one notes list to test"
        
        first_list = list(all_lists.keys())[0]
        second_list = list(all_lists.keys())[1] if len(all_lists) > 1 else first_list
        
        logger.info("Testing with lists: %s and %s", first_list, second_list)
        
        # Test data
        test_notes = [
            {
                "title": "Meeting Notes: Q1 Review",
                "body": "Key points to discuss:\n1. Performance metrics\n2. Project status\n3. Next quarter goals",
                "collection": first_list,
                "tags": ["meeting", "quarterly-review"]
            },
            {
                "title": "Project Brainstorm",
                "body": "Ideas for new features:\n- AI integration\n- Better sync\n- Improved UI",
                "collection": first_list,
                "tags": ["project", "planning"]
            },
            {
                "title": "Action Items",
                "body": "TODO:\n1. Follow up with team\n2. Schedule next meeting\n3. Send summary",
                "collection": first_list,
                "tags": ["todo", "follow-up"]
            }
        ]
        
        # Store created note IDs for cleanup
        note_ids = []
        
        # Create notes and verify their creation
        for note in test_notes:
            note_id = notes.create(
                title=note["title"],
                body=note["body"],
                collection=note["collection"],
                tags=note["tags"]
            )
            assert note_id is not None, f"Failed to create note: {note['title']}"
            note_ids.append(note_id)
            
            # Verify the note was created correctly
            created_note = notes.get_note(note_id)
            assert created_note is not None, f"Could not find created note: {note['title']}"
            assert created_note["title"] == note["title"], "Title mismatch"
            assert created_note["body"] == note["body"], "Body mismatch"
            assert set(created_note["tags"]) == set(note["tags"]), "Tags mismatch"
            logger.debug("Created note: %s", created_note)
        
        # Test searching notes
        search_results = notes.search("quarterly-review")
        assert len(search_results) >= 1, "Should find note with 'quarterly-review' tag"
        assert any(note["title"] == "Meeting Notes: Q1 Review" for note in search_results)
        
        # Test getting notes by collection
        first_list_notes = notes.get_notes_by_collection(first_list)
        assert len(first_list_notes) >= len(test_notes), f"Should find at least {len(test_notes)} notes in first list"
        
        # Test updating a note
        update_id = note_ids[0]
        updated_body = test_notes[0]["body"] + "\n4. Review budget"
        success = notes.update(
            update_id,
            body=updated_body
        )
        assert success, "Failed to update note"
        
        # Verify the update
        updated_note = notes.get_note(update_id)
        assert updated_note["body"] == updated_body, "Body not updated correctly"
        
        # Test moving notes between lists (if we have two different lists)
        if first_list != second_list:
            # Move one note to second list
            success = notes.move_note(note_ids[0], second_list)
            assert success, "Failed to move note to second list"
            
            # Verify the move
            moved_note = notes.get_note(note_ids[0])
            assert moved_note["collection"] == second_list, "Note not in correct list"
            
            # Test batch move
            batch_move_results = notes.batch_move([note_ids[1], note_ids[2]], second_list)
            assert all(batch_move_results.values()), "Failed to move notes in batch"
            
            # Verify the moves
            second_list_notes = notes.get_notes_by_collection(second_list)
            for note_id in note_ids:
                assert any(note["id"] == note_id for note in second_list_notes), f"Note {note_id} not found in second list"
        
        # Test note sharing (if supported)
        try:
            share_url = notes.share_note(note_ids[0])
            assert share_url is not None, "Failed to get share URL"
            logger.debug("Share URL: %s", share_url)
        except NotImplementedError:
            logger.warning("Note sharing not supported")
        
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
        pytest.fail(f"Failed during Notes operations test: {str(e)}") 