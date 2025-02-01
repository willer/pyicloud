import pytest
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch
from pyicloud import PyiCloudService
from pyicloud.services.reminders import RemindersService
from pyicloud.exceptions import PyiCloudAPIResponseException

@pytest.fixture
def mock_session():
    """Create a mock session."""
    with patch('requests.Session') as mock:
        session = MagicMock()
        mock.return_value = session
        
        # Mock successful authentication response
        auth_response = MagicMock()
        auth_response.status_code = 200
        auth_response.json.return_value = {
            "dsInfo": {"dsid": "12345678"},
            "webservices": {
                "reminders": {
                    "url": "https://p123-remindersws.icloud.com",
                    "status": "active"
                }
            },
            "apps": {
                "reminders": {
                    "canLaunchWithOneFactor": True,
                    "url": "https://p123-remindersws.icloud.com"
                }
            }
        }
        session.post.return_value = auth_response
        
        # Mock headers
        auth_response.headers = {
            "X-Apple-Session-Token": "fake-session-token",
            "scnt": "fake-scnt",
            "X-Apple-ID-Session-Id": "fake-session-id"
        }
        
        yield session

@pytest.fixture
def icloud_service(mock_session):
    """Create a mock iCloud service for testing."""
    with patch('pyicloud.base.PyiCloudSession', return_value=mock_session):
        service = PyiCloudService('test@example.com', 'password')
        service.data = {
            "dsInfo": {"dsid": "12345678"},
            "webservices": {
                "reminders": {
                    "url": "https://p123-remindersws.icloud.com",
                    "status": "active"
                }
            }
        }
        return service

@pytest.fixture
def reminders_service(icloud_service, monkeypatch):
    """Create a mock reminders service for testing."""
    service = RemindersService(icloud_service)
    
    # Mock the request response for getting reminders
    def mock_request(*args, **kwargs):
        return {
            "Reminders": [
                {
                    "fields": {
                        "title": "Test Reminder",
                        "dueDate": (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ"),
                        "hasSubtasks": False,
                        "hasAttachments": False,
                        "isShared": False,
                        "priority": 0,
                        "completed": False
                    },
                    "guid": "test-guid-1",
                    "etag": "test-etag-1"
                }
            ]
        }
    monkeypatch.setattr(service, '_make_request', mock_request)
    
    return service

def test_get_all_reminders(reminders_service):
    """Test getting all reminders."""
    reminders = reminders_service.reminders
    assert len(reminders) == 1
    reminder = reminders[0]
    assert reminder["fields"]["title"] == "Test Reminder"
    assert not reminder["fields"]["completed"]
    assert not reminder["fields"]["hasSubtasks"]
    assert not reminder["fields"]["hasAttachments"]
    assert not reminder["fields"]["isShared"]

def test_create_reminder(reminders_service, monkeypatch):
    """Test creating a new reminder."""
    def mock_create_request(*args, **kwargs):
        return {
            "fields": {
                "title": "New Reminder",
                "dueDate": "2024-02-01T10:00:00Z",
                "hasSubtasks": False,
                "hasAttachments": False,
                "isShared": False,
                "priority": 1,
                "completed": False
            },
            "guid": "new-guid",
            "etag": "new-etag"
        }
    monkeypatch.setattr(reminders_service, '_make_request', mock_create_request)
    
    reminder = reminders_service.create(
        "New Reminder",
        due_date="2024-02-01T10:00:00Z",
        priority=1
    )
    assert reminder["fields"]["title"] == "New Reminder"
    assert reminder["fields"]["priority"] == 1
    assert reminder["guid"] == "new-guid"

def test_complete_reminder(reminders_service, monkeypatch):
    """Test completing a reminder."""
    def mock_complete_request(*args, **kwargs):
        return {
            "fields": {
                "title": "Test Reminder",
                "completed": True,
                "completedDate": datetime.now().strftime("%Y-%m-%dT%H:%M:%SZ")
            },
            "guid": "test-guid-1",
            "etag": "test-etag-2"
        }
    monkeypatch.setattr(reminders_service, '_make_request', mock_complete_request)
    
    reminder = reminders_service.complete("test-guid-1")
    assert reminder["fields"]["completed"]
    assert "completedDate" in reminder["fields"]

def test_delete_reminder(reminders_service, monkeypatch):
    """Test deleting a reminder."""
    def mock_delete_request(*args, **kwargs):
        return True
    monkeypatch.setattr(reminders_service, '_make_request', mock_delete_request)
    
    result = reminders_service.delete("test-guid-1")
    assert result is True 