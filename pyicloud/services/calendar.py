"""Calendar service."""
from datetime import datetime
from calendar import monthrange

from tzlocal import get_localzone_name


class CalendarService:
    """
    The 'Calendar' iCloud service, connects to iCloud and returns events.
    """

    def __init__(self, service_root, session, params):
        self.session = session
        self.params = params
        self._service_root = service_root
        self._calendar_endpoint = "%s/ca" % self._service_root
        self._calendar_refresh_url = "%s/events" % self._calendar_endpoint
        self._calendar_event_detail_url = f"{self._calendar_endpoint}/eventdetail"
        self._calendars = "%s/startup" % self._calendar_endpoint

        # Add service-specific headers
        self.session.headers.update({
            "Origin": "https://www.icloud.com",
            "Referer": "https://www.icloud.com/calendar/",
            "Accept": "*/*",
            "X-Requested-With": "XMLHttpRequest",
            "X-Apple-Service": "calendar",
            "X-Apple-Auth-Token": session.service.session_data.get("session_token"),
            "X-Apple-Domain-Id": "calendar",
        })

        # Add service-specific parameters
        self.params.update({
            "clientBuildNumber": "2020Project52",
            "clientMasteringNumber": "2020B29",
            "clientId": session.service.client_id,
            "dsid": session.service.data.get("dsInfo", {}).get("dsid"),
            "lang": "en-us",
            "usertz": get_localzone_name(),
        })

        self.response = {}

    def get_event_detail(self, pguid, guid):
        """
        Fetches a single event's details by specifying a pguid
        (a calendar) and a guid (an event's ID).
        """
        params = dict(self.params)
        params.update({"lang": "en-us", "usertz": get_localzone_name()})
        url = f"{self._calendar_event_detail_url}/{pguid}/{guid}"
        req = self.session.get(url, params=params)
        self.response = req.json()
        return self.response["Event"][0]

    def refresh_client(self, from_dt=None, to_dt=None):
        """
        Refreshes the CalendarService endpoint, ensuring that the
        event data is up-to-date. If no 'from_dt' or 'to_dt' datetimes
        have been given, the range becomes this month.
        """
        today = datetime.today()
        first_day, last_day = monthrange(today.year, today.month)
        if not from_dt:
            from_dt = datetime(today.year, today.month, first_day)
        if not to_dt:
            to_dt = datetime(today.year, today.month, last_day)
        params = dict(self.params)
        params.update(
            {
                "lang": "en-us",
                "usertz": get_localzone_name(),
                "startDate": from_dt.strftime("%Y-%m-%d"),
                "endDate": to_dt.strftime("%Y-%m-%d"),
            }
        )
        req = self.session.get(self._calendar_refresh_url, params=params)
        self.response = req.json()

    def events(self, from_dt=None, to_dt=None):
        """
        Retrieves events for a given date range, by default, this month.
        """
        self.refresh_client(from_dt, to_dt)
        return self.response.get("Event")

    def calendars(self):
        """
        Retrieves calendars of this month.
        """
        today = datetime.today()
        first_day, last_day = monthrange(today.year, today.month)
        from_dt = datetime(today.year, today.month, first_day)
        to_dt = datetime(today.year, today.month, last_day)
        params = dict(self.params)
        params.update(
            {
                "lang": "en-us",
                "usertz": get_localzone_name(),
                "startDate": from_dt.strftime("%Y-%m-%d"),
                "endDate": to_dt.strftime("%Y-%m-%d"),
            }
        )
        req = self.session.get(self._calendars, params=params)
        self.response = req.json()
        return self.response["Collection"]
