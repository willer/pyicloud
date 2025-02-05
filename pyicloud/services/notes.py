"""Notes service."""
from datetime import datetime
import logging
import uuid
from typing import List, Dict, Optional, Union, Any, Tuple
import sys

if sys.platform == 'darwin':
    import objc
    from Foundation import (
        NSError,
        NSURL
    )
    # Note: NotesKit is private API, we need to load it dynamically
    notes_bundle = objc.loadBundle('Notes', globals(),
                                 bundle_path='/System/Applications/Notes.app/Contents/Frameworks/Notes.framework')

logger = logging.getLogger(__name__)

class WebNotesService:
    """iCloud web API implementation of notes."""
    
    def __init__(self, service_root, session, params):
        self.session = session
        self.params = params
        self._service_root = service_root
        self.refresh()
        
    def refresh(self, force=False):
        """Refresh from iCloud."""
        return True

    @property
    def lists(self) -> Dict[str, List[Dict]]:
        """Get all note lists with their notes."""
        raise NotImplementedError("Web API notes service not yet implemented")

    def create(self, title: str, body: str = "", collection: Optional[str] = None,
              tags: List[str] = None) -> Optional[str]:
        """Create a new note."""
        raise NotImplementedError("Web API notes service not yet implemented")

    def get_note(self, note_id: str) -> Optional[Dict]:
        """Get a note by its ID."""
        raise NotImplementedError("Web API notes service not yet implemented")

    def update(self, note_id: str, title: Optional[str] = None,
              body: Optional[str] = None, tags: Optional[List[str]] = None) -> bool:
        """Update a note."""
        raise NotImplementedError("Web API notes service not yet implemented")

    def delete_note(self, note_id: str) -> bool:
        """Delete a note."""
        raise NotImplementedError("Web API notes service not yet implemented")

    def get_notes_by_collection(self, collection: str) -> List[Dict]:
        """Get all notes in a collection."""
        raise NotImplementedError("Web API notes service not yet implemented")

    def move_note(self, note_id: str, target_collection: str) -> bool:
        """Move a note to a different collection."""
        raise NotImplementedError("Web API notes service not yet implemented")

    def batch_move(self, note_ids: List[str], target_collection: str) -> Dict[str, bool]:
        """Move multiple notes to a different collection."""
        raise NotImplementedError("Web API notes service not yet implemented")

    def search(self, query: str) -> List[Dict]:
        """Search notes."""
        raise NotImplementedError("Web API notes service not yet implemented")

    def share_note(self, note_id: str) -> Optional[str]:
        """Get a sharing URL for a note."""
        raise NotImplementedError("Web API notes service not yet implemented")

class NotesKitService:
    """Native macOS Notes implementation using NotesKit"""
    
    def __init__(self):
        self.store = objc.lookUpClass('NoteStore').defaultStore()
        self._verify_authorization()
        self._accounts = None
        self.refresh()
        
    def _verify_authorization(self):
        """Verify we have permission to access Notes."""
        # NotesKit doesn't have a formal authorization API like EventKit
        # Instead, we'll try to access the store and handle any errors
        if not self.store:
            raise Exception("Failed to access Notes.app store")

    def refresh(self, force=False):
        """Refresh accounts and notes from NotesKit."""
        self._accounts = self.store.accounts()
        return True

    @property
    def lists(self) -> Dict[str, List[Dict]]:
        """Get all note lists with their notes."""
        if not self._accounts:
            self.refresh()
        
        result = {}
        for account in self._accounts:
            for folder in account.folders():
                notes = []
                for note in folder.notes():
                    notes.append(self._convert_note_to_dict(note))
                result[folder.name()] = notes
                
        return result

    def create(self, title: str, body: str = "", collection: Optional[str] = None,
              tags: List[str] = None) -> Optional[str]:
        """Create a new note."""
        try:
            # Find target folder
            target_folder = None
            if collection:
                for account in self._accounts:
                    for folder in account.folders():
                        if folder.name() == collection:
                            target_folder = folder
                            break
                    if target_folder:
                        break
            
            if not target_folder:
                # Use first available folder
                target_folder = self._accounts[0].folders()[0]

            # Create the note
            note = objc.lookUpClass('Note').alloc().init()
            note.setTitle_(title)
            note.setBody_(body)
            
            if tags:
                note.setTags_(tags)
            
            # Add to folder
            success = target_folder.addNote_error_(note, None)
            if success:
                return str(note.identifier())
            else:
                logger.error("Failed to save note")
                return None

        except Exception as e:
            logger.error(f"Failed to create note: {str(e)}")
            return None

    def get_note(self, note_id: str) -> Optional[Dict]:
        """Get a note by its ID."""
        try:
            note = self.store.noteWithIdentifier_(note_id)
            if note:
                return self._convert_note_to_dict(note)
            return None
        except Exception as e:
            logger.error(f"Failed to get note: {str(e)}")
            return None

    def update(self, note_id: str, title: Optional[str] = None,
              body: Optional[str] = None, tags: Optional[List[str]] = None) -> bool:
        """Update a note."""
        try:
            note = self.store.noteWithIdentifier_(note_id)
            if not note:
                return False

            if title is not None:
                note.setTitle_(title)
            if body is not None:
                note.setBody_(body)
            if tags is not None:
                note.setTags_(tags)

            success = note.save()
            if not success:
                logger.error("Failed to update note")
            return success

        except Exception as e:
            logger.error(f"Failed to update note: {str(e)}")
            return False

    def delete_note(self, note_id: str) -> bool:
        """Delete a note."""
        try:
            note = self.store.noteWithIdentifier_(note_id)
            if not note:
                return False

            success = note.delete()
            if not success:
                logger.error("Failed to delete note")
            return success

        except Exception as e:
            logger.error(f"Failed to delete note: {str(e)}")
            return False

    def get_notes_by_collection(self, collection: str) -> List[Dict]:
        """Get all notes in a collection."""
        try:
            for account in self._accounts:
                for folder in account.folders():
                    if folder.name() == collection:
                        return [self._convert_note_to_dict(note) for note in folder.notes()]
            return []
        except Exception as e:
            logger.error(f"Failed to get notes by collection: {str(e)}")
            return []

    def move_note(self, note_id: str, target_collection: str) -> bool:
        """Move a note to a different collection."""
        try:
            note = self.store.noteWithIdentifier_(note_id)
            if not note:
                return False

            target_folder = None
            for account in self._accounts:
                for folder in account.folders():
                    if folder.name() == target_collection:
                        target_folder = folder
                        break
                if target_folder:
                    break

            if not target_folder:
                return False

            success = note.moveToFolder_error_(target_folder, None)
            if not success:
                logger.error("Failed to move note")
            return success

        except Exception as e:
            logger.error(f"Failed to move note: {str(e)}")
            return False

    def batch_move(self, note_ids: List[str], target_collection: str) -> Dict[str, bool]:
        """Move multiple notes to a different collection."""
        results = {}
        for note_id in note_ids:
            results[note_id] = self.move_note(note_id, target_collection)
        return results

    def search(self, query: str) -> List[Dict]:
        """Search notes."""
        try:
            search_query = objc.lookUpClass('NoteSearchQuery').alloc().initWithSearchText_(query)
            results = self.store.executeQuery_(search_query)
            return [self._convert_note_to_dict(note) for note in results]
        except Exception as e:
            logger.error(f"Failed to search notes: {str(e)}")
            return []

    def share_note(self, note_id: str) -> Optional[str]:
        """Get a sharing URL for a note."""
        try:
            note = self.store.noteWithIdentifier_(note_id)
            if not note:
                return None

            share_url = note.shareURL()
            return str(share_url) if share_url else None

        except Exception as e:
            logger.error(f"Failed to share note: {str(e)}")
            return None

    def _convert_note_to_dict(self, note) -> Dict:
        """Convert a Note object to our standard dictionary format."""
        return {
            'id': str(note.identifier()),
            'title': str(note.title()),
            'body': str(note.body()),
            'collection': str(note.folder().name()),
            'tags': list(note.tags()) if note.tags() else [],
            'created_date': note.creationDate(),
            'modified_date': note.modificationDate(),
            'shared': bool(note.isShared())
        }

class NotesService:
    """The 'Notes' iCloud service."""

    def __init__(self, service_root=None, session=None, params=None):
        """Initialize the notes service.
        
        On macOS, this will use the native NotesKit framework.
        On other platforms, it will use the iCloud web API.
        """
        # Use NotesKit on macOS
        if sys.platform == 'darwin':
            self._impl = NotesKitService()
        else:
            # Fall back to web API implementation
            self._impl = WebNotesService(service_root, session, params)

    def refresh(self, force: bool = False) -> bool:
        """Refresh data from the implementation."""
        return self._impl.refresh(force)

    @property
    def lists(self) -> Dict[str, List[Dict]]:
        """Get all note lists."""
        return self._impl.lists

    def create(self, title: str, body: str = "", collection: Optional[str] = None,
              tags: List[str] = None) -> Optional[str]:
        """Create a new note."""
        return self._impl.create(title, body, collection, tags)

    def get_note(self, note_id: str) -> Optional[Dict]:
        """Get a note by its ID."""
        return self._impl.get_note(note_id)

    def update(self, note_id: str, title: Optional[str] = None,
              body: Optional[str] = None, tags: Optional[List[str]] = None) -> bool:
        """Update a note."""
        return self._impl.update(note_id, title, body, tags)

    def delete_note(self, note_id: str) -> bool:
        """Delete a note."""
        return self._impl.delete_note(note_id)

    def get_notes_by_collection(self, collection: str) -> List[Dict]:
        """Get all notes in a collection."""
        return self._impl.get_notes_by_collection(collection)

    def move_note(self, note_id: str, target_collection: str) -> bool:
        """Move a note to a different collection."""
        return self._impl.move_note(note_id, target_collection)

    def batch_move(self, note_ids: List[str], target_collection: str) -> Dict[str, bool]:
        """Move multiple notes to a different collection."""
        return self._impl.batch_move(note_ids, target_collection)

    def search(self, query: str) -> List[Dict]:
        """Search notes."""
        return self._impl.search(query)

    def share_note(self, note_id: str) -> Optional[str]:
        """Get a sharing URL for a note."""
        return self._impl.share_note(note_id) 