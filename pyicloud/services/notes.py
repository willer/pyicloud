"""Notes service."""
from datetime import datetime
import logging
import re
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
    
    def __init__(self, session, service_root: str, params: dict = None, max_retries: int = 3):
        """Initialize the Notes service."""
        self.session = session
        self._service_root = service_root
        self._max_retries = max_retries
        self.collections = {}  # Folders by name
        self.lists = defaultdict(list)  # Notes by GUID
        self._notes_by_guid = {}  # Notes by GUID
        self._tags = set()  # All unique tags
        self._default_folder = "Notes"  # Default folder if none exists

        # Get web token from session
        web_token = session.service.data.get("dsInfo", {}).get("dsid")
        if not web_token:
            raise NotesNotAvailable("Failed to get web token")

        # Extract host from service_root
        host = service_root.split("://")[1].split(":")[0]

        # Set up headers with consistent API versions
        self.session.headers.update({
            'X-Apple-Auth-Token': session.service.session_data.get('session_token'),
            'X-Apple-Time-Zone': get_localzone_name(),
            'X-Apple-CloudKit-Request-ISO8601Timestamp': datetime.utcnow().isoformat() + 'Z',
            'X-Apple-CloudKit-Request-Context': 'notes',
            'X-Apple-CloudKit-Request-Environment': 'production',
            'X-Apple-CloudKit-Request-SigningVersion': '3',
            'X-Apple-CloudKit-Request-KeyID': session.service.client_id,
            'X-Apple-CloudKit-Request-Container': 'com.apple.notes',
            'X-Apple-CloudKit-Request-Schema': 'chunked:3',
            'Accept': 'application/json',
            'Content-Type': 'application/json',
            'Host': host,
            'Origin': 'https://www.icloud.com',
            'Referer': 'https://www.icloud.com/'
        })
        
        # Add service-specific parameters with consistent API versions
        self.params = {
            "clientBuildNumber": "4039.6.6",
            "clientMasteringNumber": "4039B6",
            "clientId": session.service.client_id,
            "dsid": session.service.data.get("dsInfo", {}).get("dsid"),
            "lang": "en-us",
            "usertz": get_localzone_name(),
            "notesWebUIVersion": "3.0",
            "_cloudKitVersion": "3",
            "requestID": str(uuid.uuid4()).upper(),
            "schema": "chunked:3"
        }
        if params:
            self.params.update(params)
        
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
                
                # Generate a single requestID for both URL params and body
                request_id = str(uuid.uuid4()).upper()
                
                # Get current time in correct format
                now = datetime.utcnow()
                timestamp_z = now.strftime('%Y-%m-%dT%H:%M:%S.%f')[:-3] + 'Z'  # Truncate microseconds to 3 digits
                
                # Start with base params and ensure consistency
                request_params = {
                    **self.params,
                    "requestID": request_id,  # Use the same requestID
                    "_cloudKitVersion": "2"  # Ensure this is always 2
                }
                if params:
                    request_params.update(params)
                    request_params["requestID"] = request_id  # Ensure it's not overwritten
                
                # Ensure data consistency
                if data and isinstance(data, dict):
                    data = {**data}  # Make a copy to avoid modifying the original
                    data["_cloudKitVersion"] = "2"  # Ensure this is always 2
                    data["requestID"] = request_id  # Use the same requestID
                    data["schema"] = "chunked:3"
                
                # Add required headers with consistent timestamps
                headers = {
                    'X-Apple-Web-Token': self.session.service.session_data.get('session_token'),
                    'X-Apple-Time-Zone': get_localzone_name(),
                    'X-Apple-CloudKit-Request-ISO8601Timestamp': timestamp_z,
                    'X-Apple-CloudKit-Request-Context': 'notes',
                    'X-Apple-CloudKit-Request-Environment': 'production',
                    'X-Apple-CloudKit-Request-SigningVersion': '3',
                    'X-Apple-CloudKit-Request-KeyID': self.session.service.client_id,
                    'X-Apple-CloudKit-Request-Container': 'com.apple.notes',
                    'X-Apple-CloudKit-Request-Schema': 'chunked:3',
                    'X-Apple-I-ClientTime': timestamp_z,  # Use same format
                    'Host': self._service_root.split("://")[1].split(":")[0],
                    'X-Apple-I-Web-Token': self.session.service.session_data.get('session_token'),
                    'X-Apple-Routing-Key': f"{self.params['dsid']}:0:notes",
                    'X-Apple-I-Protocol-Version': "2.0",  # Match CloudKit version
                    'Accept': 'application/json',
                    'Content-Type': 'application/json',
                    'Origin': 'https://www.icloud.com',
                    'Referer': 'https://www.icloud.com/'
                }
                
                url = endpoint if endpoint.startswith('http') else f"{self._service_root}{endpoint}"
                logger.debug(f"Request URL: {url}")
                logger.debug(f"Request params: {request_params}")
                logger.debug(f"Request headers: {headers}")
                
                logger.debug("Making request - URL: %s, method: %s, params: %s, data: %s", 
                           url, method, request_params, data)
                logger.debug("Request headers: %s", headers)
                logger.debug("Auth token: %s", self.session.service.session_data.get("session_token"))

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
                        json=data,  # Ensure JSON encoding
                        params=request_params,
                        timeout=timeout,
                        headers=headers
                    )
                
                logger.debug("Response status: %d", response.status_code)
                logger.debug(f"Response headers: {response.headers}")
                logger.debug(f"Response body: {response.text}")
                
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
                logger.error("Request failed - URL: %s, method: %s, params: %s, headers: %s, error: %s",
                           url, method, request_params, headers, str(e))
                retry_count += 1
                if retry_count < max_retries:
                    time.sleep(2 ** retry_count)  # Exponential backoff
                    continue
                raise NonRetryableError(f"Request failed after {max_retries} retries: {str(e)}")
                
        if last_error:
            raise NonRetryableError(f"Max retries exceeded: {str(last_error)}")
        return None

    def refresh(self) -> bool:
        """Refresh the notes data from iCloud."""
        try:
            # Get initial startup data with proper parameters
            startup_response = self._make_request(
                "get",  # Try GET instead of POST
                "/no/startup",
                params={
                    "syncToken": "",
                    "requestID": str(uuid.uuid4()).upper(),
                    "schema": "chunked:3",  # Updated schema version
                    "_cloudKitVersion": "3",  # Updated CloudKit version
                    "timeout": 10000
                }
            )
            if not startup_response:
                logger.error("Failed to refresh notes: No response")
                return False
            
            logger.debug("Startup response: %s", startup_response)

            # Initialize collections
            self.collections = {}
            self.lists = defaultdict(list)
            self._notes_by_guid = {}
            self._tags = set()

            # Process folders first
            folders = startup_response.get('folders', [])
            for folder in folders:
                folder_guid = folder.get('identifier') or folder.get('folderId')
                folder_name = folder.get('name') or folder.get('folderName', '/')
                self.collections[folder_name] = {
                    "guid": folder_guid or f"folder_{folder_name}",
                    "ctag": folder.get('serverCtag') or folder.get('etag', ''),
                    "type": "folder",
                    "parentId": folder.get('parentIdentifier') or folder.get('parentId', 'root'),
                    "order": folder.get('sortOrder') or folder.get('order', 0),
                    "version": folder.get('version', 1),
                    "isShared": folder.get('isShared', False)
                }

            # Create default root folder if no folders exist
            if not self.collections:
                self.collections['/'] = {
                    "guid": "root",
                    "ctag": startup_response.get('syncToken', ''),
                    "type": "folder",
                    "parentId": "root",
                    "order": 0,
                    "version": 1,
                    "isShared": False
                }

            # Initialize empty lists for all folders
            for folder_name in self.collections:
                if folder_name not in self.lists:
                    self.lists[folder_name] = []

            # Process notes
            notes = startup_response.get('notes', [])
            for note in notes:
                folder_name = note.get('folderName', '/')
                if folder_name not in self.collections:
                    self.collections[folder_name] = {
                        "guid": f"folder_{folder_name}",
                        "ctag": startup_response.get('syncToken', ''),
                        "type": "folder",
                        "parentId": "root",
                        "order": len(self.collections),
                        "version": 1,
                        "isShared": False
                    }
                    self.lists[folder_name] = []
                
                # Extract note data with proper field mapping
                note_guid = note.get('identifier') or note.get('noteGuid')
                note_data = {
                    "guid": note_guid,
                    "noteId": note.get('noteId', f"{note_guid}%Tm90ZXM=%{int(time.time())}"),
                    "title": note.get('title') or note.get('subject', ''),
                    "folder": folder_name,
                    "size": note.get('contentLength') or note.get('size', 0),
                    "modified": note.get('modified') or note.get('lastModifiedDate'),
                    "content": note.get('content') or note.get('detail', {}).get('content'),
                    "tags": note.get('tags', []),
                    "created": note.get('created') or note.get('createdDate'),
                    "isShared": note.get('isShared', False),
                    "hasAttachments": note.get('hasAttachments', False),
                    "version": note.get('version', 1),
                    "folderId": self.collections[folder_name]["guid"]
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
            # Ensure collection exists, fallback to default if not
            if collection and collection not in self.collections:
                logger.warning(f"Collection {collection} not found, using default folder")
                collection = self._default_folder
            elif not collection:
                collection = self._default_folder

            # Generate unique IDs
            note_guid = str(uuid.uuid4()).upper()
            identifier = str(uuid.uuid4()).upper()

            # Format the content as HTML if not already HTML
            if not body.startswith('<html>'):
                body = f'<html><head><meta charset="UTF-8"><meta name="apple-notes-version" content="3.0"><meta name="apple-notes-editable" content="true"></head><body style="word-wrap: break-word; -webkit-nbsp-mode: space; -webkit-line-break: after-white-space;">{body}</body></html>'

            # Get current time in correct format
            now = datetime.utcnow()
            timestamp_z = now.strftime('%Y-%m-%dT%H:%M:%S.%f')[:-3] + 'Z'  # Truncate microseconds to 3 digits

            # Prepare note data
            note_data = {
                "identifier": identifier,
                "noteGuid": note_guid,
                "subject": title,
                "content": body,
                "folderName": collection,
                "folderGuid": self.collections.get(collection, {}).get("guid", "root"),
                "createdDate": timestamp_z,  # Use consistent format
                "lastModifiedDate": timestamp_z,  # Use consistent format
                "tags": tags or [],
                "type": "note",
                "deleted": False,
                "version": 1,
                "contentLength": len(body),
                "isShared": False,
                "hasAttachments": False,
                "format": "html",
                "encoding": "UTF-8",
                "status": "active"
            }

            # Make request with consistent API versions
            request_data = {
                "notes": [note_data]  # Let _make_request handle requestID and version
            }

            response = self._make_request(
                "post",
                "/no/content",
                data=request_data
            )

            if response and response.get("status", 0) == 0:
                self._notes_by_guid[note_guid] = note_data
                self.lists[collection].append(note_data)
                return note_guid

        except Exception as e:
            logger.error("Failed to create note: %s", str(e))
            raise

        return None

    def get_note(self, note_id: str) -> Optional[Dict]:
        """Get a note by its ID."""
        try:
            # Format the request with proper parameters
            request_data = {
                "requestID": str(uuid.uuid4()).upper(),
                "schema": "chunked:3",  # Updated schema version
                "_cloudKitVersion": "3",  # Updated CloudKit version
                "notes": [{
                    "identifier": note_id,
                    "noteGuid": note_id,
                    "type": "note",
                    "status": "active"
                }],
                "options": {
                    "includeContent": True,
                    "includeDeleted": False,
                    "includeShared": True
                }
            }

            response = self._make_request(
                "post",
                "/no/content",
                data=request_data,
                timeout=REQUEST_TIMEOUT
            )

            if response and 'notes' in response:
                note = response['notes'][0]
                note_data = {
                    "guid": note.get('identifier') or note.get('noteGuid'),
                    "title": note.get('title') or note.get('subject', ''),
                    "folderName": note.get('folderName', '/'),
                    "folderGuid": note.get('folderGuid'),
                    "size": note.get('contentLength') or note.get('size', 0),
                    "modified": note.get('lastModifiedDate'),
                    "content": note.get('content'),
                    "tags": note.get('tags', []),
                    "created": note.get('createdDate'),
                    "isShared": note.get('isShared', False),
                    "hasAttachments": note.get('hasAttachments', False),
                    "format": note.get('format', 'html'),
                    "encoding": note.get('encoding', 'UTF-8'),
                    "status": note.get('status', 'active'),
                    "version": note.get('version', 1)
                }
                # Update local cache
                self._notes_by_guid[note_id] = note_data
                if note_data['tags']:
                    self._tags.update(note_data['tags'])
                note_data["body"] = re.sub('<[^<]+?>', '', note_data.get("content", "")).strip()
                # Ensure the note data includes the expected "collection" key
                note_data["collection"] = note_data.get("folderName", "/")
                return note_data

            # Fallback to local cache if server request fails
            return self._notes_by_guid.get(note_id)

        except NonRetryableError as e:
            logger.error(f"Failed to get note: {str(e)}")
        except Exception as e:
            logger.error(f"Unexpected error getting note: {str(e)}")
            
        return None

    def update(self, note_id: str, title: Optional[str] = None,
              body: Optional[str] = None, tags: Optional[List[str]] = None) -> bool:
        """Update a note."""
        if note_id not in self._notes_by_guid:
            return False

        current = self._notes_by_guid[note_id]
        now = datetime.now()
        local_tz = pytz.timezone(get_localzone_name())
        now_local = now.astimezone(local_tz)

        # Format the content as HTML if body is provided
        content = None
        if body is not None:
            content = f'<html><head><meta charset="UTF-8"><meta name="apple-notes-version" content="3.0"><meta name="apple-notes-editable" content="true"></head><body style="word-wrap: break-word; -webkit-nbsp-mode: space; -webkit-line-break: after-white-space;">{body}</body></html>'

        update_data = {
            "requestID": str(uuid.uuid4()).upper(),
            "schema": "chunked:3",  # Updated schema version
            "_cloudKitVersion": "3",  # Updated CloudKit version
            "notes": [{
                "identifier": note_id,
                "noteGuid": note_id,
                "subject": title if title is not None else current.get("title"),
                "content": content if content is not None else current.get("content"),
                "folderName": current.get("folderName", "/"),
                "folderGuid": current.get("folderGuid"),
                "lastModifiedDate": now_local.strftime("%Y-%m-%dT%H:%M:%S.%f%z"),
                "tags": tags if tags is not None else current.get("tags", []),
                "type": "note",
                "deleted": False,
                "version": current.get("version", 1) + 1,
                "contentLength": len(content) if content is not None else current.get("contentLength", 0),
                "isShared": current.get("isShared", False),
                "hasAttachments": current.get("hasAttachments", False),
                "format": "html",
                "encoding": "UTF-8",
                "status": "active"
            }]
        }

        try:
            response = self._make_request(
                "post",
                "/no/content",
                data=update_data,
                timeout=REQUEST_TIMEOUT
            )

            if response and 'notes' in response:
                updated_note = response['notes'][0]
                # Update local cache
                current.update({
                    "title": update_data["notes"][0]["subject"],
                    "content": update_data["notes"][0]["content"],
                    "tags": update_data["notes"][0]["tags"],
                    "modified": now_local.strftime("%Y-%m-%dT%H:%M:%S.%f%z"),
                    "version": update_data["notes"][0]["version"],
                    "contentLength": update_data["notes"][0]["contentLength"],
                    "format": "html",
                    "encoding": "UTF-8",
                    "status": "active"
                })
                current["body"] = re.sub('<[^<]+?>', '', current.get("content", "")).strip()
                # Also add the "collection" key using the folderName from the current cache
                current["collection"] = current.get("folderName", "/")
                
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
            note = self._notes_by_guid[note_id]
            now = datetime.now()
            local_tz = pytz.timezone(get_localzone_name())
            now_local = now.astimezone(local_tz)
            
            delete_data = {
                "requestID": str(uuid.uuid4()).upper(),
                "schema": "chunked:3",  # Updated schema version
                "_cloudKitVersion": "3",  # Updated CloudKit version
                "notes": [{
                    "identifier": note_id,
                    "noteGuid": note_id,
                    "folderGuid": note.get("folderGuid"),
                    "deleted": True,
                    "status": "deleted",
                    "lastModifiedDate": now_local.strftime("%Y-%m-%dT%H:%M:%S.%f%z"),
                    "version": note.get("version", 1) + 1,
                    "type": "note"
                }]
            }

            response = self._make_request(
                "POST",
                "/no/content",
                data=delete_data,
                timeout=REQUEST_TIMEOUT
            )

            if response and 'notes' in response:
                # Update local cache
                note = self._notes_by_guid.pop(note_id)
                folder_name = note.get('folderName') or note.get('folder', '/')
                # Remove from folder's list
                self.lists[folder_name] = [
                    n for n in self.lists[folder_name] 
                    if n['guid'] != note_id
                ]
                return True

        except NonRetryableError as e:
            logger.error(f"Failed to delete note: {str(e)}")
        except Exception as e:
            logger.error(f"Unexpected error deleting note: {str(e)}")
            
        return False

    def get_notes_by_collection(self, collection: str) -> List[Dict]:
        """Get all notes in a collection."""
        try:
            # Get collection GUID from server data
            collection_data = self.collections.get(collection, {})
            if not collection_data:
                return []
            collection_guid = collection_data["guid"]

            # Format the request with proper parameters
            request_data = {
                "requestID": str(uuid.uuid4()).upper(),
                "schema": "chunked:3",  # Updated schema version
                "_cloudKitVersion": "3",  # Updated CloudKit version
                "folder": {
                    "identifier": collection_guid,
                    "folderGuid": collection_guid,
                    "name": collection,
                    "type": "folder",
                    "status": "active"
                },
                "options": {
                    "includeDeleted": False,
                    "includeShared": True,
                    "sortBy": "lastModifiedDate",
                    "sortOrder": "descending",
                    "maxResults": 1000
                }
            }

            response = self._make_request(
                "post",
                "/no/folder/notes",
                data=request_data,
                timeout=REQUEST_TIMEOUT
            )

            if response and 'notes' in response:
                results = []
                for note in response['notes']:
                    note_data = {
                        "guid": note.get('identifier') or note.get('noteGuid'),
                        "title": note.get('title') or note.get('subject', ''),
                        "folderName": collection,
                        "collection": collection,
                        "folderGuid": collection_guid,
                        "size": note.get('contentLength') or note.get('size', 0),
                        "modified": note.get('lastModifiedDate'),
                        "content": note.get('content'),
                        "tags": note.get('tags', []),
                        "created": note.get('createdDate'),
                        "isShared": note.get('isShared', False),
                        "hasAttachments": note.get('hasAttachments', False),
                        "format": note.get('format', 'html'),
                        "encoding": note.get('encoding', 'UTF-8'),
                        "status": note.get('status', 'active'),
                        "version": note.get('version', 1)
                    }
                    results.append(note_data)
                    
                    # Update local cache
                    self._notes_by_guid[note_data['guid']] = note_data
                    if note_data['tags']:
                        self._tags.update(note_data['tags'])
                        
                # Update local collection cache
                self.lists[collection] = results
                return results

            # Fallback to local cache if server request fails
            return self.lists.get(collection, [])

        except NonRetryableError as e:
            logger.error(f"Failed to get notes by collection: {str(e)}")
        except Exception as e:
            logger.error(f"Unexpected error getting notes by collection: {str(e)}")
            
        return []

    def search(self, query: str) -> List[Dict]:
        """Search notes."""
        try:
            # Format the search request with proper parameters
            search_data = {
                "requestID": str(uuid.uuid4()).upper(),
                "schema": "chunked:3",  # Updated schema version
                "_cloudKitVersion": "3",  # Updated CloudKit version
                "query": {
                    "text": query.lower(),
                    "fields": ["title", "content", "tags"],
                    "options": {
                        "matchWholeWords": False,
                        "caseSensitive": False,
                        "includeDeleted": False,
                        "includeShared": True,
                        "maxResults": 100
                    }
                }
            }

            response = self._make_request(
                "post",
                "/no/search",
                data=search_data,
                timeout=REQUEST_TIMEOUT
            )

            if response and 'notes' in response:
                results = []
                for note in response['notes']:
                    note_data = {
                        "guid": note.get('identifier') or note.get('noteGuid'),
                        "title": note.get('title') or note.get('subject', ''),
                        "folder": note.get('folderName', '/'),
                        "size": note.get('contentLength') or note.get('size', 0),
                        "modified": note.get('lastModifiedDate'),
                        "content": note.get('content'),
                        "tags": note.get('tags', []),
                        "created": note.get('createdDate'),
                        "isShared": note.get('isShared', False),
                        "hasAttachments": note.get('hasAttachments', False),
                        "format": note.get('format', 'html'),
                        "encoding": note.get('encoding', 'UTF-8'),
                        "status": note.get('status', 'active'),
                        "version": note.get('version', 1)
                    }
                    results.append(note_data)
                return results

            # Fallback to local search if server search fails
            query = query.lower()
            return [
                note for note in self._notes_by_guid.values()
                if query in note.get('title', '').lower()
                or query in note.get('content', '').lower()
                or any(query in tag.lower() for tag in note.get('tags', []))
            ]

        except NonRetryableError as e:
            logger.error(f"Failed to search notes: {str(e)}")
        except Exception as e:
            logger.error(f"Unexpected error searching notes: {str(e)}")
            
        return []

    def create_folder(self, name: str) -> bool:
        """Create a new folder in Notes."""
        try:
            now = datetime.now()
            local_tz = pytz.timezone(get_localzone_name())
            now_local = now.astimezone(local_tz)
            folder_id = str(uuid.uuid4()).upper()
            
            folder_data = {
                "requestID": str(uuid.uuid4()).upper(),
                "schema": "chunked:3",  # Updated schema version
                "_cloudKitVersion": "3",  # Updated CloudKit version
                "folder": {
                    "identifier": str(uuid.uuid4()).upper(),
                    "folderGuid": str(uuid.uuid4()).upper(),
                    "name": name,
                    "type": "folder",
                    "parentGuid": "root",
                    "order": len(self.collections),
                    "version": 1,
                    "isShared": False,
                    "status": "active",
                    "createdDate": now_local.strftime("%Y-%m-%dT%H:%M:%S.%f%z"),
                    "lastModifiedDate": now_local.strftime("%Y-%m-%dT%H:%M:%S.%f%z")
                }
            }

            response = self._make_request(
                "POST",
                "/no/folders",
                data=folder_data,
                timeout=REQUEST_TIMEOUT
            )

            if response and 'folder' in response:
                # Update local cache
                folder_guid = response['folder'].get('folderGuid')
                self.collections[name] = {
                    "guid": folder_guid,
                    "identifier": response['folder'].get('identifier'),
                    "name": name,
                    "ctag": response.get('syncToken', ''),
                    "type": "folder",
                    "parentGuid": "root",
                    "order": len(self.collections),
                    "version": 1,
                    "isShared": False,
                    "status": "active",
                    "createdDate": response['folder'].get('createdDate'),
                    "lastModifiedDate": response['folder'].get('lastModifiedDate')
                }
                self.lists[name] = []
                return True

        except NonRetryableError as e:
            logger.error(f"Failed to create folder: {str(e)}")
        except Exception as e:
            logger.error(f"Unexpected error creating folder: {str(e)}")
            
        return False 
