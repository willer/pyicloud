"""Utils."""
import getpass
import keyring
import sys
import json
from os import path
from tempfile import gettempdir

from .exceptions import PyiCloudNoStoredPasswordAvailableException


KEYRING_SYSTEM = "pyicloud://icloud-password"
TRUST_TOKEN_SYSTEM = "pyicloud://trust-token"


def get_password(username, interactive=sys.stdout.isatty()):
    """Get the password from a username."""
    try:
        return get_password_from_keyring(username)
    except PyiCloudNoStoredPasswordAvailableException:
        if not interactive:
            raise

        return getpass.getpass(
            "Enter iCloud password for {username}: ".format(
                username=username,
            )
        )


def password_exists_in_keyring(username):
    """Return true if the password of a username exists in the keyring."""
    try:
        get_password_from_keyring(username)
    except PyiCloudNoStoredPasswordAvailableException:
        return False

    return True


def get_password_from_keyring(username):
    """Get the password from a username."""
    result = keyring.get_password(KEYRING_SYSTEM, username)
    if result is None:
        raise PyiCloudNoStoredPasswordAvailableException(
            "No pyicloud password for {username} could be found "
            "in the system keychain.  Use the `--store-in-keyring` "
            "command-line option for storing a password for this "
            "username.".format(
                username=username,
            )
        )

    return result


def store_password_in_keyring(username, password):
    """Store the password of a username."""
    return keyring.set_password(
        KEYRING_SYSTEM,
        username,
        password,
    )


def delete_password_in_keyring(username):
    """Delete the password of a username."""
    return keyring.delete_password(
        KEYRING_SYSTEM,
        username,
    )


def underscore_to_camelcase(word, initial_capital=False):
    """Transform a word to camelCase."""
    words = [x.capitalize() or "_" for x in word.split("_")]
    if not initial_capital:
        words[0] = words[0].lower()

    return "".join(words)


def get_default_token_directory():
    """Get the default directory for storing trust tokens."""
    topdir = path.join(gettempdir(), "pyicloud")
    return path.join(topdir, getpass.getuser(), "tokens")


def get_token_path(username, token_directory=None):
    """Get the path for storing trust tokens."""
    if not token_directory:
        token_directory = get_default_token_directory()
    return path.join(token_directory, f"{username}.token")


def store_trust_token(username, trust_data, token_directory=None):
    """Store trust token data for a username."""
    token_path = get_token_path(username, token_directory)
    
    # Ensure directory exists
    directory = path.dirname(token_path)
    if not path.exists(directory):
        from os import makedirs
        makedirs(directory, mode=0o700, exist_ok=True)
    
    # Store token data
    with open(token_path, 'w') as f:
        json.dump(trust_data, f)


def get_trust_token(username, token_directory=None):
    """Get stored trust token data for a username."""
    token_path = get_token_path(username, token_directory)
    try:
        with open(token_path, 'r') as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def delete_trust_token(username, token_directory=None):
    """Delete stored trust token data for a username."""
    token_path = get_token_path(username, token_directory)
    try:
        from os import remove
        remove(token_path)
    except FileNotFoundError:
        pass
