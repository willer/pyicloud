"""Reminders service."""
from datetime import datetime, timedelta
import time
import uuid
import json
import logging
from tzlocal import get_localzone_name
from typing import List, Dict, Optional, Union
from collections import defaultdict
from pyicloud.exceptions import PyiCloudException
import pytz
from concurrent.futures import ThreadPoolExecutor, as_completed

LOGGER = logging.getLogger(__name__)

class RemindersService:
    """The 'Reminders' iCloud service."""

    def __init__(self, service_root, session, params):
        self.session = session
        self.params = params
        self.token_expiry = 0  # Track token expiration
        self._service_root = service_root
        self._reminders_endpoint = "%s/rd" % self._service_root
        self._reminders_startup_url = "%s/startup" % self._reminders_endpoint
        self._reminders_tasks_url = "%s/reminders/tasks" % self._reminders_endpoint
        self._max_retries = 2  # Reduced from 3
        self._retry_delay = 2  # Increased from 1
        
        # Initialize empty collections even before refresh
        self.lists = {}
        self.collections = {}
        self._reminders_by_guid = {}
        
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

    def post(self, title, description="", collection=None, **kwargs):
        self._authenticate_before_request()
        collection = self._validate_collection(collection)
        pguid = "tasks"
        if collection:
            if collection in self.collections:
                pguid = self.collections[collection]["guid"]

        due_dates = None
        if kwargs.get("due_date"):
            due_dates = [
                int(str(kwargs["due_date"].year) + str(kwargs["due_date"].month) + str(kwargs["due_date"].day)),
                kwargs["due_date"].year,
                kwargs["due_date"].month,
                kwargs["due_date"].day,
                kwargs["due_date"].hour,
                kwargs["due_date"].minute,
            ]

        new_guid = str(uuid.uuid4())
        data = {
            "Reminders": {
                "title": title,
                "description": description,
                "pGuid": pguid,
                "etag": None,
                "order": None,
                "priority": 0,
                "recurrence": None,
                "alarms": [],
                "startDate": None,
                "startDateTz": None,
                "startDateIsAllDay": False,
                "completedDate": None,
                "dueDate": due_dates,
                "dueDateIsAllDay": False,
                "lastModifiedDate": None,
                "createdDate": None,
                "isFamily": None,
                "createdDateExtended": int(time.time() * 1000),
                "guid": new_guid,
            },
            "ClientState": {"Collections": list(self.collections.values())},
        }
        
        response = self._make_request('post', "/rd/reminders/tasks", data=data)
        if response and response.ok:
            self.refresh()
            return new_guid
        return None

    def get_reminder(self, guid):
        """Get a reminder by its GUID."""
        return self._reminders_by_guid.get(guid)

    def update(self, guid, title=None, description=None, due_date=None, collection=None):
        """Update an existing reminder."""
        reminder = self._reminders_by_guid.get(guid)
        if not reminder:
            LOGGER.error("Cannot update reminder %s: not found", guid)
            return False

        # Format due date if provided
        due_dates = None
        if due_date:
            due_dates = [
                int(str(due_date.year) + str(due_date.month) + str(due_date.day)),
                due_date.year,
                due_date.month,
                due_date.day,
                due_date.hour,
                due_date.minute,
            ]
        elif reminder.get("due"):
            due = reminder["due"]
            due_dates = [
                int(str(due.year) + str(due.month) + str(due.day)),
                due.year,
                due.month,
                due.day,
                due.hour,
                due.minute,
            ]

        # Get target collection GUID
        target_pguid = reminder["p_guid"]
        if collection and collection in self.collections:
            target_pguid = self.collections[collection]["guid"]

        # Create update data
        data = {
            "Reminders": [{
                "guid": guid,
                "title": title if title is not None else reminder["title"],
                "description": description if description is not None else reminder.get("desc", ""),
                "pGuid": target_pguid,
                "etag": reminder.get("etag"),
                "order": None,
                "priority": 0,
                "recurrence": None,
                "alarms": [],
                "startDate": None,
                "startDateTz": None,
                "startDateIsAllDay": False,
                "completedDate": None,
                "dueDate": due_dates,
                "dueDateIsAllDay": False,
                "lastModifiedDate": int(time.time() * 1000),
                "createdDateExtended": int(time.time() * 1000),
            }],
            "ClientState": {"Collections": list(self.collections.values())},
        }

        # Update request parameters
        params = dict(self.params)
        params.update({
            "lang": "en-us",
            "usertz": get_localzone_name(),
        })

        response = self.session.post(
            self._reminders_tasks_url,
            data=json.dumps(data),
            params=params
        )

        if response.ok:
            # Add a small delay to allow server to process the update
            time.sleep(1)
            
            # Refresh to get latest state
            self.refresh()
            
            # Verify the update was successful
            updated_reminder = self._reminders_by_guid.get(guid)
            if not updated_reminder:
                LOGGER.error("Failed to find reminder %s after update", guid)
                return False
                
            # Check title if it was updated
            if title is not None and updated_reminder["title"] != title:
                LOGGER.error("Title mismatch after update. Expected: %s, Got: %s", 
                            title, updated_reminder["title"])
                return False
                
            # Check description if it was updated
            if description is not None and updated_reminder.get("desc") != description:
                LOGGER.error("Description mismatch after update. Expected: %s, Got: %s",
                            description, updated_reminder.get("desc"))
                return False
                
            # Check due date if it was updated
            if due_date is not None and updated_reminder.get("due"):
                # Compare only date components to avoid timezone issues
                expected_date = due_date.date()
                actual_date = updated_reminder["due"].date()
                if expected_date != actual_date:
                    LOGGER.error("Due date mismatch after update. Expected: %s, Got: %s",
                                expected_date, actual_date)
                    return False
                                
            return True
        else:
            LOGGER.error("Update request failed for reminder %s: %s", guid, response.text)
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
