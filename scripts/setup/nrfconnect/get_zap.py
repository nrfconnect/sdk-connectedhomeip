#!/usr/bin/env python3

#
# Copyright (c) 2024 Project CHIP Authors
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#

#
# The script parses integrations/docker/images/chip-build/Dockerfile file
# from the Matter repository and looks for ENV ZAP_VERSION= string to find
# currently recommended ZAP version. After that the package matching this version
# and currently used OS is downloaded from the ZAP release packages and extracted
# in the location given by the user.

import os
import stat
import re
import wget
import subprocess
import shutil
import platform
import argparse
from zipfile import ZipFile


def get_zap_recommended_version():
    matter_root = os.path.abspath(os.path.join(os.path.dirname(__file__), os.path.normpath('../../..')))
    zap_version_file = os.path.join(matter_root, 'scripts/tools/zap/zap_execution.py')

    try:
        with open(os.path.join(zap_version_file), 'r') as f:
            file_content = f.read()
            result = re.findall(r'MIN_ZAP_VERSION = \'(.*)\'\s', file_content)

            if len(result) == 0:
                raise RuntimeError("Couldn't find pattern matching ZAP_VERSION.")
            elif len(result) > 1:
                raise RuntimeError("Found multiple patterns matching ZAP_VERSION.")

            return result[0]
    except Exception as e:
        raise RuntimeError(
            f"Encountered problem when trying to read {zap_version_file} file. {e}")


def get_zap_current_version():
    try:
        cmd_out = subprocess.check_output(['zap', '--version'])
        cmd_decoded_out = cmd_out.decode('ascii').strip()
        version = re.findall(r'Version: (.*)\s', cmd_decoded_out)

        if len(version) == 0:
            raise RuntimeError("Couldn't find pattern matching Version:.")
        elif len(version) > 1:
            raise RuntimeError("Found multiple patterns matching Version:.")

        return version[0]
    except Exception as e:
        print(f"ZAP file not found {e}")
        return None


def download_recommended_zap_package(version, package_name, location):
    try:
        print("Trying to download ZAP tool package matching your system and recommended version.")
        os.makedirs(location, exist_ok=True)

        # The version format is year.month.day, where month and day may be 1 or 2 digits length.
        # The URL used for package download requires format YYYY.MM.DD.
        # To assure proper format, the code splits version groups and conditionally prepends day and months with 0s.
        splitted_version = version.split('.')

        if len(splitted_version) != 3:
            raise RuntimeError("ZAP version requested to download has invalid format.")

        month = f'{int(splitted_version[1]):02d}'
        day = f'{int(splitted_version[2]):02d}'
        merged_version = f"{splitted_version[0]}.{month}.{day}"
        url = f"https://github.com/project-chip/zap/releases/download/v{merged_version}-nightly/{package_name}.zip"
        print(f"Downloading {url} into {os.path.join(location, f'{package_name}.zip')}")
        wget.download(url, out=location)
        print("\n")

    except Exception as e:
        raise RuntimeError("Invalid URL to download ZAP tool package {}".format(e))


def clear_old_artifacts(location, overwrite):
    if os.path.exists(location):
        # Ask for user consent if the overwrite flag was not provided
        if not overwrite:
            consent = input("The ZAP directory already exists in this location. Do you agree to overwrite it? Yes[y]/No[n]:")
            if consent.lower() != 'yes' and consent.lower() != 'y':
                raise RuntimeError("Couldn't download ZAP package, as the file already exists in this location.")

        shutil.rmtree(location)


def remove_zip(location, package_name):
    path = os.path.join(location, f"{package_name}.zip")
    print(f"Deleting zip file: {path}")
    os.remove(path)


def set_executable(location, package_name, filename):
    file = os.path.join(location, package_name, filename)
    st = os.stat(file)
    os.chmod(file, st.st_mode | stat.S_IEXEC)


def unzip_zap_package(location, package_name):
    package = location + f"/{package_name}.zip"
    destination = location + "/" + package_name

    try:
        if (platform.system() == 'Darwin'):
            subprocess.check_call(['unzip', package, '-d', destination])
        else:
            zip = ZipFile(os.path.join(location, f"{package_name}.zip"))
            zip.extractall(os.path.join(location, package_name))
            zip.close()

    except Exception as e:
        raise RuntimeError("Encountered problem when trying to unzip the ZAP tool package. {}".format(e))

    finally:
        remove_zip(location, package_name)


def print_paths_warning(paths_to_print):
    messages = ["Please add the following location(s) to the system PATH:"] + paths_to_print
    longest_message = max(messages, key=len)

    for item in range(len(messages)):
        messages[item] = f"# {messages[item]}{(len(longest_message)-len(messages[item]))*' '} #"

    frame_message = f"{(len(longest_message)+4)*'#'}"

    print(f"\33[33m{frame_message}\x1b[0m")
    for message in messages:
        print(f"\33[33m{message}\x1b[0m")
    print(f"\33[33m{frame_message}\x1b[0m")


def install_zap_package(version, location, overwrite):
    current_os = platform.system()
    if current_os == 'Linux':
        package = 'zap-linux-x64'
        zap_executable = 'zap'
        zap_cli_executable = 'zap-cli'
    elif current_os == 'Windows':
        package = 'zap-win-x64'
        zap_executable = 'zap.exe'
        zap_cli_executable = 'zap-cli.exe'
    elif current_os == 'Darwin':
        package = 'zap-mac-x64'
        zap_executable = 'zap.app/Contents/MacOS/zap'
        zap_cli_executable = 'zap-cli'
    else:
        raise RuntimeError(f"Couldn't find the proper ZAP tool package for the currently used operating system: {current_os}")

    clear_old_artifacts(os.path.join(location, package), overwrite)
    download_recommended_zap_package(version, package, location)
    unzip_zap_package(location, package)
    set_executable(location, package, zap_executable)
    set_executable(location, package, zap_cli_executable)

    print("ZAP tool package was downloaded and extracted in the given location.")

    if current_os == 'Darwin':
        print_paths_warning([os.path.join(location, package, zap_executable), os.path.join(location, package)])
    else:
        print_paths_warning([os.path.join(location, package)])


def main():
    parser = argparse.ArgumentParser(description='Script helping to download the ZAP tool in the currently recommended revision.')
    parser.add_argument("-l", "--location", help="Path to the location that should be used for storing ZAP tool package.", type=str, required=True)
    parser.add_argument("-o", "--overwrite", help="Overwrite files without asking, in case they already exist in given location", action="store_true")
    args = parser.parse_args()

    location = os.path.abspath(args.location)
    zap_recommended_version = get_zap_recommended_version()
    zap_current_version = get_zap_current_version()

    if not zap_current_version:
        print("No ZAP tool version was found installed on this device.")
        install_zap_package(zap_recommended_version, location, args.overwrite)
    elif zap_current_version == zap_recommended_version:
        print(f"Your currenly installed ZAP tool version: {zap_current_version} matches the recommended one.")
    else:
        print(f"Your currenly installed ZAP tool version: {zap_current_version} does not match the recommended one: {zap_recommended_version}")
        install_zap_package(zap_recommended_version, location, args.overwrite)


if __name__ == '__main__':
    main()
