"""EventKit-based Reminders service."""
from datetime import datetime, timedelta
import logging
import uuid
from typing import List, Dict, Optional, Union, Any, Tuple
import objc
from Foundation import (
    NSCalendar,
    NSDate,
    NSCalendarUnitYear,
    NSCalendarUnitMonth,
    NSCalendarUnitDay,
    NSCalendarUnitHour,
    NSCalendarUnitMinute,
    NSCalendarUnitSecond,
    NSError,
    NSDateComponents,
    NSTimeZone
)
from EventKit import (
    EKEventStore,
    EKEntityTypeReminder,
    EKReminder,
    EKCalendar,
    EKSpan
)

logger = logging.getLogger(__name__)

class Priority:
    """Priority levels for reminders"""
    NONE = 0
    LOW = 1
    MEDIUM = 2
    HIGH = 3
    URGENT = 4

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
            def completion_handler(granted: bool, error: Optional[NSError]) -> None:
                pass  # We don't need to do anything in the callback
            
            block = objc.Block(completion_handler, None, [objc.c_bool, NSError])
            success = self.store.requestAccessToEntityType_completion_(
                EKEntityTypeReminder,
                block
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
            'completed': bool(reminder.completed()),
            'collection': str(reminder.calendar().title()),
            'priority': int(reminder.priority()) if reminder.priority() else 0,
            'p_guid': str(reminder.calendar().calendarIdentifier())
        }

        if reminder.dueDateComponents():
            components = reminder.dueDateComponents()
            calendar = NSCalendar.currentCalendar()
            # Set the calendar's timezone to UTC for consistent handling
            calendar.setTimeZone_(NSTimeZone.timeZoneWithName_("UTC"))
            date = calendar.dateFromComponents_(components)
            if date:
                # Convert to UTC timestamp and create UTC datetime
                timestamp = date.timeIntervalSince1970()
                result['due'] = datetime.fromtimestamp(timestamp, tz=datetime.timezone.utc)
                logger.debug("Converted reminder due date to UTC: %s (from timestamp: %s)", result['due'], timestamp)

        return result

    def post(self, title: str, description: str = "", collection: Optional[str] = None,
             priority: int = 0, tags: List[str] = None,
             due_date: Optional[datetime] = None) -> Optional[str]:
        """Create a new reminder."""
        try:
            reminder = EKReminder.reminderWithEventStore_(self.store)
            reminder.setTitle_(title)
            reminder.setNotes_(description or "")
            reminder.setPriority_(priority)

            if collection:
                calendar = None
                for cal in self._calendars:
                    if cal.title() == collection:
                        calendar = cal
                        break
                if calendar:
                    reminder.setCalendar_(calendar)
                else:
                    # Use first available calendar if specified one not found
                    reminder.setCalendar_(self._calendars[0])
            else:
                # Use first available calendar
                reminder.setCalendar_(self._calendars[0])

            if due_date:
                # Convert to UTC for storage
                if due_date.tzinfo is not None:
                    due_date = due_date.astimezone(datetime.timezone.utc)
                    logger.debug("Converted due_date to UTC: %s", due_date)
                else:
                    # If naive datetime, assume UTC
                    due_date = due_date.replace(tzinfo=datetime.timezone.utc)
                    logger.debug("Added UTC timezone to naive due_date: %s", due_date)
                
                # Create calendar with UTC timezone
                calendar = NSCalendar.currentCalendar()
                calendar.setTimeZone_(NSTimeZone.timeZoneWithName_("UTC"))
                
                components = NSDateComponents.alloc().init()
                components.setYear_(due_date.year)
                components.setMonth_(due_date.month)
                components.setDay_(due_date.day)
                components.setHour_(due_date.hour)
                components.setMinute_(due_date.minute)
                components.setSecond_(due_date.second)
                components.setTimeZone_(NSTimeZone.timeZoneWithName_("UTC"))
                
                # Convert components to NSDate to verify the conversion
                date = calendar.dateFromComponents_(components)
                if date:
                    logger.debug("Verified NSDate from components: %s", datetime.fromtimestamp(date.timeIntervalSince1970(), tz=datetime.timezone.utc))
                
                reminder.setDueDateComponents_(components)

            success, error = self.store.saveReminder_commit_error_(reminder, True, None)
            if success:
                return str(reminder.calendarItemIdentifier())
            else:
                logger.error(f"Failed to save reminder: {error}")

        except Exception as e:
            logger.error(f"Failed to create reminder: {str(e)}")

        return None

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
                logger.error(f"Failed to update reminder: {error}")
            return success

        except Exception as e:
            logger.error(f"Failed to update reminder: {str(e)}")
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
                logger.error(f"Failed to complete reminder: {error}")
            return success

        except Exception as e:
            logger.error(f"Failed to complete reminder: {str(e)}")
            return False

    def get_reminders_by_collection(self, collection_name: str,
                                  include_completed: bool = False) -> List[Dict]:
        """Get all reminders in a specific collection."""
        if collection_name not in self._lists:
            return []

        calendar = self._lists[collection_name]['calendar']
        predicate = self.store.predicateForRemindersInCalendars_([calendar])
        
        # Create a completion block
        def completion_handler(fetched_reminders):
            if fetched_reminders:
                result = []
                for reminder in fetched_reminders:
                    if not include_completed and reminder.completed():
                        continue
                    result.append(self._convert_reminder_to_dict(reminder))
                return result
            return []
        
        # Fetch reminders
        self.store.fetchRemindersMatchingPredicate_completion_(
            predicate,
            completion_handler
        )

        return []  # Return empty list since we can't get results synchronously

    def get_reminders_by_due_date(self, start_date: datetime, end_date: datetime, include_completed: bool = False) -> List[Dict]:
        """Get reminders due between start_date and end_date (inclusive)."""
        logger.debug("Original start_date: %s (%s), end_date: %s (%s)", 
                    start_date, start_date.tzinfo, end_date, end_date.tzinfo)
        
        # Convert to UTC for comparison
        if start_date.tzinfo is not None:
            start_date = start_date.astimezone(datetime.timezone.utc)
        else:
            start_date = start_date.replace(tzinfo=datetime.timezone.utc)
            
        if end_date.tzinfo is not None:
            end_date = end_date.astimezone(datetime.timezone.utc)
        else:
            end_date = end_date.replace(tzinfo=datetime.timezone.utc)
            
        # Set start_date to beginning of day and end_date to end of day
        start_date = start_date.replace(hour=0, minute=0, second=0, microsecond=0)
        end_date = end_date.replace(hour=23, minute=59, second=59, microsecond=999999)
            
        logger.debug("Adjusted start_date: %s (%s), end_date: %s (%s)", 
                    start_date, start_date.tzinfo, end_date, end_date.tzinfo)

        # Create NSDate objects from UTC timestamps
        start = NSDate.dateWithTimeIntervalSince1970_(start_date.timestamp())
        end = NSDate.dateWithTimeIntervalSince1970_(end_date.timestamp())
        
        logger.debug("NSDate start: %s, end: %s", start, end)

        result = [None]

        def completion_handler(reminders_list: List[EKReminder], error: Optional[NSError]) -> None:
            if error:
                logger.error("Error fetching reminders: %s", error.localizedDescription())
            result[0] = reminders_list
            if reminders_list:
                logger.debug("Found %d reminders", len(reminders_list))
                for reminder in reminders_list:
                    components = reminder.dueDateComponents()
                    calendar = NSCalendar.currentCalendar()
                    calendar.setTimeZone_(NSTimeZone.timeZoneWithName_("UTC"))
                    date = calendar.dateFromComponents_(components) if components else None
                    logger.debug("Reminder: title=%s, due_components=%s, due_date=%s", 
                               reminder.title(), components, date)

        block = objc.Block(completion_handler, None, [objc.c_array(EKReminder), NSError])

        # Use the current calendar to create the predicate
        if include_completed:
            predicate = self.store.predicateForRemindersInCalendars_(None)
        else:
            predicate = self.store.predicateForIncompleteRemindersWithDueDateStarting_ending_calendars_(
                start,
                end,
                None
            )

        self.store.fetchRemindersMatchingPredicate_completion_(
            predicate,
            block
        )

        # Wait for results
        while result[0] is None:
            pass

        reminders = []
        if result[0]:
            for reminder in result[0]:
                # For completed reminders, we need to filter by date manually
                if include_completed:
                    due_date = reminder.dueDateComponents()
                    if due_date:
                        calendar = NSCalendar.currentCalendar()
                        calendar.setTimeZone_(NSTimeZone.timeZoneWithName_("UTC"))
                        date = calendar.dateFromComponents_(due_date)
                        if date:
                            # Convert to UTC datetime for comparison
                            reminder_date = datetime.fromtimestamp(date.timeIntervalSince1970(), tz=datetime.timezone.utc)
                            logger.debug("Checking reminder %s: due=%s, start=%s, end=%s", 
                                       reminder.title(), reminder_date, start_date, end_date)
                            if start_date <= reminder_date <= end_date:
                                reminders.append(self._convert_reminder_to_dict(reminder))
                else:
                    # For incomplete reminders, the predicate has already filtered by date
                    reminder_dict = self._convert_reminder_to_dict(reminder)
                    logger.debug("Adding incomplete reminder: %s, due=%s", reminder_dict["title"], reminder_dict.get("due"))
                    reminders.append(reminder_dict)

        logger.debug("Returning %d reminders", len(reminders))
        for reminder in reminders:
            logger.debug("Returned reminder: %s", reminder)

        return reminders

    def get_upcoming_reminders(self, days: int = 7, include_completed: bool = False) -> Dict[str, List[Dict]]:
        """Get upcoming reminders grouped by collection."""
        now = datetime.now(datetime.timezone.utc)
        end_date = now + timedelta(days=days)
        
        # Get all reminders due in the next N days
        reminders = self.get_reminders_by_due_date(
            start_date=now,
            end_date=end_date,
            include_completed=include_completed
        )
        
        # Group by collection
        result = {}
        for reminder in reminders:
            collection = reminder["collection"]
            if collection not in result:
                result[collection] = []
            result[collection].append(reminder)
            
        return result

    def move_reminder(self, guid: str, target_collection: str) -> bool:
        """Move a reminder to a different collection.

        Args:
            guid: The GUID of the reminder to move.
            target_collection: The collection to move the reminder to.

        Returns:
            bool: True if the move was successful, False otherwise.
        """
        try:
            reminder = self.get_reminder(guid)
            if not reminder:
                logger.error("Reminder %s not found", guid)
                return False

            if reminder["collection"] == target_collection:
                # Already in the target collection
                return True

            # Attempt to move the reminder
            try:
                response = self._service.post(
                    f"/rd/reminders/{guid}/move",
                    {
                        "collection": target_collection,
                        "clientData": {"timezone": self._get_timezone()}
                    }
                )
            except Exception as e:
                if "Moving between lists is unsupported in this account" in str(e):
                    logger.warning("Moving reminders between lists is not supported by this account")
                    return False
                raise

            if not response.ok:
                if "Moving between lists is unsupported in this account" in response.text:
                    logger.warning("Moving reminders between lists is not supported by this account")
                    return False
                logger.error("Failed to move reminder: %s", response.text)
                return False

            return True
        except Exception as e:
            logger.error("Failed to move reminder: %s", str(e))
            return False

    def batch_complete(self, guids: List[str]) -> Dict[str, bool]:
        """Complete multiple reminders in batch."""
        results = {}
        for guid in guids:
            results[guid] = self.complete(guid)
        return results

    def batch_move(self, guids: List[str], target_collection: str) -> Dict[str, bool]:
        """Move multiple reminders to a different collection in batch.

        Args:
            guids: List of GUIDs of reminders to move.
            target_collection: The collection to move the reminders to.

        Returns:
            Dict[str, bool]: A dictionary mapping GUIDs to success status.
        """
        results = {}
        for guid in guids:
            try:
                results[guid] = self.move_reminder(guid, target_collection)
            except Exception as e:
                logger.error("Failed to move reminder %s: %s", guid, str(e))
                results[guid] = False
        return results 