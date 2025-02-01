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
    """The 'Reminders' iCloud service with enhanced Chief of Staff features and performance optimizations."""

    def __init__(self, service_root, session, params):
        self.session = session
        self.params = params
        self.token_expiry = 0
        self._service_root = service_root
        self._reminders_endpoint = "%s/rd" % self._service_root
        self._reminders_startup_url = "%s/startup" % self._reminders_endpoint
        self._reminders_tasks_url = "%s/reminders/tasks" % self._reminders_endpoint
        self._batch_endpoint = "%s/batch" % self._reminders_tasks_url
        self._max_retries = 1  # Reduced from 3
        self._retry_delay = 1  # Reduced from 2
        self._batch_size = BATCH_SIZE
        self._pending_operations = []
        self._last_refresh = 0
        self._refresh_interval = 300  # 5 minutes
        
        # Initialize empty collections with better memory efficiency
        self.lists = defaultdict(list)
        self.collections = {}
        self._reminders_by_guid = {}
        self._tags = set()
        
        # Add service-specific headers
        self.session.headers.update({
            "Origin": "https://www.icloud.com",
            "Referer": "https://www.icloud.com/reminders/",
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "en-US,en;q=0.9",
            "X-Requested-With": "XMLHttpRequest",
            "X-Apple-Service": "reminders",
            "X-Apple-Auth-Token": session.service.session_data.get("session_token"),
            "X-Apple-Domain-Id": "reminders",
            "X-Apple-I-FD-Client-Info": "{\"app\":{\"name\":\"reminders\",\"version\":\"1.0\"}}",
            "X-Apple-App-Version": "1.0",
            "X-Apple-Web-Session-Token": session.service.session_data.get("session_token"),
            "Content-Type": "application/json",
        })

        # Add service-specific parameters
        self.params.update({
            "clientBuildNumber": "2020Project52",
            "clientMasteringNumber": "2020B29",
            "clientId": session.service.client_id,
            "dsid": session.service.data.get("dsInfo", {}).get("dsid"),
            "lang": "en-us",
            "usertz": get_localzone_name(),
            "remindersWebUIVersion": "1.0",
        })
        
        # Initial refresh
        self.refresh()

    def _authenticate_before_request(self) -> bool:
        """Only refresh auth token if expired."""
        now = time.time()
        if now < self.token_expiry:
            return True
            
        try:
            self.session.service.authenticate(True, "reminders")
            self.token_expiry = now + AUTH_TOKEN_EXPIRY
            return True
        except Exception as e:
            LOGGER.error("Failed to refresh auth token: %s", str(e))
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
        if not self._authenticate_before_request():
            raise NonRetryableError("Failed to authenticate")
            
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
            if response.status_code == 401 or \
               (response.status_code == 500 and "Authentication required" in response.text):
                # Auth error - let session handle it
                raise NonRetryableError("Authentication failed")
                
            elif response.status_code >= 400:
                # Other errors - non-retryable
                raise NonRetryableError(f"HTTP {response.status_code}: {response.text}")
                
            response.raise_for_status()
            return response
            
        except Exception as e:
            LOGGER.error(f"Request failed: {str(e)}")
            raise NonRetryableError(str(e))

    def refresh(self, force: bool = False) -> bool:
        """Refresh data with caching."""
        now = time.time()
        if not force and now - self._last_refresh < self._refresh_interval:
            return True
            
        response = self._make_request('get', "/rd/startup")
        if not response:
            return False

        try:
            data = response.json()
            
            # Clear existing data
            self.lists.clear()
            self.collections.clear()
            self._reminders_by_guid.clear()
            self._tags.clear()

            for collection in data.get("Collections", []):
                self.collections[collection["title"]] = {
                    "guid": collection["guid"],
                    "ctag": collection["ctag"],
                }
                
            for reminder in data.get("Reminders", []):
                collection_guid = reminder["pGuid"]
                collection_title = next(
                    (title for title, info in self.collections.items() 
                     if info["guid"] == collection_guid),
                    None
                )
                
                if not collection_title:
                    continue

                due_date = None
                if reminder.get("dueDate"):
                    try:
                        due_date = datetime(
                            reminder["dueDate"][1],
                            reminder["dueDate"][2],
                            reminder["dueDate"][3],
                            reminder["dueDate"][4],
                            reminder["dueDate"][5],
                        )
                    except (IndexError, ValueError) as e:
                        LOGGER.warning(f"Invalid due date for reminder {reminder['guid']}: {e}")

                reminder_data = {
                    "guid": reminder["guid"],
                    "title": reminder["title"],
                    "desc": reminder.get("description"),
                    "due": due_date,
                    "completed": reminder.get("completedDate") is not None,
                    "collection": collection_title,
                    "priority": reminder.get("priority", 0),
                    "tags": reminder.get("tags", []),
                    "p_guid": collection_guid,
                }
                
                self.lists[collection_title].append(reminder_data)
                self._reminders_by_guid[reminder["guid"]] = reminder_data
                self._tags.update(reminder_data["tags"])
                
            self._last_refresh = now
            return True
            
        except Exception as e:
            LOGGER.error("Failed to parse reminders data: %s", str(e))
            return False

    def _validate_collection(self, collection_name):
        """Validate collection exists or use first available"""
        if not self.lists:
            raise PyiCloudException("No reminder lists available")
            
        if collection_name not in self.lists:
            default_collection = next(iter(self.lists.keys()))
            LOGGER.warning(f"Using default collection {default_collection}")
            return default_collection
        return collection_name

    def post(self, title: str, description: str = "", collection: Optional[str] = None, 
             priority: int = Priority.NONE, tags: List[str] = None, 
             recurrence: Optional[str] = None, **kwargs) -> Optional[str]:
        """Create a new reminder with enhanced features."""
        try:
            collection = self._validate_collection(collection)
            pguid = self.collections[collection]["guid"] if collection in self.collections else "tasks"

            new_guid = str(uuid.uuid4())
            now = datetime.now(pytz.UTC)
            
            reminder_data = {
                "guid": new_guid,
                "title": title,
                "description": description or "",
                "pGuid": pguid,
                "etag": None,
                "order": 0,
                "priority": priority,
                "recurrence": self._format_recurrence(recurrence),
                "createdDateExtended": int(now.timestamp() * 1000),
                "lastModifiedDate": int(now.timestamp() * 1000),
                "dueDateIsAllDay": False,
                "tags": tags or [],
                "completed": False,
                "completedDate": None,
                "alarms": [],
                "recurrenceMaster": None,
                "startDate": None,
                "startDateTz": None,
                "startDateIsAllDay": False,
                "isFamily": False,
                "createdDate": [
                    int(f"{now.year}{now.month:02d}{now.day:02d}"),
                    now.year,
                    now.month,
                    now.day,
                    now.hour,
                    now.minute,
                    now.second
                ]
            }

            # Add due date if provided
            due_date = kwargs.get("due_date")
            if due_date:
                if not due_date.tzinfo:
                    local_tz = pytz.timezone('America/Los_Angeles')
                    due_date = local_tz.localize(due_date)
                utc_date = due_date.astimezone(pytz.UTC)
                reminder_data.update({
                    "dueDate": [
                        int(f"{utc_date.year}{utc_date.month:02d}{utc_date.day:02d}"),
                        utc_date.year,
                        utc_date.month,
                        utc_date.day,
                        utc_date.hour,
                        utc_date.minute,
                        utc_date.second
                    ],
                    "dueDateTz": "UTC"
                })

            # Make direct request instead of using batch operation for better error handling
            response = self._make_request(
                "post",
                "/rd/reminders/tasks",
                data={"Reminder": reminder_data},
                timeout=REQUEST_TIMEOUT
            )

            if response and response.status_code == 200:
                # Update local cache
                cache_data = {
                    "guid": new_guid,
                    "title": title,
                    "desc": description,
                    "due": due_date,
                    "completed": False,
                    "collection": collection,
                    "priority": priority,
                    "tags": tags or [],
                    "recurrence": recurrence,
                    "p_guid": pguid,
                }
                self._reminders_by_guid[new_guid] = cache_data
                self.lists[collection].append(cache_data)
                if tags:
                    self._tags.update(tags)
                return new_guid
                
        except NonRetryableError as e:
            LOGGER.error(f"Failed to create reminder: {str(e)}")
        except Exception as e:
            LOGGER.error(f"Unexpected error creating reminder: {str(e)}")
            
        return None

    def get_reminder(self, guid):
        """Get a reminder by its GUID."""
        return self._reminders_by_guid.get(guid)

    def update(self, guid: str, title: Optional[str] = None, 
               description: Optional[str] = None, due_date: Optional[datetime] = None,
               collection: Optional[str] = None, priority: Optional[int] = None,
               tags: Optional[List[str]] = None, recurrence: Optional[str] = None) -> bool:
        """Update a reminder with enhanced features."""
        if guid not in self._reminders_by_guid:
            return False

        current = self._reminders_by_guid[guid]
        pguid = current["p_guid"]
        
        if collection:
            collection = self._validate_collection(collection)
            if collection in self.collections:
                pguid = self.collections[collection]["guid"]

        update_data = {
            "guid": guid,
            "pGuid": pguid,
            "title": title if title is not None else current["title"],
            "description": description if description is not None else current.get("desc", ""),
            "priority": priority if priority is not None else current.get("priority", Priority.NONE),
            "tags": tags if tags is not None else current.get("tags", []),
            "recurrence": self._format_recurrence(recurrence) if recurrence else None,
            "lastModifiedDate": self._format_date(datetime.now()),
        }

        if due_date is not None:
            update_data.update(self._format_due_date(due_date))

        success = self._queue_operation(
            BatchOperation.UPDATE,
            update_data,
            immediate=True  # Force immediate update for better UX
        )

        if success:
            # Update local cache
            current.update({
                "title": update_data["title"],
                "desc": update_data["description"],
                "due": due_date if due_date is not None else current.get("due"),
                "priority": update_data["priority"],
                "tags": update_data["tags"],
                "recurrence": recurrence,
                "p_guid": pguid,
            })
            
            # Update collection if changed
            if collection and collection != current["collection"]:
                old_collection = current["collection"]
                self.lists[old_collection].remove(current)
                self.lists[collection].append(current)
                current["collection"] = collection
            
            # Update tags set
            if tags:
                self._tags.update(tags)
            
            return True
        return False

    def complete(self, guid: str) -> bool:
        """Mark a reminder as completed."""
        reminder = self.get_reminder(guid)
        if not reminder:
            return False

        complete_data = {
            "guid": guid,
            "pGuid": reminder["p_guid"],
            "title": reminder["title"],
            "completedDate": int(time.time() * 1000),
            "lastModifiedDate": self._format_date(datetime.now()),
        }

        success = self._queue_operation(
            BatchOperation.COMPLETE,
            complete_data,
            immediate=True
        )

        if success:
            reminder["completed"] = True
            return True
        return False

    def move_reminder(self, guid: str, target_collection: str) -> bool:
        """Move a reminder to a different collection/list."""
        if target_collection not in self.collections:
            LOGGER.error(f"Target collection {target_collection} does not exist")
            return False
            
        reminder = self.get_reminder(guid)
        if not reminder:
            return False
            
        return self.update(
            guid,
            title=reminder["title"],
            description=reminder.get("desc", ""),
            due_date=reminder.get("due"),
            collection=target_collection
        )

    def get_reminders_by_due_date(self, start_date: Optional[datetime] = None,
                                 end_date: Optional[datetime] = None,
                                 include_completed: bool = False) -> List[Dict]:
        """Get reminders due within a date range."""
        # Ensure dates are timezone-aware
        if start_date and not start_date.tzinfo:
            start_date = start_date.replace(tzinfo=pytz.UTC)
        if end_date and not end_date.tzinfo:
            end_date = end_date.replace(tzinfo=pytz.UTC)
        
        matching_reminders = []
        
        for reminders in self.lists.values():
            for reminder in reminders:
                if not include_completed and reminder.get("completed"):
                    continue
                    
                due_date = reminder.get("due")
                if not due_date:
                    continue
                    
                # Ensure reminder due date is timezone-aware
                if due_date and not due_date.tzinfo:
                    due_date = due_date.replace(tzinfo=pytz.UTC)
                    
                if start_date and due_date < start_date:
                    continue
                if end_date and due_date > end_date:
                    continue
                    
                matching_reminders.append(reminder)
                
        return sorted(matching_reminders, key=lambda x: x["due"])

    def get_reminders_by_collection(self, collection_name: str,
                                  include_completed: bool = False) -> List[Dict]:
        """Get all reminders in a specific collection."""
        if collection_name not in self.lists:
            return []
            
        reminders = self.lists[collection_name]
        if not include_completed:
            reminders = [r for r in reminders if not r.get("completed")]
            
        return reminders

    def get_upcoming_reminders(self, days: int = 7,
                             include_completed: bool = False) -> Dict[str, List[Dict]]:
        """Get reminders due in the next N days, grouped by collection."""
        start_date = datetime.now()
        end_date = start_date + timedelta(days=days)
        
        reminders_by_collection = defaultdict(list)
        
        for collection_name, reminders in self.lists.items():
            for reminder in reminders:
                if not include_completed and reminder.get("completed"):
                    continue
                    
                due_date = reminder.get("due")
                if not due_date:
                    continue
                    
                if start_date <= due_date <= end_date:
                    reminders_by_collection[collection_name].append(reminder)
                    
        return dict(reminders_by_collection)

    def batch_complete(self, guids: List[str]) -> Dict[str, bool]:
        """Complete multiple reminders efficiently."""
        results = {}
        operations = []
        
        for guid in guids:
            reminder = self.get_reminder(guid)
            if not reminder:
                results[guid] = False
                continue
                
            complete_data = {
                "guid": guid,
                "pGuid": reminder["p_guid"],
                "title": reminder["title"],
                "completedDate": int(time.time() * 1000),
                "lastModifiedDate": self._format_date(datetime.now()),
            }
            operations.append({
                "type": BatchOperation.COMPLETE,
                "data": complete_data
            })
            
        if operations:
            success = self._batch_request(operations)
            if success:
                for op in operations:
                    guid = op["data"]["guid"]
                    self._reminders_by_guid[guid]["completed"] = True
                    results[guid] = True
            else:
                for op in operations:
                    results[op["data"]["guid"]] = False
                    
        return results

    def batch_move(self, guids: List[str], target_collection: str) -> Dict[str, bool]:
        """Move multiple reminders to a target collection efficiently."""
        if target_collection not in self.collections:
            return {guid: False for guid in guids}
            
        results = {}
        operations = []
        target_pguid = self.collections[target_collection]["guid"]
        
        for guid in guids:
            reminder = self.get_reminder(guid)
            if not reminder:
                results[guid] = False
                continue
                
            move_data = {
                "guid": guid,
                "pGuid": target_pguid,
                "title": reminder["title"],
                "lastModifiedDate": self._format_date(datetime.now()),
            }
            operations.append({
                "type": BatchOperation.UPDATE,
                "data": move_data
            })
            
        if operations:
            success = self._batch_request(operations)
            if success:
                for op in operations:
                    guid = op["data"]["guid"]
                    reminder = self._reminders_by_guid[guid]
                    old_collection = reminder["collection"]
                    self.lists[old_collection].remove(reminder)
                    reminder["collection"] = target_collection
                    reminder["p_guid"] = target_pguid
                    self.lists[target_collection].append(reminder)
                    results[guid] = True
            else:
                for op in operations:
                    results[op["data"]["guid"]] = False
                    
        return results

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
