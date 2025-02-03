"""EventKit-based Reminders service."""
from datetime import datetime
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
    NSError
)
from EventKit import (
    EKEventStore,
    EKEntityTypeReminder,
    EKReminder
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
    """EventKit-based Reminders service."""

    def __init__(self):
        """Initialize the EventKit reminders service."""
        self.store = EKEventStore.alloc().init()
        self._authorize()
        self._lists = {}  # Cache for reminder lists
        self._reminders_by_guid = {}  # Cache for reminders
        self.refresh()

    def _authorize(self):
        """Request authorization to access reminders."""
        authorized = [False]

        def completion_handler(granted: bool, error: Optional[NSError]) -> None:
            if error:
                logger.error("EventKit authorization error: %s", error.localizedDescription())
            authorized[0] = bool(granted)

        block = objc.block(completion_handler, None, [objc.c_bool, NSError])

        self.store.requestAccessToEntityType_completion_(
            EKEntityTypeReminder,
            block
        )

        # Wait for authorization
        while not authorized[0]:
            pass

        if not authorized[0]:
            logger.error("EventKit authorization denied")
            raise Exception("EventKit authorization denied")

    def refresh(self) -> List[Dict]:
        """Refresh and return all reminders."""
        reminders = []
        result = [None]

        @objc.callbackFor(EKEventStore.fetchRemindersMatchingPredicate_completion_)
        def completion_handler(self, reminders_list: List[EKReminder], error: Optional[NSError]) -> None:
            if error:
                logger.error("Error fetching reminders: %s", error.localizedDescription())
            result[0] = reminders_list

        predicate = self.store.predicateForRemindersInCalendars_(None)
        self.store.fetchRemindersMatchingPredicate_completion_(
            predicate,
            completion_handler
        )

        # Wait for results
        while result[0] is None:
            pass

        if result[0]:
            for reminder in result[0]:
                reminders.append(self._parse_reminder(reminder))

        return reminders

    def _parse_reminder(self, reminder: EKReminder) -> Dict:
        """Parse an EKReminder into a dictionary."""
        calendar = NSCalendar.currentCalendar()

        reminder_dict = {
            'title': reminder.title(),
            'completed': reminder.completed(),
            'priority': reminder.priority(),
            'notes': reminder.notes(),
            'identifier': str(uuid.uuid4()),  # Generate a unique identifier
        }

        if reminder.dueDateComponents():
            components = reminder.dueDateComponents()
            date = calendar.dateFromComponents_(components)
            if date:
                reminder_dict['due_date'] = datetime.fromtimestamp(date.timeIntervalSince1970())

        return reminder_dict

    def _convert_reminder_to_dict(self, reminder) -> Dict:
        """Convert an EKReminder object to our standard dictionary format."""
        return {
            'guid': str(reminder.calendarItemIdentifier()),
            'title': str(reminder.title()),
            'desc': str(reminder.notes()) if reminder.notes() else '',
            'due': reminder.dueDateComponents().date() if reminder.dueDateComponents() else None,
            'completed': bool(reminder.completed()),
            'collection': str(reminder.calendar().title()),
            'priority': int(reminder.priority()) if reminder.priority() else Priority.NONE,
            'p_guid': str(reminder.calendar().calendarIdentifier())
        }

    def _create_reminder(self, title: str, calendar) -> EKReminder:
        """Create a new EKReminder object."""
        reminder = EKReminder.reminderWithEventStore_(self.store)
        reminder.setTitle_(title)
        reminder.setCalendar_(calendar)
        return reminder

    def post(self, title: str, description: str = "", collection: Optional[str] = None,
             priority: int = Priority.NONE, tags: List[str] = None,
             due_date: Optional[datetime] = None, **kwargs) -> Optional[str]:
        """Create a new reminder."""
        try:
            # Get the target calendar
            if collection and collection in self._lists:
                calendar = self._lists[collection]['calendar']
            else:
                # Use the first available calendar
                calendar = next(iter(self._lists.values()))['calendar']

            # Create the reminder
            reminder = self._create_reminder(title, calendar)
            
            # Set properties
            if description:
                reminder.setNotes_(description)
            if priority is not None:
                reminder.setPriority_(priority)
            if due_date:
                calendar = NSCalendar.currentCalendar()
                components = calendar.components_fromDate_(
                    NSCalendarUnitYear | 
                    NSCalendarUnitMonth | 
                    NSCalendarUnitDay |
                    NSCalendarUnitHour |
                    NSCalendarUnitMinute |
                    NSCalendarUnitSecond,
                    NSDate.dateWithTimeIntervalSince1970_(due_date.timestamp())
                )
                reminder.setDueDateComponents_(components)

            # Save the reminder
            success, error = self.store.saveReminder_commit_error_(reminder, True, None)
            if success:
                # Update our cache
                reminder_data = self._convert_reminder_to_dict(reminder)
                self._reminders_by_guid[reminder_data['guid']] = reminder_data
                return reminder_data['guid']
            else:
                logger.error(f"Failed to save reminder: {error}")
                
        except Exception as e:
            logger.error(f"Failed to create reminder: {str(e)}")
            
        return None

    def get_reminder(self, guid: str) -> Optional[Dict]:
        """Get a reminder by its GUID."""
        return self._reminders_by_guid.get(guid)

    def update(self, guid: str, title: Optional[str] = None,
               description: Optional[str] = None, due_date: Optional[datetime] = None,
               collection: Optional[str] = None, priority: Optional[int] = None,
               tags: Optional[List[str]] = None) -> bool:
        """Update a reminder."""
        try:
            reminder = self.store.calendarItemWithIdentifier_(guid)
            if not reminder:
                return False

            if title is not None:
                reminder.setTitle_(title)
            if description is not None:
                reminder.setNotes_(description)
            if priority is not None:
                reminder.setPriority_(priority)
            if due_date is not None:
                calendar = NSCalendar.currentCalendar()
                components = calendar.components_fromDate_(
                    NSCalendarUnitYear | 
                    NSCalendarUnitMonth | 
                    NSCalendarUnitDay |
                    NSCalendarUnitHour |
                    NSCalendarUnitMinute |
                    NSCalendarUnitSecond,
                    NSDate.dateWithTimeIntervalSince1970_(due_date.timestamp())
                )
                reminder.setDueDateComponents_(components)
            if collection is not None and collection in self._lists:
                reminder.setCalendar_(self._lists[collection]['calendar'])

            success, error = self.store.saveReminder_commit_error_(reminder, True, None)
            if success:
                # Update our cache
                reminder_data = self._convert_reminder_to_dict(reminder)
                self._reminders_by_guid[guid] = reminder_data
                return True
            else:
                logger.error(f"Failed to update reminder: {error}")

        except Exception as e:
            logger.error(f"Failed to update reminder: {str(e)}")

        return False

    def complete(self, guid: str) -> bool:
        """Mark a reminder as completed."""
        try:
            reminder = self.store.calendarItemWithIdentifier_(guid)
            if not reminder:
                return False

            reminder.setCompleted_(True)
            success, error = self.store.saveReminder_commit_error_(reminder, True, None)
            if success:
                # Update our cache
                reminder_data = self._convert_reminder_to_dict(reminder)
                self._reminders_by_guid[guid] = reminder_data
                return True
            else:
                logger.error(f"Failed to complete reminder: {error}")

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

    def get_reminders_by_due_date(self, start_date: datetime, end_date: datetime) -> List[Dict]:
        """Get reminders due between start_date and end_date."""
        calendar = NSCalendar.currentCalendar()
        start = NSDate.dateWithTimeIntervalSince1970_(start_date.timestamp())
        end = NSDate.dateWithTimeIntervalSince1970_(end_date.timestamp())

        result = [None]

        @objc.callbackFor(EKEventStore.fetchRemindersMatchingPredicate_completion_)
        def completion_handler(self, reminders_list: List[EKReminder], error: Optional[NSError]) -> None:
            if error:
                logger.error("Error fetching reminders: %s", error.localizedDescription())
            result[0] = reminders_list

        predicate = self.store.predicateForIncompleteRemindersWithDueDateStarting_ending_calendars_(
            start,
            end,
            None
        )
        self.store.fetchRemindersMatchingPredicate_completion_(
            predicate,
            completion_handler
        )

        # Wait for results
        while result[0] is None:
            pass

        reminders = []
        if result[0]:
            for reminder in result[0]:
                reminders.append(self._parse_reminder(reminder))

        return reminders

    def get_upcoming_reminders(self) -> List[Dict]:
        """Get upcoming reminders."""
        now = datetime.now()
        future = now.replace(year=now.year + 1)  # Get reminders for the next year
        return self.get_reminders_by_due_date(now, future)

    def move_reminder(self, guid: str, target_collection: str) -> bool:
        """Move a reminder to a different collection."""
        if target_collection not in self._lists:
            return False

        return self.update(guid, collection=target_collection)

    def batch_complete(self, guids: List[str]) -> Dict[str, bool]:
        """Complete multiple reminders in batch."""
        results = {}
        for guid in guids:
            results[guid] = self.complete(guid)
        return results

    def batch_move(self, guids: List[str], target_collection: str) -> Dict[str, bool]:
        """Move multiple reminders to a different collection in batch."""
        results = {}
        for guid in guids:
            results[guid] = self.move_reminder(guid, target_collection)
        return results

    @property
    def lists(self):
        """Get all reminder lists."""
        return {name: {'guid': info['guid']} for name, info in self._lists.items()} 