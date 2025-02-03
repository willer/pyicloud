"""Reminders service."""
from datetime import datetime, timedelta
import time
import uuid
import json
import logging
from tzlocal import get_localzone_name
from typing import List, Dict, Optional, Union, Tuple, Any
from collections import defaultdict
from pyicloud.exceptions import PyiCloudException
import pytz
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed

LOGGER = logging.getLogger(__name__)

# Constants for performance tuning
AUTH_TOKEN_EXPIRY = 3600  # 1 hour
BATCH_SIZE = 20
REQUEST_TIMEOUT = 30
MAX_BATCH_RETRIES = 3

class Priority:
    """Priority levels for reminders"""
    NONE = 0
    LOW = 1
    MEDIUM = 2
    HIGH = 3
    URGENT = 4

class RecurrenceType:
    """Recurrence types for reminders"""
    NONE = "none"
    DAILY = "daily"
    WEEKLY = "weekly"
    MONTHLY = "monthly"
    YEARLY = "yearly"

class BatchOperation:
    """Types of batch operations"""
    CREATE = "create"
    UPDATE = "update"
    DELETE = "delete"
    COMPLETE = "complete"

class RetryableError(Exception):
    """Errors that should trigger a retry."""
    pass

class NonRetryableError(Exception):
    """Errors that should fail immediately."""
    pass

class RemindersService:
    """The 'Reminders' iCloud service."""

    def __init__(self, service_root, session, params):
        """Initialize the reminders service.
        
        On macOS, this will use the native EventKit framework.
        On other platforms, it will use the iCloud web API.
        """
        self.session = session
        self.params = params
        self._service_root = service_root
        
        # Use EventKit on macOS
        if sys.platform == 'darwin':
            from .eventkit_reminders import EventKitRemindersService
            self._impl = EventKitRemindersService()
        else:
            # Fall back to web API implementation
            from .web_reminders import WebRemindersService
            self._impl = WebRemindersService(service_root, session, params)

    def refresh(self, force: bool = False) -> bool:
        """Refresh data from the implementation."""
        return self._impl.refresh(force)

    def post(self, title: str, description: str = "", collection: Optional[str] = None,
             priority: int = Priority.NONE, tags: List[str] = None,
             due_date: Optional[datetime] = None, **kwargs) -> Optional[str]:
        """Create a new reminder."""
        return self._impl.post(title, description, collection, priority, tags, due_date, **kwargs)

    def get_reminder(self, guid: str) -> Optional[Dict]:
        """Get a reminder by its GUID."""
        return self._impl.get_reminder(guid)

    def update(self, guid: str, title: Optional[str] = None,
               description: Optional[str] = None, due_date: Optional[datetime] = None,
               collection: Optional[str] = None, priority: Optional[int] = None,
               tags: Optional[List[str]] = None) -> bool:
        """Update a reminder."""
        return self._impl.update(guid, title, description, due_date, collection, priority, tags)

    def complete(self, guid: str) -> bool:
        """Mark a reminder as completed."""
        return self._impl.complete(guid)

    def get_reminders_by_collection(self, collection_name: str,
                                  include_completed: bool = False) -> List[Dict]:
        """Get all reminders in a specific collection."""
        return self._impl.get_reminders_by_collection(collection_name, include_completed)

    def get_reminders_by_due_date(self, start_date: Optional[datetime] = None,
                                 end_date: Optional[datetime] = None,
                                 include_completed: bool = False) -> List[Dict]:
        """Get reminders due within a date range."""
        return self._impl.get_reminders_by_due_date(start_date, end_date, include_completed)

    def get_upcoming_reminders(self, days: int = 7,
                             include_completed: bool = False) -> Dict[str, List[Dict]]:
        """Get reminders due in the next N days, grouped by collection."""
        return self._impl.get_upcoming_reminders(days, include_completed)

    def move_reminder(self, guid: str, target_collection: str) -> bool:
        """Move a reminder to a different collection."""
        return self._impl.move_reminder(guid, target_collection)

    def batch_complete(self, guids: List[str]) -> Dict[str, bool]:
        """Complete multiple reminders in batch."""
        return self._impl.batch_complete(guids)

    def batch_move(self, guids: List[str], target_collection: str) -> Dict[str, bool]:
        """Move multiple reminders to a different collection in batch."""
        return self._impl.batch_move(guids, target_collection)

    @property
    def lists(self):
        """Get all reminder lists."""
        return self._impl.lists

    def _authenticate_before_request(self) -> bool:
        """Only refresh auth token if expired."""
        now = time.time()
        if now < self.token_expiry:
            return True
            
        try:
            # Force authentication refresh for reminders service
            self.session.service.authenticate(True, "reminders")
            
            # Update headers with new tokens
            self.session.headers.update({
                "Origin": "https://www.icloud.com",
                "Referer": "https://www.icloud.com/reminders/",
                "Accept": "application/json, text/plain, */*",
                "Accept-Language": "en-US,en;q=0.9",
                "X-Requested-With": "XMLHttpRequest",
                "X-Apple-Service": "reminders",
                "X-Apple-Auth-Token": self.session.service.session_data.get("session_token"),
                "X-Apple-Domain-Id": "reminders",
                "X-Apple-I-FD-Client-Info": "{\"app\":{\"name\":\"reminders\",\"version\":\"2.0\"}}",
                "X-Apple-App-Version": "2.0",
                "X-Apple-Web-Session-Token": self.session.service.session_data.get("session_token"),
                "Content-Type": "application/json",
                "X-Apple-I-TimeZone": get_localzone_name(),
                "X-Apple-I-ClientTime": datetime.now().strftime("%Y-%m-%dT%H:%M:%SZ"),
            })
            
            # Update service-specific parameters
            self.params.update({
                "clientBuildNumber": "2023Project70",
                "clientMasteringNumber": "2023B70",
                "clientId": self.session.service.client_id,
                "dsid": self.session.service.data.get("dsInfo", {}).get("dsid"),
                "lang": "en-us",
                "usertz": get_localzone_name(),
                "remindersWebUIVersion": "2.0",
            })
            
            self.token_expiry = now + AUTH_TOKEN_EXPIRY
            return True
            
        except Exception as e:
            LOGGER.error("Failed to refresh auth token: %s", str(e))
            # Add exponential backoff for auth failures
            retry_after = min(int(time.time() - self.token_expiry), 30)  # Cap at 30 seconds
            time.sleep(retry_after)
            return False

    def _batch_request(self, operations: List[Dict[str, Any]], force: bool = False) -> bool:
        """Execute batch operations efficiently."""
        if not operations and not force:
            return True
            
        if not operations and force:
            operations = self._pending_operations
            self._pending_operations = []
            
        if not operations:
            return True
            
        try:
            for i in range(0, len(operations), self._batch_size):
                batch = operations[i:i + self._batch_size]
                response = self._make_request(
                    'post',
                    '/rd/reminders/tasks/batch',
                    data={'operations': batch},
                    timeout=REQUEST_TIMEOUT
                )
                
                if not response or response.status_code != 200:
                    LOGGER.error("Batch operation failed: %s", response.text if response else "No response")
                    return False
                    
                # Small delay between batches to avoid rate limiting
                if i + self._batch_size < len(operations):
                    time.sleep(0.5)
                    
            return True
            
        except Exception as e:
            LOGGER.error("Batch operation failed: %s", str(e))
            return False

    def _queue_operation(self, op_type: str, data: Dict[str, Any], immediate: bool = False) -> bool:
        """Queue an operation for batch processing."""
        operation = {
            'type': op_type,
            'data': data,
            'timestamp': time.time()
        }
        
        self._pending_operations.append(operation)
        
        if immediate or len(self._pending_operations) >= self._batch_size:
            return self._batch_request(self._pending_operations, force=True)
            
        return True

    def _make_request(self, method: str, endpoint: str, data: Optional[Dict] = None,
                     params: Optional[Dict] = None, timeout: int = REQUEST_TIMEOUT) -> Optional[Any]:
        """Make an authenticated request with minimal retries."""
        max_retries = 3
        retry_count = 0
        last_error = None
        
        while retry_count < max_retries:
            if not self._authenticate_before_request():
                retry_count += 1
                if retry_count == max_retries:
                    raise NonRetryableError("Failed to authenticate after multiple attempts")
                continue
                
            try:
                LOGGER.debug(f"Making {method} request to {endpoint}")
                request_params = {**self.params, **(params or {})}
                
                if method.lower() == 'get':
                    response = self.session.get(
                        f"{self._service_root}{endpoint}",
                        params=request_params,
                        timeout=timeout
                    )
                else:
                    response = self.session.post(
                        f"{self._service_root}{endpoint}",
                        data=json.dumps(data) if data else None,
                        params=request_params,
                        timeout=timeout
                    )
                    
                # Handle different error cases
                if response.status_code == 401:
                    LOGGER.debug("Got 401, attempting auth refresh")
                    self.token_expiry = 0  # Force auth refresh
                    retry_count += 1
                    continue
                    
                elif response.status_code == 500 and "Authentication required" in response.text:
                    LOGGER.debug("Got auth required error, attempting auth refresh")
                    self.token_expiry = 0  # Force auth refresh
                    retry_count += 1
                    continue
                    
                elif response.status_code == 503:
                    # Service unavailable - retry with backoff
                    retry_after = min(int(response.headers.get('Retry-After', 2)), 5)  # Cap at 5 seconds
                    LOGGER.warning("Got 503, waiting %d seconds before retry", retry_after)
                    time.sleep(retry_after)
                    retry_count += 1
                    continue
                    
                elif response.status_code >= 400:
                    # Other errors - non-retryable
                    LOGGER.error("Got error status %d: %s", 
                               response.status_code,
                               response.text if response.text else "No error message")
                    raise NonRetryableError(f"HTTP {response.status_code}: {response.text}")
                    
                response.raise_for_status()
                return response.json()
                
            except Exception as e:
                last_error = e
                LOGGER.error(f"Request failed: {str(e)}")
                retry_count += 1
                if retry_count < max_retries:
                    time.sleep(2 ** retry_count)  # Exponential backoff
                    continue
                raise NonRetryableError(str(e))
                
        if last_error:
            raise NonRetryableError(f"Max retries exceeded: {str(last_error)}")
        return None

    def _validate_collection(self, collection_name):
        """Validate collection exists or use first available"""
        if not self.lists:
            raise PyiCloudException("No reminder lists available")
            
        if collection_name not in self.lists:
            default_collection = next(iter(self.lists.keys()))
            LOGGER.warning(f"Using default collection {default_collection}")
            return default_collection
        return collection_name

    def _format_due_date(self, due_date):
        """Format a datetime object for the API."""
        if not due_date:
            return None
            
        if not due_date.tzinfo:
            # Assume local timezone if not specified
            local_tz = pytz.timezone('America/Los_Angeles')  # Adjust as needed
            due_date = local_tz.localize(due_date)
            
        # Convert to UTC for API
        utc_date = due_date.astimezone(pytz.UTC)
        
        # Format as required by the API
        return {
            "dueDate": [
                int(f"{utc_date.year}{utc_date.month:02d}{utc_date.day:02d}"),
                utc_date.year,
                utc_date.month,
                utc_date.day,
                utc_date.hour,
                utc_date.minute,
                utc_date.second
            ],
            "dueDateIsAllDay": False,
            "dueDateTz": "UTC"
        }

    def get_reminders_by_priority(self, min_priority: int = Priority.NONE,
                                include_completed: bool = False) -> List[Dict]:
        """Get reminders filtered by minimum priority level."""
        reminders = []
        for collection in self.lists.values():
            for reminder in collection:
                if (reminder.get("priority", Priority.NONE) >= min_priority and
                    (include_completed or not reminder["completed"])):
                    reminders.append(reminder)
        return sorted(reminders, key=lambda x: (-x.get("priority", Priority.NONE),
                                              x.get("due") or datetime.max))

    def get_reminders_by_tags(self, tags: List[str], match_all: bool = False,
                            include_completed: bool = False) -> List[Dict]:
        """Get reminders that match specified tags."""
        reminders = []
        tags = set(tags)
        for collection in self.lists.values():
            for reminder in collection:
                reminder_tags = set(reminder.get("tags", []))
                if ((match_all and tags.issubset(reminder_tags)) or
                    (not match_all and tags.intersection(reminder_tags)) and
                    (include_completed or not reminder["completed"])):
                    reminders.append(reminder)
        return reminders

    def get_all_tags(self) -> List[str]:
        """Get all unique tags used across reminders."""
        return sorted(list(self._tags))

    def _format_recurrence(self, recurrence_type: str) -> Optional[Dict]:
        """Format recurrence rule for a reminder."""
        if not recurrence_type or recurrence_type == RecurrenceType.NONE:
            return None
            
        recurrence_rules = {
            RecurrenceType.DAILY: {"freq": "DAILY"},
            RecurrenceType.WEEKLY: {"freq": "WEEKLY"},
            RecurrenceType.MONTHLY: {"freq": "MONTHLY"},
            RecurrenceType.YEARLY: {"freq": "YEARLY"}
        }
        
        return recurrence_rules.get(recurrence_type)

    def _format_date(self, date: datetime) -> List[int]:
        """Format a datetime object for the API."""
        if not date:
            return None
            
        if not date.tzinfo:
            local_tz = pytz.timezone('America/Los_Angeles')
            date = local_tz.localize(date)
            
        utc_date = date.astimezone(pytz.UTC)
        return [
            int(f"{utc_date.year}{utc_date.month:02d}{utc_date.day:02d}"),
            utc_date.year,
            utc_date.month,
            utc_date.day,
            utc_date.hour,
            utc_date.minute,
            utc_date.second
        ]
