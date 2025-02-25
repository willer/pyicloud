#! /usr/bin/env python
"""
A Command Line Wrapper to allow easy use of pyicloud for
command line scripts, and related.
"""
import argparse
import pickle
import sys
import os
from click import confirm
from dotenv import load_dotenv

from pyicloud import PyiCloudService
from pyicloud.exceptions import PyiCloudFailedLoginException
from . import utils

# Load environment variables from .env file
load_dotenv()

DEVICE_ERROR = "Please use the --device switch to indicate which device to use."


def create_pickled_data(idevice, filename):
    """
    This helper will output the idevice to a pickled file named
    after the passed filename.

    This allows the data to be used without resorting to screen / pipe
    scrapping.
    """
    with open(filename, "wb") as pickle_file:
        pickle.dump(idevice.content, pickle_file, protocol=pickle.HIGHEST_PROTOCOL)


def main(args=None):
    """Main commandline entrypoint."""
    if args is None:
        args = sys.argv[1:]

    parser = argparse.ArgumentParser(description="Find My iPhone CommandLine Tool")

    parser.add_argument(
        "--username",
        action="store",
        dest="username",
        default=os.environ.get("ICLOUD_USERNAME", ""),
        help="Apple ID to Use (can also be set via ICLOUD_USERNAME env var)",
    )
    parser.add_argument(
        "--password",
        action="store",
        dest="password",
        default=os.environ.get("ICLOUD_PASSWORD", ""),
        help=(
            "Apple ID Password to Use (can also be set via ICLOUD_PASSWORD env var); "
            "if unspecified, password will be fetched from the system keyring."
        ),
    )
    parser.add_argument(
        "--china-mainland",
        action="store_true",
        dest="china_mainland",
        default=False,
        help="If the country/region setting of the Apple ID is China mainland"
    )
    parser.add_argument(
        "-n",
        "--non-interactive",
        action="store_false",
        dest="interactive",
        default=True,
        help="Disable interactive prompts.",
    )
    parser.add_argument(
        "--delete-from-keyring",
        action="store_true",
        dest="delete_from_keyring",
        default=False,
        help="Delete stored password in system keyring for this username.",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        dest="list",
        default=False,
        help="Short Listings for Device(s) associated with account",
    )
    parser.add_argument(
        "--llist",
        action="store_true",
        dest="longlist",
        default=False,
        help="Detailed Listings for Device(s) associated with account",
    )
    parser.add_argument(
        "--locate",
        action="store_true",
        dest="locate",
        default=False,
        help="Retrieve Location for the iDevice (non-exclusive).",
    )

    # Restrict actions to a specific devices UID / DID
    parser.add_argument(
        "--device",
        action="store",
        dest="device_id",
        default=False,
        help="Only effect this device",
    )

    # Trigger Sound Alert
    parser.add_argument(
        "--sound",
        action="store_true",
        dest="sound",
        default=False,
        help="Play a sound on the device",
    )

    # Trigger Message w/Sound Alert
    parser.add_argument(
        "--message",
        action="store",
        dest="message",
        default=False,
        help="Optional Text Message to display with a sound",
    )

    # Trigger Message (without Sound) Alert
    parser.add_argument(
        "--silentmessage",
        action="store",
        dest="silentmessage",
        default=False,
        help="Optional Text Message to display with no sounds",
    )

    # Lost Mode
    parser.add_argument(
        "--lostmode",
        action="store_true",
        dest="lostmode",
        default=False,
        help="Enable Lost mode for the device",
    )
    parser.add_argument(
        "--lostphone",
        action="store",
        dest="lost_phone",
        default=False,
        help="Phone Number allowed to call when lost mode is enabled",
    )
    parser.add_argument(
        "--lostpassword",
        action="store",
        dest="lost_password",
        default=False,
        help="Forcibly active this passcode on the idevice",
    )
    parser.add_argument(
        "--lostmessage",
        action="store",
        dest="lost_message",
        default="",
        help="Forcibly display this message when activating lost mode.",
    )

    # Output device data to an pickle file
    parser.add_argument(
        "--outputfile",
        action="store_true",
        dest="output_to_file",
        default="",
        help="Save device data to a file in the current directory.",
    )

    command_line = parser.parse_args(args)

    username = command_line.username
    password = command_line.password
    china_mainland = command_line.china_mainland

    if username and command_line.delete_from_keyring:
        utils.delete_password_in_keyring(username)
        utils.delete_trust_token(username)

    failure_count = 0
    while True:
        if not username:
            parser.error("No username supplied")

        if not password:
            password = utils.get_password(
                username, interactive=command_line.interactive
            )

        if not password:
            parser.error("No password supplied")

        try:
            # Try to get stored trust token
            trust_data = utils.get_trust_token(username)
            
            api = PyiCloudService(
                username.strip(), 
                password.strip(), 
                china_mainland=china_mainland,
                trust_data=trust_data
            )
            
            # Handle keyring storage first
            if (
                not utils.password_exists_in_keyring(username)
                and command_line.interactive
                and confirm("Save password in keyring?")
            ):
                utils.store_password_in_keyring(username, password)

            # Handle 2FA before proceeding with any service calls
            auth_successful = False
            while not auth_successful:
                if api.requires_2fa:
                    print("\nTwo-factor authentication required. Please enter validation code:")
                    code = input("Code: ")
                    if not api.validate_2fa_code(code):
                        print("Failed to verify verification code")
                        if not command_line.interactive:
                            sys.exit(1)
                        continue
                    print("Successfully verified 2FA code")
                    
                    # Store trust data after successful 2FA
                    if api.session_data.get("trust_token"):
                        utils.store_trust_token(username, {
                            "trust_token": api.session_data["trust_token"],
                            "session_token": api.session_data.get("session_token"),
                            "scnt": api.session_data.get("scnt"),
                            "session_id": api.session_data.get("session_id")
                        })
                    
                    auth_successful = True

                elif api.requires_2sa:
                    print("\nTwo-step authentication required. Your trusted devices are:")
                    devices = api.trusted_devices
                    for i, device in enumerate(devices):
                        print(
                            "    %s: %s"
                            % (
                                i,
                                device.get(
                                    "deviceName", "SMS to %s" % device.get("phoneNumber")
                                ),
                            )
                        )

                    device_index = int(input("\nWhich device would you like to use? (number) --> "))
                    device = devices[device_index]
                    if not api.send_verification_code(device):
                        print("Failed to send verification code")
                        if not command_line.interactive:
                            sys.exit(1)
                        continue

                    code = input("Please enter validation code received: ")
                    if not api.validate_verification_code(device, code):
                        print("Failed to verify verification code")
                        if not command_line.interactive:
                            sys.exit(1)
                        continue
                    print("Successfully verified 2SA code")
                    
                    # Store trust data after successful 2SA
                    if api.session_data.get("trust_token"):
                        utils.store_trust_token(username, {
                            "trust_token": api.session_data["trust_token"],
                            "session_token": api.session_data.get("session_token"),
                            "scnt": api.session_data.get("scnt"),
                            "session_id": api.session_data.get("session_id")
                        })
                    
                    auth_successful = True
                else:
                    auth_successful = True

            # Force a fresh authentication after 2FA
            if auth_successful:
                api.authenticate(force_refresh=True)

            # Now proceed with service calls
            if command_line.list:
                for dev in api.devices:
                    print(dev)
                break

            for dev in api.devices:
                if not command_line.device_id or (
                    command_line.device_id.strip().lower() == dev.content["id"].strip().lower()
                ):
                    # List device(s)
                    if command_line.locate:
                        dev.location()

                    if command_line.output_to_file:
                        create_pickled_data(
                            dev,
                            filename=(dev.content["name"].strip().lower() + ".fmip_snapshot"),
                        )

                    contents = dev.content
                    if command_line.longlist:
                        print("-" * 30)
                        print(contents["name"])
                        for key in contents:
                            print("%20s - %s" % (key, contents[key]))
                    elif command_line.list:
                        print("-" * 30)
                        print("Name - %s" % contents["name"])
                        print("Display Name  - %s" % contents["deviceDisplayName"])
                        print("Location      - %s" % contents["location"])
                        print("Battery Level - %s" % contents["batteryLevel"])
                        print("Battery Status- %s" % contents["batteryStatus"])
                        print("Device Class  - %s" % contents["deviceClass"])
                        print("Device Model  - %s" % contents["deviceModel"])

                    # Play a Sound on a device
                    if command_line.sound:
                        if command_line.device_id:
                            dev.play_sound()
                        else:
                            raise RuntimeError(
                                "\n\n\t\t%s %s\n\n"
                                % (
                                    "Sounds can only be played on a singular device.",
                                    DEVICE_ERROR,
                                )
                            )

                    # Display a Message on the device
                    if command_line.message:
                        if command_line.device_id:
                            dev.display_message(
                                subject="A Message", message=command_line.message, sounds=True
                            )
                        else:
                            raise RuntimeError(
                                "%s %s"
                                % (
                                    "Messages can only be played on a singular device.",
                                    DEVICE_ERROR,
                                )
                            )

                    # Display a Silent Message on the device
                    if command_line.silentmessage:
                        if command_line.device_id:
                            dev.display_message(
                                subject="A Silent Message",
                                message=command_line.silentmessage,
                                sounds=False,
                            )
                        else:
                            raise RuntimeError(
                                "%s %s"
                                % (
                                    "Silent Messages can only be played "
                                    "on a singular device.",
                                    DEVICE_ERROR,
                                )
                            )

                    # Enable Lost mode
                    if command_line.lostmode:
                        if command_line.device_id:
                            dev.lost_device(
                                number=command_line.lost_phone.strip(),
                                text=command_line.lost_message.strip(),
                                newpasscode=command_line.lost_password.strip(),
                            )
                        else:
                            raise RuntimeError(
                                "%s %s"
                                % (
                                    "Lost Mode can only be activated on a singular device.",
                                    DEVICE_ERROR,
                                )
                            )
            break
        except PyiCloudFailedLoginException as err:
            # If they have a stored password; we just used it and
            # it did not work; let's delete it if there is one.
            if utils.password_exists_in_keyring(username):
                utils.delete_password_in_keyring(username)

            message = "Bad username or password for {username}".format(
                username=username,
            )
            password = None

            failure_count += 1
            if failure_count >= 3:
                raise RuntimeError(message) from err

            print(message, file=sys.stderr)

    sys.exit(0)


if __name__ == "__main__":
    main()
