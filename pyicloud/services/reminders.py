"""Reminders service."""
from datetime import datetime
import time
import uuid
import json

from tzlocal import get_localzone_name


class RemindersService:
    """The 'Reminders' iCloud service."""

    def __init__(self, service_root, session, params):
        self.session = session
        self._params = params
        self._service_root = service_root

        # Add service-specific headers
        self.session.headers.update({
            "Origin": "https://www.icloud.com",
            "Referer": "https://www.icloud.com/reminders/",
            "Accept": "*/*",
            "X-Requested-With": "XMLHttpRequest",
            "X-Apple-Service": "reminders",
            "X-Apple-Auth-Token": session.service.session_data.get("session_token"),
            "X-Apple-Domain-Id": "reminders",
        })

        # Add service-specific parameters
        self._params.update({
            "clientBuildNumber": "2020Project52",
            "clientMasteringNumber": "2020B29",
            "clientId": session.service.client_id,
            "dsid": session.service.data.get("dsInfo", {}).get("dsid"),
            "lang": "en-us",
            "usertz": get_localzone_name(),
        })

        self.lists = {}
        self.collections = {}
        self._reminders_by_guid = {}  # Cache to store reminders by GUID

        self.refresh()

    def refresh(self):
        """Refresh data."""
        params_reminders = dict(self._params)
        params_reminders.update(
            {"clientVersion": "4.0", "lang": "en-us", "usertz": get_localzone_name()}
        )

        # Open reminders
        req = self.session.get(
            self._service_root + "/rd/startup", params=params_reminders
        )

        data = req.json()

        self.lists = {}
        self.collections = {}
        self._reminders_by_guid = {}  # Reset the cache
        
        for collection in data["Collections"]:
            temp = []
            self.collections[collection["title"]] = {
                "guid": collection["guid"],
                "ctag": collection["ctag"],
            }
            for reminder in data["Reminders"]:
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

    def post(self, title, description="", collection=None, due_date=None):
        """Adds a new reminder."""
        pguid = "tasks"
        if collection:
            if collection in self.collections:
                pguid = self.collections[collection]["guid"]

        params_reminders = dict(self._params)
        params_reminders.update(
            {"clientVersion": "4.0", "lang": "en-us", "usertz": get_localzone_name()}
        )

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

        new_guid = str(uuid.uuid4())
        req = self.session.post(
            self._service_root + "/rd/reminders/tasks",
            data=json.dumps(
                {
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
            ),
            params=params_reminders,
        )
        
        if req.ok:
            self.refresh()  # Refresh to get the new reminder in our cache
            return new_guid
        return None

    def get_reminder(self, guid):
        """Get a reminder by its GUID."""
        return self._reminders_by_guid.get(guid)

    def update(self, guid, title=None, description=None, due_date=None, collection=None, max_retries=3):
        """Update an existing reminder by creating a new one with updated data."""
        reminder = self._reminders_by_guid.get(guid)
        if not reminder:
            return False

        # Create a new reminder with updated data
        new_title = title if title is not None else reminder["title"]
        new_desc = description if description is not None else reminder.get("desc", "")
        new_due_date = due_date if due_date is not None else reminder.get("due")
        new_collection = collection if collection is not None else reminder["collection"]

        # Create the new reminder
        new_guid = self.post(
            new_title,
            description=new_desc,
            collection=new_collection,
            due_date=new_due_date
        )

        return new_guid is not None

    def complete(self, guid):
        """Mark a reminder as completed.
        
        Note: The completion functionality may not work reliably due to API limitations.
        The iCloud API appears to handle reminder completion differently than other operations.
        While this method attempts to mark a reminder as completed, it may not always succeed.
        
        Args:
            guid: The GUID of the reminder to complete.
            
        Returns:
            bool: True if the operation was successful, False otherwise.
        """
        # Re-authenticate for reminders service
        self.session.service.authenticate(True, "reminders")

        # Get the existing reminder
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
        completed_date = int(time.time() * 1000)  # Use milliseconds timestamp
        update_data = {
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

        # Update parameters with required fields
        params_reminders = dict(self._params)
        params_reminders.update({
            "clientVersion": "4.0",
            "lang": "en-us",
            "usertz": get_localzone_name()
        })

        try:
            # Update the reminder with completed status
            req = self.session.post(
                f"{self._service_root}/rd/reminders/tasks",
                params=params_reminders,
                data=json.dumps(update_data),
                headers=self.session.headers
            )

            if req.ok:
                self.refresh()  # Refresh to get updated data
                return True
        except Exception:
            pass  # Ignore any errors and return False
        return False
