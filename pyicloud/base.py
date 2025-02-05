"""Library base file."""
from uuid import uuid1
import inspect
import json
import logging
from requests import Session
from tempfile import gettempdir
from os import path, mkdir
from re import match
import http.cookiejar as cookielib
import getpass
from urllib3.util.retry import Retry
from requests.adapters import HTTPAdapter
from datetime import datetime
import time
from pyicloud.utils import get_localzone_name

from pyicloud.exceptions import (
    PyiCloudFailedLoginException,
    PyiCloudAPIResponseException,
    PyiCloud2SARequiredException,
    PyiCloudServiceNotActivatedException,
)
from pyicloud.services import (
    FindMyiPhoneServiceManager,
    CalendarService,
    UbiquityService,
    ContactsService,
    RemindersService,
    PhotosService,
    AccountService,
    DriveService,
    NotesService,
)
from pyicloud.utils import get_password_from_keyring


LOGGER = logging.getLogger(__name__)

HEADER_DATA = {
    "X-Apple-ID-Account-Country": "account_country",
    "X-Apple-ID-Session-Id": "session_id",
    "X-Apple-Session-Token": "session_token",
    "X-Apple-TwoSV-Trust-Token": "trust_token",
    "scnt": "scnt",
}


class PyiCloudPasswordFilter(logging.Filter):
    """Password log hider."""

    def __init__(self, password):
        super().__init__(password)

    def filter(self, record):
        message = record.getMessage()
        if self.name in message:
            record.msg = message.replace(self.name, "*" * 8)
            record.args = []

        return True


class PyiCloudSession(Session):
    """iCloud session."""

    def __init__(self, service):
        self.service = service
        super().__init__()
        # Configure more efficient retry strategy
        self.retry_strategy = Retry(
            total=2,  # Reduced from 5
            connect=2,  # Reduced from 5
            read=2,    # Reduced from 5
            backoff_factor=0.5,  # Reduced from 2.0
            status_forcelist=[429, 503, 504],  # Removed 500, 502 as they're handled separately
            allowed_methods=frozenset(['GET', 'POST', 'PUT', 'DELETE']),
            respect_retry_after_header=True,
            raise_on_status=True  # Changed to True to handle errors in request method
        )
        self.mount('https://', HTTPAdapter(max_retries=self.retry_strategy))
        self._last_auth_time = None

    def request(self, method, url, **kwargs):
        """Make a request with improved logging and error handling."""
        # Set up logging
        callee = inspect.stack()[2]
        module = inspect.getmodule(callee[0])
        request_logger = logging.getLogger(module.__name__).getChild("http")
        if self.service.password_filter not in request_logger.filters:
            request_logger.addFilter(self.service.password_filter)

        request_logger.debug("%s %s %s", method, url, kwargs.get("data", ""))

        # Make request
        try:
            response = super().request(method, url, **kwargs)
            
            # Handle auth errors first, before any error raising
            if (response.status_code in (450, 500) and 'Authentication required' in response.text) or \
               (response.status_code == 401) or \
               (response.status_code == 403):
                request_logger.debug("Got auth error %d, attempting single refresh", response.status_code)
                
                # Clear existing tokens and cookies
                self.cookies.clear()
                self.service.session_data.clear()
                
                # Re-authenticate with force refresh
                self.service.authenticate(True)
                self._last_auth_time = datetime.now()
                
                # Retry the request once with fresh auth
                request = response.request
                if isinstance(request.body, bytes):
                    request.body = request.body.decode('utf-8')
                    
                # Update headers with new auth tokens
                request.headers.update(self._get_auth_headers())
                
                response = self.send(request)
                request_logger.debug("Retried request after auth refresh, status: %d", response.status_code)
                
                # If still failing after refresh, raise error
                if response.status_code >= 400:
                    self._raise_error(response.status_code, response.reason)
            
            # Handle 503 errors with minimal backoff
            elif response.status_code == 503:
                retry_after = min(int(response.headers.get('Retry-After', 2)), 5)  # Cap at 5 seconds
                request_logger.warning("Got 503, waiting %d seconds before retry", retry_after)
                time.sleep(retry_after)
                
                # Retry the request once
                request = response.request
                if isinstance(request.body, bytes):
                    request.body = request.body.decode('utf-8')
                response = self.send(request)
                request_logger.debug("Retried request after 503, status: %d", response.status_code)

            # Handle other response processing
            content_type = response.headers.get("Content-Type", "").split(";")[0]
            json_mimetypes = ["application/json", "text/json"]

            # Update session data from headers
            for header, value in HEADER_DATA.items():
                if response.headers.get(header):
                    session_arg = value
                    self.service.session_data.update(
                        {session_arg: response.headers.get(header)}
                    )

            # Save session data
            with open(self.service.session_path, "w", encoding="utf-8") as outfile:
                json.dump(self.service.session_data, outfile)
                request_logger.debug("Saved session data to file")

            # Save cookies
            self.cookies.save(ignore_discard=True, ignore_expires=True)
            request_logger.debug("Cookies saved to %s", self.service.cookiejar_path)

            # Now handle other errors
            if not response.ok and (
                content_type not in json_mimetypes
                or response.status_code in [421, 450, 500]
            ):
                request_logger.warning(
                    "Got error status %d: %s", 
                    response.status_code,
                    response.text if response.text else "No error message"
                )
                self._raise_error(response.status_code, response.reason)

            return response
            
        except Exception as e:
            request_logger.error("Request failed: %s", str(e))
            raise

    def _get_auth_headers(self):
        """Get current authentication headers"""
        headers = {}
        if self.service.session_data.get("session_token"):
            headers["X-Apple-Session-Token"] = self.service.session_data["session_token"]
        if self.service.session_data.get("scnt"):
            headers["scnt"] = self.service.session_data["scnt"]
        if self.service.session_data.get("session_id"):
            headers["X-Apple-ID-Session-Id"] = self.service.session_data["session_id"]
        return headers

    def _raise_error(self, code, reason):
        if (
            self.service.requires_2sa
            and reason == "Missing X-APPLE-WEBAUTH-TOKEN cookie"
        ):
            raise PyiCloud2SARequiredException(self.service.user["apple_id"])
        if code in ("ZONE_NOT_FOUND", "AUTHENTICATION_FAILED"):
            reason = (
                "Please log into https://icloud.com/ to manually "
                "finish setting up your iCloud service"
            )
            api_error = PyiCloudServiceNotActivatedException(reason, code)
            LOGGER.error(api_error)

            raise (api_error)
        if code == "ACCESS_DENIED":
            reason = (
                reason + ".  Please wait a few minutes then try again."
                "The remote servers might be trying to throttle requests."
            )
        if code in [421, 450, 500]:
            reason = "Authentication required for Account."

        api_error = PyiCloudAPIResponseException(reason, code)
        LOGGER.error(api_error)
        raise api_error


class PyiCloudService:
    """
    A base authentication class for the iCloud service. Handles the
    authentication required to access iCloud services.

    Usage:
        from pyicloud import PyiCloudService
        pyicloud = PyiCloudService('username@apple.com', 'password')
        pyicloud.iphone.location()
    """

    AUTH_ENDPOINT = "https://idmsa.apple.com/appleauth/auth"
    HOME_ENDPOINT = "https://www.icloud.com"
    SETUP_ENDPOINT = "https://setup.icloud.com/setup/ws/1"

    def __init__(
        self,
        apple_id,
        password=None,
        cookie_directory=None,
        verify=True,
        client_id=None,
        with_family=True,
        china_mainland=False,
        trust_data=None,
    ):
        # If the country or region setting of your Apple ID is China mainland.
        # See https://support.apple.com/en-us/HT208351
        if china_mainland:
            self.AUTH_ENDPOINT = "https://idmsa.apple.com.cn/appleauth/auth"
            self.HOME_ENDPOINT = "https://www.icloud.com.cn"
            self.SETUP_ENDPOINT = "https://setup.icloud.com.cn/setup/ws/1"

        if password is None:
            password = get_password_from_keyring(apple_id)

        self.user = {"accountName": apple_id, "password": password}
        self.data = {}
        self.params = {}
        self.client_id = client_id or ("auth-%s" % str(uuid1()).lower())
        self.with_family = with_family

        self.password_filter = PyiCloudPasswordFilter(password)
        LOGGER.addFilter(self.password_filter)

        if cookie_directory:
            self._cookie_directory = path.expanduser(path.normpath(cookie_directory))
            if not path.exists(self._cookie_directory):
                mkdir(self._cookie_directory, 0o700)
        else:
            topdir = path.join(gettempdir(), "pyicloud")
            self._cookie_directory = path.join(topdir, getpass.getuser())
            if not path.exists(topdir):
                mkdir(topdir, 0o777)
            if not path.exists(self._cookie_directory):
                mkdir(self._cookie_directory, 0o700)

        LOGGER.debug("Using session file %s", self.session_path)

        self.session_data = {}
        try:
            with open(self.session_path, encoding="utf-8") as session_f:
                self.session_data = json.load(session_f)
        except:  # pylint: disable=bare-except
            LOGGER.info("Session file does not exist")
            
        # Update session data with trust data if provided
        if trust_data:
            LOGGER.debug("Using provided trust data")
            self.session_data.update(trust_data)
            
        if self.session_data.get("client_id"):
            self.client_id = self.session_data.get("client_id")
        else:
            self.session_data.update({"client_id": self.client_id})

        self.session = PyiCloudSession(self)
        self.session.verify = verify
        self.session.headers.update(
            {"Origin": self.HOME_ENDPOINT, "Referer": "%s/" % self.HOME_ENDPOINT}
        )

        cookiejar_path = self.cookiejar_path
        self.session.cookies = cookielib.LWPCookieJar(filename=cookiejar_path)
        if path.exists(cookiejar_path):
            try:
                self.session.cookies.load(ignore_discard=True, ignore_expires=True)
                LOGGER.debug("Read cookies from %s", cookiejar_path)
            except:  # pylint: disable=bare-except
                # Most likely a pickled cookiejar from earlier versions.
                # The cookiejar will get replaced with a valid one after
                # successful authentication.
                LOGGER.warning("Failed to read cookiejar %s", cookiejar_path)

        self.authenticate()
        self._webservices = self.data["webservices"]
        
        self._drive = None
        self._files = None
        self._photos = None

    def _get_auth_retry_strategy(self):
        """Get a retry strategy specifically for authentication."""
        return Retry(
            total=5,  # Increased total retries
            backoff_factor=1.5,  # Exponential backoff
            status_forcelist=[500, 502, 503, 504],
            allowed_methods=["POST", "GET"],
            respect_retry_after_header=True,
            raise_on_status=False
        )

    def _setup_authentication_session(self):
        """Set up session with proper retry handling for authentication."""
        # Create an adapter with our custom retry strategy
        adapter = HTTPAdapter(max_retries=self._get_auth_retry_strategy())
        
        # Mount it for both http and https
        self.session.mount("https://", adapter)
        self.session.mount("http://", adapter)
        
        # Set reasonable timeouts
        self.session.timeout = (30, 90)  # (connect, read) timeouts in seconds

    def authenticate(self, force_refresh=False, service=None):
        """Authenticate or re-authenticate to iCloud."""
        
        # Set up authentication-specific session handling first
        self._setup_authentication_session()
        
        if force_refresh:
            LOGGER.debug("Forcing authentication refresh")
            self.session.cookies.clear()
            self.session.headers.clear()
            # Don't clear session data on force refresh if we have a trust token
            if not self.session_data.get("trust_token"):
                self.session_data.clear()
            self.session.headers.update({
                'Origin': self.HOME_ENDPOINT,
                'Referer': f"{self.HOME_ENDPOINT}/",
                'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko)'
            })

        login_successful = False
        if self.session_data.get("session_token") and not force_refresh:
            LOGGER.debug("Checking session token validity")
            try:
                self.data = self._validate_token()
                login_successful = True
                LOGGER.debug("Existing session token is valid")
            except PyiCloudAPIResponseException:
                LOGGER.debug("Invalid authentication token, will log in from scratch")
                self.session_data.clear()
                self.session.cookies.clear()

        if not login_successful and service is not None:
            LOGGER.debug("Attempting service-specific authentication for %s", service)
            app = self.data.get("apps", {}).get(service)
            if app and app.get("canLaunchWithOneFactor"):
                LOGGER.debug(
                    "Authenticating as %s for %s", self.user["accountName"], service
                )
                try:
                    self._authenticate_with_credentials_service(service)
                    login_successful = True
                    LOGGER.debug("Service-specific authentication successful")
                except Exception as e:
                    LOGGER.debug(
                        "Service-specific authentication failed: %s. Attempting full login.", str(e)
                    )

        if not login_successful:
            LOGGER.debug("Performing full authentication as %s", self.user["accountName"])

            data = dict(self.user)
            data["rememberMe"] = True
            data["trustTokens"] = []
            if self.session_data.get("trust_token"):
                data["trustTokens"] = [self.session_data.get("trust_token")]

            headers = self._get_auth_headers()

            if self.session_data.get("scnt"):
                headers["scnt"] = self.session_data.get("scnt")

            if self.session_data.get("session_id"):
                headers["X-Apple-ID-Session-Id"] = self.session_data.get("session_id")

            try:
                # First try validating existing session
                if not force_refresh and self.session_data:
                    try:
                        self.data = self._validate_token()
                        login_successful = True
                        LOGGER.debug("Existing session validated successfully")
                    except:
                        LOGGER.debug("Session validation failed, proceeding with full login")

                if not login_successful:
                    LOGGER.debug("Performing full sign-in")
                    
                    # The retry logic is now handled by the session's retry strategy
                    response = self.session.post(
                        "%s/signin" % self.AUTH_ENDPOINT,
                        params={"isRememberMeEnabled": "true"},
                        data=json.dumps(data),
                        headers=headers,
                    )
                    
                    response.raise_for_status()
                    self._authenticate_with_token()
                    LOGGER.debug("Full authentication completed successfully")

            except PyiCloudAPIResponseException as error:
                msg = "Invalid email/password combination."
                raise PyiCloudFailedLoginException(msg, error) from error

        self._webservices = self.data["webservices"]
        LOGGER.debug("Authentication completed successfully")

    def _authenticate_with_token(self):
        """Authenticate using session token."""
        data = {
            "accountCountryCode": self.session_data.get("account_country"),
            "dsWebAuthToken": self.session_data.get("session_token"),
            "extended_login": True,
            "trustToken": self.session_data.get("trust_token", ""),
        }

        try:
            req = self.session.post(
                "%s/accountLogin" % self.SETUP_ENDPOINT, data=json.dumps(data)
            )
            self.data = req.json()
        except PyiCloudAPIResponseException as error:
            msg = "Invalid authentication token."
            raise PyiCloudFailedLoginException(msg, error) from error

    def _authenticate_with_credentials_service(self, service):
        """Authenticate to a specific service using credentials."""
        
        # Set up authentication-specific session handling first
        self._setup_authentication_session()
        
        data = {
            "appName": service,
            "apple_id": self.user["accountName"],
            "password": self.user["password"],
            "extended_login": True,
        }

        # Add service-specific parameters
        if service == "reminders":
            data.update({
                "clientBuildNumber": "2023Project70",
                "clientMasteringNumber": "2023B70",
                "clientId": self.client_id,
                "dsid": self.data.get("dsInfo", {}).get("dsid"),
                "remindersWebUIVersion": "2.0",
                "usertz": get_localzone_name(),
            })
            
            # Add iOS 13+ specific headers
            self.session.headers.update({
                "X-Apple-I-FD-Client-Info": "{\"app\":{\"name\":\"reminders\",\"version\":\"2.0\"}}",
                "X-Apple-App-Version": "2.0",
                "X-Apple-I-TimeZone": get_localzone_name(),
                "X-Apple-I-ClientTime": datetime.now().strftime("%Y-%m-%dT%H:%M:%SZ"),
            })

        try:
            # First try validating existing session
            if self.session_data.get("session_token"):
                try:
                    self.data = self._validate_token()
                    return
                except PyiCloudAPIResponseException:
                    LOGGER.debug("Session validation failed, proceeding with service auth")
                    self.session_data.clear()
                    self.session.cookies.clear()

            # Perform service-specific authentication
            # The retry logic is now handled by the session's retry strategy
            response = self.session.post(
                "%s/accountLogin" % self.SETUP_ENDPOINT,
                data=json.dumps(data),
                headers=self._get_auth_headers()
            )

            self.data = response.json()

            # Update session data
            self.session_data.update({
                "session_token": response.headers.get("X-Apple-Session-Token"),
                "scnt": response.headers.get("scnt"),
                "session_id": response.headers.get("X-Apple-ID-Session-Id"),
            })

            # Save session data
            with open(self.session_path, "w", encoding="utf-8") as outfile:
                json.dump(self.session_data, outfile)

            # Save cookies
            self.session.cookies.save(ignore_discard=True, ignore_expires=True)

        except PyiCloudAPIResponseException as error:
            msg = "Invalid email/password combination."
            raise PyiCloudFailedLoginException(msg, error) from error

    def _validate_token(self):
        """Checks if the current access token is still valid."""
        LOGGER.debug("Checking session token validity")
        
        # Set up authentication-specific session handling first
        self._setup_authentication_session()
        
        try:
            # The retry logic is now handled by the session's retry strategy
            req = self.session.post("%s/validate" % self.SETUP_ENDPOINT, data="null")
            LOGGER.debug("Session token is still valid")
            return req.json()
        except PyiCloudAPIResponseException as err:
            LOGGER.debug("Invalid authentication token")
            raise err

    def _get_auth_headers(self, overrides=None):
        headers = {
            "Accept": "*/*",
            "Content-Type": "application/json",
            "X-Apple-OAuth-Client-Id": "d39ba9916b7251055b22c7f910e2ea796ee65e98b2ddecea8f5dde8d9d1a815d",
            "X-Apple-OAuth-Client-Type": "firstPartyAuth",
            "X-Apple-OAuth-Redirect-URI": "https://www.icloud.com",
            "X-Apple-OAuth-Require-Grant-Code": "true",
            "X-Apple-OAuth-Response-Mode": "web_message",
            "X-Apple-OAuth-Response-Type": "code",
            "X-Apple-OAuth-State": self.client_id,
            "X-Apple-Widget-Key": "d39ba9916b7251055b22c7f910e2ea796ee65e98b2ddecea8f5dde8d9d1a815d",
        }
        if overrides:
            headers.update(overrides)
        return headers

    @property
    def cookiejar_path(self):
        """Get path for cookiejar file."""
        return path.join(
            self._cookie_directory,
            "".join([c for c in self.user.get("accountName") if match(r"\w", c)]),
        )

    @property
    def session_path(self):
        """Get path for session data file."""
        return path.join(
            self._cookie_directory,
            "".join([c for c in self.user.get("accountName") if match(r"\w", c)])
            + ".session",
        )

    @property
    def requires_2sa(self):
        """Returns True if two-step authentication is required."""
        return self.data.get("dsInfo", {}).get("hsaVersion", 0) >= 1 and (
            self.data.get("hsaChallengeRequired", False) or not self.is_trusted_session
        )

    @property
    def requires_2fa(self):
        """Returns True if two-factor authentication is required."""
        return self.data["dsInfo"].get("hsaVersion", 0) == 2 and (
            self.data.get("hsaChallengeRequired", False) or not self.is_trusted_session
        )

    @property
    def is_trusted_session(self):
        """Returns True if the session is trusted."""
        return self.data.get("hsaTrustedBrowser", False)

    @property
    def trusted_devices(self):
        """Returns devices trusted for two-step authentication."""
        request = self.session.get(
            "%s/listDevices" % self.SETUP_ENDPOINT, params=self.params
        )
        return request.json().get("devices")

    def send_verification_code(self, device):
        """Requests that a verification code is sent to the given device."""
        data = json.dumps(device)
        request = self.session.post(
            "%s/sendVerificationCode" % self.SETUP_ENDPOINT,
            params=self.params,
            data=data,
        )
        return request.json().get("success", False)

    def validate_verification_code(self, device, code):
        """Verifies a verification code received on a trusted device."""
        device.update({"verificationCode": code, "trustBrowser": True})
        data = json.dumps(device)

        try:
            self.session.post(
                "%s/validateVerificationCode" % self.SETUP_ENDPOINT,
                params=self.params,
                data=data,
            )
        except PyiCloudAPIResponseException as error:
            if error.code == -21669:
                # Wrong verification code
                return False
            raise

        self.trust_session()

        return not self.requires_2sa

    def validate_2fa_code(self, code):
        """Verifies a verification code received via Apple's 2FA system (HSA2)."""
        data = {"securityCode": {"code": code}}

        headers = self._get_auth_headers({"Accept": "application/json"})

        if self.session_data.get("scnt"):
            headers["scnt"] = self.session_data.get("scnt")

        if self.session_data.get("session_id"):
            headers["X-Apple-ID-Session-Id"] = self.session_data.get("session_id")

        try:
            self.session.post(
                "%s/verify/trusteddevice/securitycode" % self.AUTH_ENDPOINT,
                data=json.dumps(data),
                headers=headers,
            )
        except PyiCloudAPIResponseException as error:
            if error.code == -21669:
                # Wrong verification code
                LOGGER.error("Code verification failed.")
                return False
            raise

        LOGGER.debug("Code verification successful.")

        self.trust_session()
        return not self.requires_2sa

    def trust_session(self):
        """Request session trust to avoid user log in going forward."""
        headers = self._get_auth_headers()

        if self.session_data.get("scnt"):
            headers["scnt"] = self.session_data.get("scnt")

        if self.session_data.get("session_id"):
            headers["X-Apple-ID-Session-Id"] = self.session_data.get("session_id")

        try:
            self.session.get(
                f"{self.AUTH_ENDPOINT}/2sv/trust",
                headers=headers,
            )
            self._authenticate_with_token()
            return True
        except PyiCloudAPIResponseException:
            LOGGER.error("Session trust failed.")
            return False

    def _get_webservice_url(self, ws_key):
        """Get webservice URL, raise an exception if not exists."""
        LOGGER.debug("Getting webservice URL for %s", ws_key)
        if self._webservices.get(ws_key) is None:
            raise PyiCloudServiceNotActivatedException(
                "Webservice not available", ws_key
            )
        return self._webservices[ws_key]["url"]

    @property
    def devices(self):
        """Returns all devices."""
        service_root = self._get_webservice_url("findme")
        return FindMyiPhoneServiceManager(
            service_root, self.session, self.params, self.with_family
        )

    @property
    def iphone(self):
        """Returns the iPhone."""
        return self.devices[0]

    @property
    def account(self):
        """Gets the 'Account' service."""
        service_root = self._get_webservice_url("account")
        return AccountService(service_root, self.session, self.params)

    @property
    def files(self):
        """Gets the 'File' service."""
        if not self._files:
            service_root = self._get_webservice_url("ubiquity")
            self._files = UbiquityService(service_root, self.session, self.params)
        return self._files

    @property
    def photos(self):
        """Gets the 'Photo' service."""
        if not self._photos:
            service_root = self._get_webservice_url("ckdatabasews")
            self._photos = PhotosService(service_root, self.session, self.params)
        return self._photos

    @property
    def calendar(self):
        """Gets the 'Calendar' service."""
        service_root = self._get_webservice_url("calendar")
        return CalendarService(service_root, self.session, self.params)

    @property
    def contacts(self):
        """Gets the 'Contacts' service."""
        service_root = self._get_webservice_url("contacts")
        return ContactsService(service_root, self.session, self.params)

    @property
    def reminders(self):
        """Gets the 'Reminders' service."""
        LOGGER.debug("Initializing reminders service")
        service_root = self._get_webservice_url("reminders")
        LOGGER.debug("Got reminders service root: %s", service_root)
        return RemindersService(service_root, self.session, self.params)

    @property
    def drive(self):
        """Gets the 'Drive' service."""
        if not self._drive:
            self._drive = DriveService(
                service_root=self._get_webservice_url("drivews"),
                document_root=self._get_webservice_url("docws"),
                session=self.session,
                params=self.params,
            )
        return self._drive

    @property
    def notes(self):
        """Gets the 'Notes' service."""
        service_root = self._get_webservice_url("notes")
        return NotesService(
            session=self.session,
            service_root=service_root,
            max_retries=3
        )

    def __str__(self):
        return f"iCloud API: {self.user.get('apple_id')}"

    def __repr__(self):
        return f"<{self}>"
