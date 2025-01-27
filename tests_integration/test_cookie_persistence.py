import os
import pickle
from pyicloud import PyiCloudService
import logging
from dotenv import load_dotenv
from http.cookiejar import Cookie

# Load environment variables
load_dotenv()

# Set up logging
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

def cookie_to_dict(cookie):
    """Convert a Cookie object to a dictionary"""
    return {
        'version': cookie.version,
        'name': cookie.name,
        'value': cookie.value,
        'port': cookie.port,
        'port_specified': cookie.port_specified,
        'domain': cookie.domain,
        'domain_specified': cookie.domain_specified,
        'domain_initial_dot': cookie.domain_initial_dot,
        'path': cookie.path,
        'path_specified': cookie.path_specified,
        'secure': cookie.secure,
        'expires': cookie.expires,
        'discard': cookie.discard,
        'comment': cookie.comment,
        'comment_url': cookie.comment_url,
        'rest': {},  # Required by Cookie constructor
        '_rest': cookie._rest
    }

def dict_to_cookie(cookie_dict):
    """Convert a dictionary back to a Cookie object"""
    rest = cookie_dict.pop('_rest', {})
    cookie = Cookie(**cookie_dict)
    cookie._rest = rest  # Restore any extra attributes
    return cookie

def test_cookie_persistence():
    """Test accessing services with persisted cookies"""
    cookie_file = "icloud_cookies.pkl"
    
    # Delete cookie file if it exists (start fresh)
    if os.path.exists(cookie_file):
        os.remove(cookie_file)
        logger.info("Deleted existing cookie file")
    
    # Do fresh login and save cookies
    logger.info("Performing fresh login...")
    api = PyiCloudService(os.environ.get("ICLOUD_USERNAME"), 
                        os.environ.get("ICLOUD_PASSWORD"))
    
    # Convert cookies to dictionaries for pickling
    cookie_dicts = [cookie_to_dict(cookie) for cookie in api.session.cookies]
    logger.info(f"Found {len(cookie_dicts)} cookies")
    
    # Save cookies
    with open(cookie_file, 'wb') as f:
        pickle.dump(cookie_dicts, f)
    logger.info("Saved cookies to file")
    
    # Create new session with saved cookies
    logger.info("Creating new session...")
    new_api = PyiCloudService(os.environ.get("ICLOUD_USERNAME"), 
                         os.environ.get("ICLOUD_PASSWORD"))
    
    # Load saved cookies
    with open(cookie_file, 'rb') as f:
        loaded_cookie_dicts = pickle.load(f)
    logger.info(f"Loaded {len(loaded_cookie_dicts)} cookies from file")
    
    # Add cookies to new session
    for cookie_dict in loaded_cookie_dicts:
        cookie = dict_to_cookie(cookie_dict)
        new_api.session.cookies.set_cookie(cookie)
        logger.debug(f"Added cookie: {cookie.name} = {cookie.value[:10]}...")
    
    # Try accessing services
    try:
        logger.info("Attempting to access reminders...")
        reminders = new_api.reminders
        lists = reminders.lists()
        print(f"Found {len(lists)} reminder lists")
    except Exception as e:
        logger.error(f"Failed to access reminders: {e}") 