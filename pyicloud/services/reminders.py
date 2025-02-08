"""Reminders service."""
from datetime import datetime, timedelta
import time
import uuid
import json
import logging
from tzlocal import get_localzone_name
from typing import List, Dict, Optional, Union, Tuple, Any
from collections import defaultdict
from pyicloud.exceptions import PyiCloudException, PyiCloudAPIResponseException
import pytz
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed

if sys.platform == 'darwin':
    from Foundation import (
        NSDate, NSDateComponents, NSCalendar,
        NSCalendarUnitYear, NSCalendarUnitMonth, NSCalendarUnitDay,
        NSCalendarUnitHour, NSCalendarUnitMinute, NSCalendarUnitSecond,
        NSError
    )
    from EventKit import (
        EKEventStore, EKReminder, EKCalendar,
        EKEntityTypeReminder, EKSpan
    )

LOGGER = logging.getLogger(__name__)

class WebRemindersService:
    """iCloud web API implementation of reminders."""
    
    def __init__(self, service_root, session, params):
        self.session = session
        self.params = params
        self._service_root = service_root
        self.refresh()
        
    def refresh(self, force=False):
        """Refresh from iCloud."""
        return True

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

class EventKitRemindersService:
    """Native macOS Reminders implementation using EventKit"""
    
    def __init__(self):
        self.store = EKEventStore.alloc().init()
        self._verify_authorization()
        self._calendars = None
        self.refresh()
        
    def _verify_authorization(self):
        """Verify we have permission to access Reminders."""
        auth_status = EKEventStore.authorizationStatusForEntityType_(EKEntityTypeReminder)
        if auth_status == 0:  # Not determined
            success = self.store.requestAccessToEntityType_completion_(
                EKEntityTypeReminder,
                lambda granted, error: None
            )
            if not success:
                raise PyiCloudException("Failed to request Reminders access")
        elif auth_status != 3:  # 3 = Authorized
            raise PyiCloudException(
                "Reminder access not authorized. Please enable in System Preferences."
            )

    def _calendar_for_name(self, name):
        """Get calendar by name."""
        calendars = self.store.calendarsForEntityType_(EKEntityTypeReminder)
        for calendar in calendars:
            if calendar.title() == name:
                return calendar
        return None

    def refresh(self, force=False):
        """Refresh calendars from EventKit."""
        self._calendars = self.store.calendarsForEntityType_(EKEntityTypeReminder)
        return True

    @property
    def lists(self):
        """Get all reminder lists with their reminders."""
        if not self._calendars:
            self.refresh()
        
        result = {}
        for calendar in self._calendars:
            # Get reminders for this calendar
            predicate = self.store.predicateForRemindersInCalendars_([calendar])
            reminders = []
            result_array = [None]
            
            def completion_handler(fetched_reminders):
                result_array[0] = fetched_reminders
            
            self.store.fetchRemindersMatchingPredicate_completion_(
                predicate,
                completion_handler
            )
            
            # Wait for results
            while result_array[0] is None:
                pass
                
            if result_array[0]:
                for reminder in result_array[0]:
                    reminders.append(self._convert_reminder_to_dict(reminder))
            
            result[calendar.title()] = reminders
            
        return result

    def get_reminder(self, guid: str) -> Optional[Dict]:
        """Get a reminder by its GUID."""
        reminder = self.store.calendarItemWithIdentifier_(guid)
        if reminder:
            return self._convert_reminder_to_dict(reminder)
        return None

    def _convert_reminder_to_dict(self, reminder: EKReminder) -> Dict:
        """Convert an EKReminder object to our standard dictionary format."""
        result = {
            'guid': str(reminder.calendarItemIdentifier()),
            'title': str(reminder.title()),
            'desc': str(reminder.notes()) if reminder.notes() else '',
            'completed': bool(reminder.completionDate()),
            'collection': str(reminder.calendar().title()),
            'priority': int(reminder.priority()) if reminder.priority() else 0,
            'p_guid': str(reminder.calendar().calendarIdentifier())
        }

        if reminder.dueDateComponents():
            components = reminder.dueDateComponents()
            date = NSCalendar.currentCalendar().dateFromComponents_(components)
            if date:
                # Convert timestamp to datetime in UTC
                dt = datetime.utcfromtimestamp(date.timeIntervalSince1970())
                # Ensure timezone is set to UTC
                result['due'] = pytz.UTC.localize(dt)

        return result

    def post(self, title: str, description: str = "", collection: Optional[str] = None,
             priority: int = 0, tags: List[str] = None,
             due_date: Optional[datetime] = None) -> Optional[str]:
        """Create a new reminder.
        
        Args:
            title: Title of the reminder
            description: Optional description
            collection: Optional collection name (defaults to first available)
            priority: Optional priority level (0-4)
            tags: Optional list of tags
            due_date: Optional due date
            
        Returns:
            str: GUID of created reminder if successful
            
        Raises:
            PyiCloudException: If creation fails for any reason
        """
        try:
            # Validate and format due date if provided
            if due_date is not None:
                if not isinstance(due_date, datetime):
                    raise PyiCloudException("due_date must be a datetime object")
                if due_date.tzinfo is None:
                    # Localize naive datetime to UTC
                    due_date = pytz.UTC.localize(due_date)

            # Validate collection
            collection = self._validate_collection(collection)
            calendar = self._calendar_for_name(collection)
            if not calendar:
                raise PyiCloudException(f"Calendar not found: {collection}")

            # Create the reminder
            reminder = EKReminder.reminderWithEventStore_(self.store)
            reminder.setTitle_(title)
            reminder.setNotes_(description)
            reminder.setPriority_(priority)
            reminder.setCalendar_(calendar)

            if due_date:
                if due_date.tzinfo is not None:
                    due_date = due_date.astimezone(pytz.UTC)
                components = NSDateComponents.alloc().init()
                components.setYear_(due_date.year)
                components.setMonth_(due_date.month)
                components.setDay_(due_date.day)
                components.setHour_(due_date.hour)
                components.setMinute_(due_date.minute)
                components.setSecond_(due_date.second)
                reminder.setDueDateComponents_(components)

            # Save the reminder
            success, error = self.store.saveReminder_commit_error_(reminder, True, None)
            if not success:
                error_msg = str(error) if error else "Unknown error"
                raise PyiCloudException(f"Failed to save reminder: {error_msg}")

            return str(reminder.calendarItemIdentifier())

        except Exception as e:
            LOGGER.error("Failed to create reminder: %s", str(e))
            raise PyiCloudException(f"Failed to create reminder: {str(e)}")

    def update(self, guid: str, title: Optional[str] = None,
               description: Optional[str] = None, due_date: Optional[datetime] = None,
               collection: Optional[str] = None, priority: Optional[int] = None) -> bool:
        """Update a reminder."""
        reminder = self.store.calendarItemWithIdentifier_(guid)
        if not reminder:
            return False

        try:
            if title is not None:
                reminder.setTitle_(title)
            if description is not None:
                reminder.setNotes_(description)
            if priority is not None:
                reminder.setPriority_(priority)

            if collection:
                calendar = self._calendar_for_name(collection)
                if calendar:
                    reminder.setCalendar_(calendar)

            if due_date:
                if not isinstance(due_date, datetime):
                    raise PyiCloudException("due_date must be a datetime object")
                if due_date.tzinfo is None:
                    # Localize naive datetime to UTC
                    due_date = pytz.UTC.localize(due_date)
                else:
                    # Convert to UTC if in another timezone
                    due_date = due_date.astimezone(pytz.UTC)

                components = NSDateComponents.alloc().init()
                components.setYear_(due_date.year)
                components.setMonth_(due_date.month)
                components.setDay_(due_date.day)
                components.setHour_(due_date.hour)
                components.setMinute_(due_date.minute)
                components.setSecond_(due_date.second)
                reminder.setDueDateComponents_(components)

            success, error = self.store.saveReminder_commit_error_(reminder, True, None)
            if not success:
                LOGGER.error(f"Failed to update reminder: {error}")
            return success

        except Exception as e:
            LOGGER.error(f"Failed to update reminder: {str(e)}")
            return False

    def complete(self, guid: str) -> bool:
        """Mark a reminder as completed."""
        reminder = self.store.calendarItemWithIdentifier_(guid)
        if not reminder:
            return False

        try:
            reminder.setCompleted_(True)
            success, error = self.store.saveReminder_commit_error_(reminder, True, None)
            if not success:
                LOGGER.error(f"Failed to complete reminder: {error}")
            return success

        except Exception as e:
            LOGGER.error(f"Failed to complete reminder: {str(e)}")
            return False

    def get_reminders_by_due_date(self, start_date: Optional[datetime] = None,
                                 end_date: Optional[datetime] = None,
                                 include_completed: bool = False) -> List[Dict]:
        """Get reminders due within a date range."""
        if not start_date:
            start_date = datetime.now()
        if not end_date:
            end_date = start_date + timedelta(days=1)

        # Create a predicate for the date range
        calendar = NSCalendar.currentCalendar()
        start_components = NSDateComponents.alloc().init()
        start_components.setYear_(start_date.year)
        start_components.setMonth_(start_date.month)
        start_components.setDay_(start_date.day)
        start_components.setHour_(start_date.hour)
        start_components.setMinute_(start_date.minute)
        start_components.setSecond_(start_date.second)

        end_components = NSDateComponents.alloc().init()
        end_components.setYear_(end_date.year)
        end_components.setMonth_(end_date.month)
        end_components.setDay_(end_date.day)
        end_components.setHour_(end_date.hour)
        end_components.setMinute_(end_date.minute)
        end_components.setSecond_(end_date.second)

        start_date_ns = calendar.dateFromComponents_(start_components)
        end_date_ns = calendar.dateFromComponents_(end_components)

        predicate = self.store.predicateForIncompleteRemindersWithDueDateStarting_ending_calendars_(
            start_date_ns,
            end_date_ns,
            self._calendars
        )

        reminders = []
        result_array = [None]

        def completion_handler(fetched_reminders):
            result_array[0] = fetched_reminders

        self.store.fetchRemindersMatchingPredicate_completion_(
            predicate,
            completion_handler
        )

        # Wait for results
        while result_array[0] is None:
            pass

        if result_array[0]:
            for reminder in result_array[0]:
                if include_completed or not reminder.completionDate():
                    reminders.append(self._convert_reminder_to_dict(reminder))

        return reminders

    def get_reminders_by_collection(self, collection: str,
                                  include_completed: bool = False) -> List[Dict]:
        """Get all reminders in a collection."""
        calendar = self._calendar_for_name(collection)
        if not calendar:
            return []

        predicate = self.store.predicateForRemindersInCalendars_([calendar])
        reminders = []
        result_array = [None]

        def completion_handler(fetched_reminders):
            result_array[0] = fetched_reminders

        self.store.fetchRemindersMatchingPredicate_completion_(
            predicate,
            completion_handler
        )

        # Wait for results
        while result_array[0] is None:
            pass

        if result_array[0]:
            for reminder in result_array[0]:
                if include_completed or not reminder.completionDate():
                    reminders.append(self._convert_reminder_to_dict(reminder))

        return reminders

    def move_reminder(self, guid: str, target_collection: str) -> bool:
        """Move a reminder to a different collection."""
        reminder = self.store.calendarItemWithIdentifier_(guid)
        if not reminder:
            return False

        calendar = self._calendar_for_name(target_collection)
        if not calendar:
            return False

        try:
            reminder.setCalendar_(calendar)
            success, error = self.store.saveReminder_commit_error_(reminder, True, None)
            if not success:
                LOGGER.error(f"Failed to move reminder: {error}")
            return success

        except Exception as e:
            LOGGER.error(f"Failed to move reminder: {str(e)}")
            return False

    def batch_complete(self, guids: List[str]) -> Dict[str, bool]:
        """Complete multiple reminders."""
        results = {}
        for guid in guids:
            results[guid] = self.complete(guid)
        return results

    def batch_move(self, guids: List[str], target_collection: str) -> Dict[str, bool]:
        """Move multiple reminders to a different collection."""
        results = {}
        for guid in guids:
            results[guid] = self.move_reminder(guid, target_collection)
        return results

    def get_upcoming_reminders(self, days: int = 7,
                             include_completed: bool = False) -> Dict[str, List[Dict]]:
        """Get upcoming reminders grouped by collection."""
        start_date = datetime.now()
        end_date = start_date + timedelta(days=days)

        reminders = self.get_reminders_by_due_date(start_date, end_date)
        result = defaultdict(list)
        for reminder in reminders:
            result[reminder['collection']].append(reminder)
        return dict(result)

    def _validate_collection(self, collection: Optional[str]) -> str:
        """Validate and return a collection name.
        
        Args:
            collection: Optional collection name
            
        Returns:
            str: Valid collection name (defaults to first available)
            
        Raises:
            PyiCloudException: If no valid collection is available
        """
        if not self._calendars:
            self.refresh()
            
        if not self._calendars:
            raise PyiCloudException("No reminder lists available")
            
        # If no collection specified, use first available
        if not collection:
            LOGGER.warning("Using default collection %s", self._calendars[0].title())
            return self._calendars[0].title()
            
        # Verify collection exists
        for calendar in self._calendars:
            if calendar.title() == collection:
                return collection
                
        # Collection not found, use first available
        LOGGER.warning(
            "Collection %s not found, using default collection %s",
            collection,
            self._calendars[0].title()
        )
        return self._calendars[0].title()

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
        self.token_expiry = 0  # Initialize token expiry
        
        # Use EventKit on macOS
        if sys.platform == 'darwin':
            self._impl = EventKitRemindersService()
        else:
            # Fall back to web API implementation
            self._impl = WebRemindersService(service_root, session, params)

    def refresh(self, force: bool = False) -> bool:
        """Refresh data from the implementation."""
        return self._impl.refresh(force)

    def post(self, title: str, description: str = "", collection: Optional[str] = None,
             priority: int = 0, tags: List[str] = None,
             due_date: Optional[datetime] = None) -> Optional[str]:
        """Create a new reminder.
        
        Args:
            title: Title of the reminder
            description: Optional description
            collection: Optional collection name (defaults to first available)
            priority: Optional priority level (0-4)
            tags: Optional list of tags
            due_date: Optional due date
            
        Returns:
            str: GUID of created reminder if successful
            
        Raises:
            PyiCloudException: If creation fails for any reason
        """
        try:
            # Validate and format due date if provided
            if due_date is not None:
                if not isinstance(due_date, datetime):
                    raise PyiCloudException("due_date must be a datetime object")
                if due_date.tzinfo is None:
                    # Localize naive datetime to UTC
                    due_date = pytz.UTC.localize(due_date)

            # Validate collection
            collection = self._validate_collection(collection)

            # Create the reminder using implementation
            guid = self._impl.post(title, description, collection, priority, tags, due_date)
            if guid is None:
                raise PyiCloudException("Failed to create reminder - no GUID returned")
            return guid

        except Exception as e:
            LOGGER.error("Failed to create reminder: %s", str(e))
            raise PyiCloudException(f"Failed to create reminder: {str(e)}")

    def get_reminder(self, guid: str) -> Optional[Dict]:
        """Get a reminder by its GUID."""
        return self._impl.get_reminder(guid)

    def update(self, guid: str, title: Optional[str] = None,
               description: Optional[str] = None, due_date: Optional[datetime] = None,
               collection: Optional[str] = None, priority: Optional[int] = None) -> bool:
        """Update a reminder."""
        return self._impl.update(guid, title, description, due_date, collection, priority)

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
        return self._impl.get_upcoming_reminders(days=days, include_completed=include_completed)

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
                
                url = f"{self._service_root}{endpoint}"
                LOGGER.debug("Making request - URL: %s, method: %s, params: %s, data: %s",
                           url, method, request_params, data)
                LOGGER.debug("Auth token: %s", self.session.service.session_data.get("session_token"))

                if method.lower() == 'get':
                    response = self.session.get(
                        url,
                        params=request_params,
                        timeout=timeout
                    )
                else:
                    response = self.session.post(
                        url,
                        json=data,  # Changed to use json parameter
                        params=request_params,
                        timeout=timeout
                    )
                
                LOGGER.debug("Response status: %d", response.status_code)
                LOGGER.debug("Response headers: %s", response.headers)
                LOGGER.debug("Response body: %s", response.text)
                
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
                LOGGER.error("Request failed - URL: %s, method: %s, params: %s, error: %s",
                           url, method, request_params, str(e))
                retry_count += 1
                if retry_count < max_retries:
                    time.sleep(2 ** retry_count)  # Exponential backoff
                    continue
                raise NonRetryableError(f"Request failed after {max_retries} retries: {str(e)}")
                
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

    def create_list(self, name: str, color: Optional[str] = None) -> bool:
        """Create a new reminder list.
        
        Args:
            name: The name of the list to create
            color: Optional color for the list (hex code or name)
            
        Returns:
            bool: True if list was created successfully, False otherwise
        """
        try:
            if not name:
                LOGGER.error("List name cannot be empty")
                return False

            # Format the request data according to iCloud API requirements
            list_data = {
                "Collection": {
                    "title": name,
                    "type": "com.apple.reminders.list",
                    "color": color if color else "#FF9500",  # Default orange color if none specified
                    "createdDate": int(time.time() * 1000),  # Current time in milliseconds
                    "enabled": True
                }
            }

            # Make the request
            for _ in range(3):  # Try up to 3 times
                try:
                    response = self._make_request(
                        "POST",
                        "/rl/collections",
                        data=list_data
                    )

                    if response and isinstance(response, dict) and response.get("Collection"):
                        # Update local cache
                        self.lists[name] = []
                        return True

                    LOGGER.error("Failed to create list, response: %s", response)
                    return False

                except RetryableError:
                    continue
                except NonRetryableError as e:
                    LOGGER.error("Non-retryable error creating list: %s", str(e))
                    return False

            LOGGER.error("Failed to create list after 3 retries")
            return False

        except Exception as e:
            LOGGER.error("Failed to create list: %s", str(e))
            return False
