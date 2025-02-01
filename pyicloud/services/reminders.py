"""Reminders service."""
from datetime import datetime, timedelta
import time
import uuid
import json
import logging
from tzlocal import get_localzone_name
from typing import List, Dict, Optional, Union, Tuple
from collections import defaultdict
from pyicloud.exceptions import PyiCloudException
import pytz
from concurrent.futures import ThreadPoolExecutor, as_completed

LOGGER = logging.getLogger(__name__)

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

class RemindersService:
    """The 'Reminders' iCloud service with enhanced Chief of Staff features."""

    def __init__(self, service_root, session, params):
        self.session = session
        self.params = params
        self.token_expiry = 0  # Track token expiration
        self._service_root = service_root
        self._reminders_endpoint = "%s/rd" % self._service_root
        self._reminders_startup_url = "%s/startup" % self._reminders_endpoint
        self._reminders_tasks_url = "%s/reminders/tasks" % self._reminders_endpoint
        self._max_retries = 3  # Increased from 2
        self._retry_delay = 2
        self._batch_size = 50  # Maximum number of reminders to process in a batch
        
        # Initialize empty collections
        self.lists = {}
        self.collections = {}
        self._reminders_by_guid = {}
        self._tags = set()  # Track all unique tags
        
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
        
        # Now try to refresh
        self.refresh()

    def _authenticate_before_request(self):
        """Refresh auth token before each request"""
        try:
            self.session.service.authenticate(True, "reminders")
            LOGGER.debug("Refreshed reminders service auth token")
        except Exception as e:
            LOGGER.error("Failed to refresh auth token: %s", str(e))
            return False
        return True

    def _make_request(self, method, endpoint, data=None, params=None, retries=None):
        """Make an authenticated request to the reminders service."""
        if not self._authenticate_before_request():
            return None
        
        if retries is None:
            retries = self._max_retries
            
        if retries <= 0:
            LOGGER.error("Max retries exceeded")
            return None
            
        try:
            LOGGER.debug(f"Making {method} request to {endpoint} (retries left: {retries})")
            # Update parameters for this request
            request_params = dict(self.params)
            if params:
                request_params.update(params)
                
            # Make request
            if method.lower() == 'get':
                response = self.session.get(
                    f"{self._service_root}{endpoint}",
                    params=request_params
                )
            else:  # POST
                LOGGER.debug(f"POST data: {data}")
                response = self.session.post(
                    f"{self._service_root}{endpoint}",
                    data=json.dumps(data) if data else None,
                    params=request_params
                )
                
            LOGGER.debug(f"Response status: {response.status_code}")
            
            # Only retry on specific status codes, not 500
            if response.status_code in (401, 421, 503):
                error_msg = response.text if response.text else f"HTTP {response.status_code}"
                LOGGER.warning(f"Got status {response.status_code}: {error_msg}")
                
                if "Authentication required" in error_msg or response.status_code in (401, 421):
                    LOGGER.info(f"Authentication expired, retrying in {self._retry_delay}s...")
                    self._authenticate_before_request()
                    time.sleep(self._retry_delay)
                    return self._make_request(method, endpoint, data, params, retries - 1)
                elif response.status_code == 503:
                    delay = self._retry_delay * 2
                    LOGGER.info(f"Service temporarily unavailable, retrying in {delay}s...")
                    time.sleep(delay)
                    return self._make_request(method, endpoint, data, params, retries - 1)
            elif response.status_code == 500:
                LOGGER.error(f"Server error (500): {response.text}")
                return None
                
            response.raise_for_status()
            return response
            
        except Exception as e:
            LOGGER.error("Request failed: %s", str(e))
            if retries > 0 and not str(e).startswith('500'):  # Don't retry 500 errors
                time.sleep(self._retry_delay)
                return self._make_request(method, endpoint, data, params, retries - 1)
            return None

    def refresh(self):
        """Refresh data."""
        response = self._make_request('get', "/rd/startup")
        if not response:
            LOGGER.error("Failed to refresh reminders data")
            return False

        try:
            data = response.json()
            
            # Clear existing data
            self.lists.clear()
            self.collections.clear()
            self._reminders_by_guid.clear()

            collections = data.get("Collections", [])
            
            for collection in collections:
                temp = []
                self.collections[collection["title"]] = {
                    "guid": collection["guid"],
                    "ctag": collection["ctag"],
                }
                
                reminders = data.get("Reminders", [])
                
                for reminder in reminders:
                    if reminder["pGuid"] != collection["guid"]:
                        continue

                    if reminder.get("dueDate"):
                        due = datetime(
                            reminder["dueDate"][1],
                            reminder["dueDate"][2],
                            reminder["dueDate"][3],
                            reminder["dueDate"][4],
                            reminder["dueDate"][5],
                        )
                    else:
                        due = None

                    reminder_data = {
                        "guid": reminder["guid"],
                        "title": reminder["title"],
                        "desc": reminder.get("description"),
                        "due": due,
                        "completed": reminder.get("completedDate") is not None,
                        "collection": collection["title"],
                        "etag": reminder.get("etag"),
                        "p_guid": reminder["pGuid"],
                    }
                    
                    temp.append(reminder_data)
                    self._reminders_by_guid[reminder["guid"]] = reminder_data
                    
                self.lists[collection["title"]] = temp
                
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
        self._authenticate_before_request()
        collection = self._validate_collection(collection)
        pguid = self.collections[collection]["guid"] if collection in self.collections else "tasks"

        # Format due date if provided
        due_date = kwargs.get("due_date")
        due_dates = self._format_due_date(due_date) if due_date else None

        # Handle recurrence
        recurrence_rule = self._format_recurrence(recurrence) if recurrence else None

        new_guid = str(uuid.uuid4())
        reminder_data = {
            "Reminder": {
                "guid": new_guid,
                "title": title,
                "description": description,
                "pGuid": pguid,
                "etag": None,
                "order": None,
                "priority": priority,
                "recurrence": recurrence_rule,
                "createdDate": self._format_date(datetime.now()),
                "lastModifiedDate": self._format_date(datetime.now()),
                "dueDateIsAllDay": False,
                "tags": tags or [],
                "completed": False,
                "completedDate": None
            }
        }

        if due_dates:
            reminder_data["Reminder"].update(due_dates)

        response = self._make_request(
            "post",
            "/rd/reminders/tasks",
            data=reminder_data
        )

        if response and response.status_code == 200:
            # Update local cache
            reminder_data = {
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
            self._reminders_by_guid[new_guid] = reminder_data
            self.lists[collection].append(reminder_data)
            
            # Update tags set
            if tags:
                self._tags.update(tags)
            
            return new_guid
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

        # Format due date if provided
        due_dates = self._format_due_date(due_date) if due_date is not None else None
        
        # Handle recurrence
        recurrence_rule = self._format_recurrence(recurrence) if recurrence else None

        reminder_data = {
            "Reminder": {
                "guid": guid,
                "pGuid": pguid,
                "title": title if title is not None else current["title"],
                "description": description if description is not None else current.get("desc", ""),
                "priority": priority if priority is not None else current.get("priority", Priority.NONE),
                "tags": tags if tags is not None else current.get("tags", []),
                "recurrence": recurrence_rule,
                "lastModifiedDate": self._format_date(datetime.now()),
            }
        }

        if due_dates:
            reminder_data["Reminder"].update(due_dates)

        response = self._make_request(
            "post",
            f"/rd/reminders/tasks/{guid}",
            data=reminder_data
        )

        if response and response.status_code == 200:
            # Update local cache
            current.update({
                "title": reminder_data["Reminder"]["title"],
                "desc": reminder_data["Reminder"]["description"],
                "due": due_date if due_date is not None else current.get("due"),
                "priority": reminder_data["Reminder"]["priority"],
                "tags": reminder_data["Reminder"]["tags"],
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

    def complete(self, guid):
        """Mark a reminder as completed."""
        reminder = self.get_reminder(guid)
        if not reminder:
            return False

        # Format due date if it exists
        due_dates = None
        if reminder.get("due"):
            due = reminder["due"]
            due_dates = [
                int(str(due.year) + str(due.month) + str(due.day)),
                due.year,
                due.month,
                due.day,
                due.hour,
                due.minute,
            ]

        # Create update data with completed status
        completed_date = int(time.time() * 1000)
        data = {
            "Reminders": [{
                "guid": guid,
                "title": reminder["title"],
                "description": reminder.get("desc", ""),
                "pGuid": reminder["p_guid"],
                "etag": None,
                "order": None,
                "priority": 0,
                "recurrence": None,
                "alarms": [],
                "startDate": None,
                "startDateTz": None,
                "startDateIsAllDay": False,
                "completedDate": completed_date,
                "dueDate": due_dates,
                "dueDateIsAllDay": False,
                "lastModifiedDate": completed_date,
            }],
            "ClientState": {"Collections": list(self.collections.values())},
        }

        response = self._make_request('post', "/rd/reminders/tasks", data=data)
        if response and response.ok:
            self.refresh()
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

    def batch_complete(self, guids):
        """Process batch completions with rate limiting"""
        results = {}
        with ThreadPoolExecutor(max_workers=3) as executor:  # Reduced from 5
            futures = {executor.submit(self.complete, guid): guid for guid in guids}
            
            for i, future in enumerate(as_completed(futures)):
                guid = futures[future]
                try:
                    results[guid] = future.result()
                except Exception as e:
                    results[guid] = False
                    LOGGER.error(f"Failed to complete {guid}: {str(e)}")
                    if '500' in str(e):  # If we get a 500, stop processing
                        break
                
                # Rate limit: 1 request every 0.5s
                time.sleep(0.5)
                    
        return results

    def batch_move(self, guids: List[str], target_collection: str) -> Dict[str, bool]:
        """Move multiple reminders to a target collection."""
        results = {}
        for guid in guids:
            results[guid] = self.move_reminder(guid, target_collection)
        return results

    def _format_due_date(self, due_date):
        """Convert datetime to iCloud-compatible UTC ISO format"""
        if not due_date:
            return None
            
        if not due_date.tzinfo:
            # Assume local timezone if not specified
            local_tz = pytz.timezone('America/Los_Angeles')  # Adjust as needed
            due_date = local_tz.localize(due_date)
            
        return due_date.astimezone(pytz.utc).isoformat(timespec='seconds')

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

    def _format_date(self, date: datetime) -> List:
        """Format a datetime object for the API."""
        if not date:
            return None
        return [0, date.year, date.month, date.day,
                date.hour, date.minute, date.second]
