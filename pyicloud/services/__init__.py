"""iCloud services."""

from pyicloud.services.calendar import CalendarService
from pyicloud.services.contacts import ContactsService
from pyicloud.services.drive import DriveService
from pyicloud.services.ubiquity import UbiquityService
from pyicloud.services.findmyiphone import FindMyiPhoneServiceManager
from pyicloud.services.photos import PhotosService
from pyicloud.services.account import AccountService
from pyicloud.services.notes import NotesService
from pyicloud.services.reminders import RemindersService

__all__ = [
    "CalendarService",
    "ContactsService",
    "DriveService",
    "UbiquityService",
    "FindMyiPhoneServiceManager",
    "PhotosService",
    "AccountService",
    "NotesService",
    "RemindersService",
]
