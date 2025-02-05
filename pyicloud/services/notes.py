"""Notes service."""
from datetime import datetime
import logging
import uuid
from typing import List, Dict, Optional, Union, Any
import json
import time
from collections import defaultdict
from tzlocal import get_localzone_name
import pytz
from pyicloud.exceptions import PyiCloudException, PyiCloudAPIResponseException

logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

REQUEST_TIMEOUT = 30
BATCH_SIZE = 50

class NotesNotAvailable(Exception):
    """Raised when Notes service is not available."""
    pass

class NonRetryableError(Exception):
    """Raised when an error occurs that should not be retried."""
    pass

class NotesService:
    """The 'Notes' iCloud service."""
    
    def __init__(self, session, service_root: str, max_retries: int = 3):
        """Initialize the Notes service."""
        self.session = session
        self._service_root = service_root
        self._max_retries = max_retries
        self.collections = {}  # Folders by name
        self.lists = defaultdict(list)  # Notes by folder name
        self._notes_by_guid = {}  # Notes by GUID
        self._tags = set()  # All unique tags

        # Get web token from session
        web_token = session.service.data.get("dsInfo", {}).get("dsid")
        if not web_token:
            raise NotesNotAvailable("Failed to get web token")

        # Set up headers with working calendar service values
        self.session.headers.update({
            'X-Apple-Auth-Token': session.service.session_data.get('session_token'),
            'X-Apple-Time-Zone': get_localzone_name(),
            'X-Apple-CloudKit-Request-ISO8601Timestamp': datetime.utcnow().isoformat(),
            'Accept': 'application/json',
            'Content-Type': 'application/json',
            'Host': 'p85-notes.icloud.com',  # Required header for iOS 17
            'Origin': 'https://www.icloud.com',
            'Referer': 'https://www.icloud.com/'
        })
        
        # Add service-specific parameters for iOS 13+ format
        self.params = {
            "clientBuildNumber": "2308Project45",  # Updated to latest known version
            "clientMasteringNumber": "2308B45",
            "clientId": session.service.client_id,
            "dsid": session.service.data.get("dsInfo", {}).get("dsid"),
            "lang": "en-us",
            "usertz": get_localzone_name(),
            "notesWebUIVersion": "2.0",
        }
        
        # Force authentication refresh for notes service
        try:
            session.service.authenticate(True, "notes")
        except Exception as e:
            logger.warning(f"Failed to refresh notes authentication: {e}")
        
        # Get web token from session
        web_token = session.service.session_data.get("session_token")
        if not web_token:
            raise NotesNotAvailable("Failed to get session token")

        # Update headers with correct iOS 13+ values
        self.session.headers.update({
            "X-Apple-I-Web-Token": web_token,
            "X-Apple-Routing-Key": f"{self.params['dsid']}:0:notes",
            "X-Apple-I-Protocol-Version": "1.0",
            "X-Apple-I-TimeZone": get_localzone_name(),
            "X-Apple-I-Client-Time": datetime.now().isoformat()
        })

        # Initial refresh
        if not self.refresh():
            raise NotesNotAvailable("Failed to initialize notes service")

    def _make_request(self, method: str, endpoint: str, data: Optional[Dict] = None,
                     params: Optional[Dict] = None, timeout: int = REQUEST_TIMEOUT) -> Optional[Any]:
        """Make an authenticated request with minimal retries."""
        max_retries = self._max_retries
        retry_count = 0
        last_error = None
        
        while retry_count < max_retries:
            try:
                logger.debug(f"Making {method} request to {endpoint}")
                request_params = {**self.params, **(params or {})}
                
                # Add required headers
                headers = {
                    'X-Apple-Web-Token': self.session.service.session_data.get('session_token'),
                    'X-Apple-Time-Zone': get_localzone_name(),
                    'X-Apple-CloudKit-Request-ISO8601Timestamp': 
                        datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ'),
                    'X-Apple-I-ClientTime': datetime.now().strftime('%Y-%m-%dT%H:%M:%S%z')
                }
                
                url = endpoint if endpoint.startswith('http') else f"{self._service_root}{endpoint}"
                if method.lower() == 'get':
                    response = self.session.get(
                        url,
                        params=request_params,
                        timeout=timeout,
                        headers=headers
                    )
                else:
                    response = self.session.post(
                        url,
                        json=data,  # Changed from data=json.dumps(data)
                        params=request_params,
                        timeout=timeout,
                        headers=headers
                    )
                
                # Handle different error cases
                if response.status_code == 401:
                    logger.debug("Got 401, attempting auth refresh")
                    self.session.service.authenticate(True, "notes")
                    retry_count += 1
                    continue
                    
                elif response.status_code == 500 and "Authentication required" in response.text:
                    logger.debug("Got auth required error, attempting auth refresh")
                    self.session.service.authenticate(True, "notes")
                    retry_count += 1
                    continue
                    
                elif response.status_code == 503:
                    # Service unavailable - retry with backoff
                    retry_after = min(int(response.headers.get('Retry-After', 2)), 5)  # Cap at 5 seconds
                    logger.warning("Got 503, waiting %d seconds before retry", retry_after)
                    time.sleep(retry_after)
                    retry_count += 1
                    continue
                    
                elif response.status_code == 450:  # Notes-specific auth failures
                    logger.debug("Notes-specific auth failure, refreshing...")
                    self.session.service.authenticate(
                        force_refresh=True,
                        service='notes'
                    )
                    retry_count += 1
                    continue
                    
                elif response.status_code >= 400:
                    # Other errors - non-retryable
                    logger.error("Got error status %d: %s", 
                               response.status_code,
                               response.text if response.text else "No error message")
                    raise NonRetryableError(f"HTTP {response.status_code}: {response.text}")
                
                response.raise_for_status()
                return response.json()
                
            except Exception as e:
                last_error = e
                logger.error(f"Request failed: {str(e)}")
                retry_count += 1
                if retry_count < max_retries:
                    time.sleep(2 ** retry_count)  # Exponential backoff
                    continue
                raise NonRetryableError(str(e))
                
        if last_error:
            raise NonRetryableError(f"Max retries exceeded: {str(last_error)}")
        return None

    def refresh(self) -> bool:
        """Refresh the notes data."""
        try:
            # Get initial startup data with proper parameters
            startup = self._make_request(
                "post",  # Changed from GET to POST
                "/no/startup",
                data={
                    "clientVersion": "4039.6.6",
                    "syncToken": "",
                    "requestID": str(uuid.uuid4()).upper(),
                    "schema": "chunked:2",  # New required field
                    "timeout": 10000
                }
            )
            if not startup:
                return False
            
            logger.debug("Startup response: %s", startup)

            # Initialize collections
            self.collections = {}
            self.lists = defaultdict(list)
            self._notes_by_guid = {}
            self._tags = set()

            # Process folders first
            folders = startup.get('folders', [])
            for folder in folders:
                folder_guid = folder.get('identifier') or folder.get('folderId')
                folder_name = folder.get('name') or folder.get('folderName', '/')
                self.collections[folder_name] = {
                    "guid": folder_guid or f"folder_{folder_name}",
                    "ctag": folder.get('serverCtag') or folder.get('etag', ''),
                    "type": "folder",
                    "parentId": folder.get('parentIdentifier') or folder.get('parentId', 'root'),
                    "order": folder.get('sortOrder') or folder.get('order', 0)
                }

            # Create default root folder if no folders exist
            if not self.collections:
                self.collections['/'] = {
                    "guid": "root",
                    "ctag": startup.get('syncToken', ''),
                    "type": "folder",
                    "parentId": "root",
                    "order": 0
                }

            # Process notes
            notes = startup.get('notes', [])
            for note in notes:
                folder_name = note.get('folderName', '/')
                if folder_name not in self.collections:
                    self.collections[folder_name] = {
                        "guid": f"folder_{folder_name}",
                        "ctag": startup.get('syncToken', ''),
                        "type": "folder",
                        "parentId": "root",
                        "order": len(self.collections)
                    }
                
                # Extract note data with proper field mapping
                note_guid = note.get('identifier') or note.get('noteGuid')
                note_data = {
                    "guid": note_guid,
                    "title": note.get('title') or note.get('subject', ''),
                    "folder": folder_name,
                    "size": note.get('contentLength') or note.get('size', 0),
                    "modified": note.get('modified') or note.get('lastModifiedDate'),
                    "content": note.get('content') or note.get('detail', {}).get('content'),
                    "tags": note.get('tags', []),
                    "created": note.get('created') or note.get('createdDate'),
                    "isShared": note.get('isShared', False),
                    "hasAttachments": note.get('hasAttachments', False)
                }
                self.lists[folder_name].append(note_data)
                self._notes_by_guid[note_data['guid']] = note_data
                if note_data['tags']:
                    self._tags.update(note_data['tags'])

            return True

        except Exception as e:
            logger.error(f"Failed to refresh notes: {str(e)}")
            return False

    def create(self, title: str, body: str, collection: Optional[str] = None,
                tags: Optional[List[str]] = None, pguid: Optional[str] = None) -> Optional[str]:
        """Create a new note."""
        try:
            # Get collection GUID
            if collection and collection not in self.collections:
                logger.error(f"Collection not found: {collection}")
                collection = "/"  # Always use root folder if collection not found

            # Generate note ID in the correct format
            note_id = f"{str(uuid.uuid4()).upper()}%Tm90ZXM=%{len(self.lists[collection or '/']) + 1}"
            now = datetime.now()
            local_tz = pytz.timezone(get_localzone_name())
            now_local = now.astimezone(local_tz)
            
            # Format the content as HTML like existing notes
            content = f'<html><head></head><body style="word-wrap: break-word; -webkit-nbsp-mode: space; -webkit-line-break: after-white-space;">{body}</body></html>'
            
            collection_guid = self.collections.get(collection or "/", {}).get("guid", "root")
            note_data = {
                "notes": [{
                    "guid": note_id,
                    "subject": title,
                    "body": {
                        "content": content,
                        "preview": body[:100]  # First 100 chars as preview
                    },
                    "folderId": collection_guid,
                    "created": int(now.timestamp() * 1000),
                    "modified": int(now.timestamp() * 1000),
                    "tags": tags or [],
                    "pinned": False,
                    "deleted": False,
                    "version": 1
                }]
            }

            response = self._make_request(
                "post",
                "/no/content",
                data=note_data,
                timeout=REQUEST_TIMEOUT
            )

            if response:
                # Update local cache
                cache_data = {
                    "guid": note_id,
                    "title": title,
                    "body": body,
                    "content": content,
                    "collection": collection or "/",
                    "size": len(content),
                    "tags": tags or [],
                    "modified": now_local.strftime("%Y-%m-%dT%H:%M:%S%z"),
                }
                self._notes_by_guid[note_id] = cache_data
                self.lists[collection or "/"].append(cache_data)
                if tags:
                    self._tags.update(tags)
                return note_id

        except NonRetryableError as e:
            logger.error(f"Failed to create note: {str(e)}")
        except Exception as e:
            logger.error(f"Unexpected error creating note: {str(e)}")
            
        return None

    def get_note(self, note_id: str) -> Optional[Dict]:
        """Get a note by its ID."""
        return self._notes_by_guid.get(note_id)

    def update(self, note_id: str, title: Optional[str] = None,
              body: Optional[str] = None, tags: Optional[List[str]] = None) -> bool:
        """Update a note."""
        if note_id not in self._notes_by_guid:
            return False

        current = self._notes_by_guid[note_id]
        pguid = current["p_guid"]

        update_data = {
            "fields": {
                "guid": note_id,
                "pGuid": pguid,
                "title": title if title is not None else current["title"],
                "content": body if body is not None else current.get("body", ""),
                "tags": tags if tags is not None else current.get("tags", []),
                "lastModifiedDate": int(time.time() * 1000),
                "isShared": current.get("isShared", False),
                "hasAttachments": current.get("hasAttachments", False),
                "attachments": current.get("attachments", []),
            }
        }

        try:
            response = self._make_request(
                "post",
                "/no/content",
                data=update_data,
                timeout=REQUEST_TIMEOUT
            )

            if response:
                # Update local cache
                current.update({
                    "title": update_data["fields"]["title"],
                    "body": update_data["fields"]["content"],
                    "tags": update_data["fields"]["tags"],
                    "modified": datetime.now(pytz.UTC).isoformat(),
                })
                
                # Update tags set
                if tags:
                    self._tags.update(tags)
                
                return True

        except NonRetryableError as e:
            logger.error(f"Failed to update note: {str(e)}")
        except Exception as e:
            logger.error(f"Unexpected error updating note: {str(e)}")
            
        return False

    def delete_note(self, note_id: str) -> bool:
        """Delete a note."""
        if note_id not in self._notes_by_guid:
            return False

        try:
            response = self._make_request(
                "delete",
                f"/no/content/{note_id}",
                timeout=REQUEST_TIMEOUT
            )

            if response:
                # Update local cache
                note = self._notes_by_guid.pop(note_id)
                collection = note["collection"]
                self.lists[collection].remove(note)
                return True

        except NonRetryableError as e:
            logger.error(f"Failed to delete note: {str(e)}")
        except Exception as e:
            logger.error(f"Unexpected error deleting note: {str(e)}")
            
        return False

    def get_notes_by_collection(self, collection: str) -> List[Dict]:
        """Get all notes in a collection."""
        return self.lists.get(collection, [])

    def search(self, query: str) -> List[Dict]:
        """Search notes."""
        try:
            response = self._make_request(
                "post",
                "/no/search",
                data={"query": query},
                timeout=REQUEST_TIMEOUT
            )

            if response and "results" in response:
                return [
                    self._notes_by_guid[note["guid"]]
                    for note in response["results"]
                    if note["guid"] in self._notes_by_guid
                ]

        except NonRetryableError as e:
            logger.error(f"Failed to search notes: {str(e)}")
        except Exception as e:
            logger.error(f"Unexpected error searching notes: {str(e)}")
            
        return []

    def create_folder(self, name: str) -> bool:
        """Create a new folder in Notes."""
        try:
            folder_id = f"{str(uuid.uuid4()).upper()}%Tm90ZXM=%{len(self.collections) + 1}"
            
            folder_data = {
                "operation": "create",
                "recordName": folder_id,
                "recordType": "Folder",
                "fields": {
                    "title": {"value": name},
                    "parentID": {"value": "root"},
                    "color": {"value": 7}  # Gray
                }
            }

            response = self._make_request(
                "post",
                "/no/content",
                data=folder_data,
                timeout=REQUEST_TIMEOUT
            )

            if response:
                # Update local cache
                self.collections[name] = {
                    "guid": folder_id,
                    "ctag": response.get("syncToken", ""),
                    "type": "folder",
                    "order": len(self.collections)
                }
                return True

        except NonRetryableError as e:
            logger.error(f"Failed to create folder: {str(e)}")
        except Exception as e:
            logger.error(f"Unexpected error creating folder: {str(e)}")
            
        return False 
