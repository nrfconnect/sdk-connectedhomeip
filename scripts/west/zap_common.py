# Copyright (c) 2024 Nordic Semiconductor ASA
#
# SPDX-License-Identifier: LicenseRef-Nordic-5-Clause

import argparse
import json
import os
import platform
import re
import shutil
import signal
import stat
import subprocess
import tempfile
from collections import deque
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import ContextManager, Tuple
from zipfile import ZipFile

import wget
from west import log

DEFAULT_MATTER_PATH = Path(__file__).parents[2]
DEFAULT_ZCL_JSON_RELATIVE_PATH = Path('src/app/zap-templates/zcl/zcl.json')
DEFAULT_APP_TEMPLATES_RELATIVE_PATH = Path('src/app/zap-templates/app-templates.json')
DEFAULT_MATTER_TYPES_RELATIVE_PATH = Path('src/app/zap-templates/zcl/data-model/chip/chip-types.xml')
DEFAULT_ZAP_VERSION_RELATIVE_PATH = Path('scripts/setup/zap.version')
DEFAULT_ZAP_GENERATE_RELATIVE_PATH = Path('scripts/tools/zap/generate.py')
DEFAULT_RULES_RELATIVE_PATH = Path('src/app/zap-templates/zcl/upgrade-rules-matter.json')


def get_rules_path(matter_path: Path = DEFAULT_MATTER_PATH) -> Path:
    """
    Returns absolute path to the upgrade-rules-matter.json file within the Matter SDK.
    """
    return matter_path.joinpath(DEFAULT_RULES_RELATIVE_PATH).absolute()


def get_zap_generate_path(matter_path: Path = DEFAULT_MATTER_PATH) -> Path:
    """
    Returns absolute path to the zap-generate.py script within the Matter SDK.
    """
    return matter_path.joinpath(DEFAULT_ZAP_GENERATE_RELATIVE_PATH).absolute()


def get_app_templates_path(matter_path: Path = DEFAULT_MATTER_PATH) -> Path:
    """
    Returns absolute path to the app-templates.json file within the Matter SDK.
    """
    return matter_path.joinpath(DEFAULT_APP_TEMPLATES_RELATIVE_PATH).absolute()


def get_default_zcl_json_path(matter_path: Path = DEFAULT_MATTER_PATH) -> Path:
    """
    Returns absolute path to the default zcl.json file within the Matter SDK.
    """
    return matter_path.joinpath(DEFAULT_ZCL_JSON_RELATIVE_PATH).absolute()


def find_zap(root: Path = Path.cwd(), max_depth: int = 2):
    """
    Find *.zap file in the given directory or its subdirectories.
    """
    zap_files = []
    search_dirs = deque()
    search_dirs.append((root, max_depth))

    while search_dirs:
        search_dir, max_depth = search_dirs.popleft()

        for name in search_dir.iterdir():
            if name.is_file() and (name.suffix.lower() == '.zap'):
                zap_files.append(search_dir / name)
                continue
            if name.is_dir() and (max_depth > 0):
                search_dirs.append((search_dir / name, max_depth - 1))

    # At most one ZAP file found in the selected location, return immediately.
    if len(zap_files) <= 1:
        return zap_files[0] if zap_files else None

    # Otherwise, ask a user to choose the ZAP file to edit.
    for i, zap_file in enumerate(zap_files):
        print(f'{i}. {zap_file.relative_to(root)}')

    while True:
        try:
            maxind = len(zap_files) - 1
            prompt = f'Select file to edit (0-{maxind}): '
            return zap_files[int(input(prompt))]
        except Exception:
            pass


def existing_file_path(arg: str) -> Path:
    """
    Helper function to validate file path argument.
    """
    p = Path(arg)
    if p.is_file():
        return p
    raise argparse.ArgumentTypeError(f'invalid file path: \'{arg}\'')


def existing_dir_path(arg: str) -> Path:
    """
    Helper function to validate directory path argument.
    """
    p = Path(arg)
    if p.is_dir():
        return p
    raise argparse.ArgumentTypeError(f'invalid directory path: \'{arg}\'')


def update_zcl_in_zap(zap_file: Path, zcl_json: Path, app_templates: Path) -> bool:
    """
    In the .zap file, there is a relative path to the zcl.json file.
    Use this function to update zcl.json path if needed.
    Functions returns True if the path was updated, False otherwise.
    """
    updated = False
    zap_file = zap_file.resolve()
    zcl_json = zcl_json.resolve()
    app_templates = app_templates.resolve()

    with open(zap_file, 'r+') as file:
        data = json.load(file)
        packages = data.get("package")

        for package in packages:
            if package.get("type") == "zcl-properties":
                if zcl_json.parent.absolute() == zap_file.parent.absolute() or \
                        not zcl_json.parent.absolute().is_relative_to(zap_file.parent.absolute()):
                    try:
                        package.update({"path": str(zcl_json.absolute().relative_to(zap_file.parent.absolute(), walk_up=True))})
                        updated = True
                    except ValueError:
                        package.update({"path": str(zcl_json.absolute())})
                        updated = True

            if package.get("type") == "gen-templates-json":
                if app_templates.parent.absolute() == zap_file.parent.absolute() or \
                        not app_templates.parent.absolute().is_relative_to(zap_file.parent.absolute()):
                    try:
                        package.update({"path": str(app_templates.absolute().relative_to(zap_file.parent.absolute(), walk_up=True))})
                        updated = True
                    except ValueError:
                        package.update({"path": str(app_templates.absolute())})
                        updated = True

        file.seek(0)
        json.dump(data, file, indent=2)
        file.truncate()

    return updated


def post_process_generated_files(output_path: Path, base_dir: str):
    """
    Post-process the generated files:

    - Decode as utf-8, fallback to system default if needed
    - Ensure all files in output_path (recursively) have exactly one empty line at the end
    - If some files contains path to the local files, remove the absolute paths

    - The base_dir is used to find and clear all absolute paths to the local files.
    """
    for root, _, files in os.walk(output_path):
        for fname in files:
            file_path = os.path.join(root, fname)
            # Only process regular files (skip symlinks, etc.)
            if not os.path.isfile(file_path):
                continue
            try:
                with open(file_path, 'rb') as f:
                    content = f.read()
                # Decode as utf-8, fallback to system default if needed
                try:
                    text = content.decode('utf-8')
                except UnicodeDecodeError:
                    try:
                        text = content.decode()
                    except Exception:
                        continue  # skip non-text files

                # Remove all trailing newlines
                stripped = text.rstrip('\r\n')
                # Add exactly one newline
                new_text = stripped + '\n'

                # Check if the file contains absolute paths to .matter files
                lines = new_text.splitlines()
                for i, line in zip(range(20), lines):
                    # Check if line contains "based on" pattern with absolute path
                    if 'based on' in line:
                        # Remove the line if it contains '// based on' or '# based on'
                        if line.strip().startswith("// based on") or line.strip().startswith("# Cluster generated code for constants and metadata based on" or line.strip().startswith("# List of cluster")):
                            new_text = new_text.replace(line + '\n', '')

                if new_text != text:
                    with open(file_path, 'w', encoding='utf-8') as f:
                        f.write(new_text)
            except Exception:
                # Ignore files that can't be read/written as text
                continue


def update_rules_paths(zcl_json: Path) -> ContextManager[Path]:
    """
    Temporarily update all paths within the rules file to relative paths.

    The rules file contains paths relative to the zcl.json file.
    While updating the zap file with a custom zcl.json file (zap-gui or zap-sync) we need to use all rules scripts
    with relative paths to the new zcl.json file.

    This function returns a context manager that can be used with a "with" statement.
    The zcl.json file is temporarily updated with the new temporary rules file.
    The zcl.json file is restored to the original state when the context manager is exited.
    """
    class RulesPathContextManager:
        def __init__(self, zcl_json: Path):
            self.zcl_json = zcl_json
            self.source_path = get_rules_path(DEFAULT_MATTER_PATH)
            self.zcl = json.load(open(zcl_json, 'r+')) if zcl_json else None
            self.do_not_update_rules = True

            if self.zcl and self.zcl_json.absolute() != get_default_zcl_json_path(DEFAULT_MATTER_PATH):
                try:
                    self.original_upgrade_rules = self.zcl["upgradeRules"]
                    self.do_not_update_rules = False
                except KeyError:
                    self.do_not_update_rules = True

        def __enter__(self) -> Path:
            if self.do_not_update_rules:
                return None

            # Read the original rules file
            with open(self.source_path, 'r') as f:
                rules_data = json.load(f)

            # Get the parent directory of rules_path for relative path calculation
            rules_parent = self.source_path.parent

            # Update all paths in upgradeRuleScripts
            if "upgradeRuleScripts" in rules_data:
                for script in rules_data["upgradeRuleScripts"]:
                    if "path" in script:
                        original_path = Path(script["path"])

                        # If the path is absolute, make it relative to rules_parent
                        if original_path.is_absolute():
                            script["path"] = str(original_path.relative_to(rules_parent, walk_up=True))
                        else:
                            # If already relative, resolve it relative to rules_parent
                            # and then make it relative again
                            resolved_path = (rules_parent / original_path).resolve()
                            script["path"] = str(resolved_path)

            # Create a temporary file
            self.temp_file = NamedTemporaryFile(
                mode='w',
                suffix='.json',
                delete=False
            )
            self.temp_path = Path(self.temp_file.name)

            # Write updated JSON to temp file
            json.dump(rules_data, self.temp_file, indent=4)
            self.temp_file.close()

            # Update the upgrade rules path in the zcl.json file
            if self.zcl:
                self.zcl["upgradeRules"] = str(self.temp_path.relative_to(self.zcl_json.parent.absolute(), walk_up=True))
                with open(self.zcl_json, 'w') as f:
                    json.dump(self.zcl, f, indent=4)

            return self.temp_path

        def __exit__(self, exc_type, exc_val, exc_tb):
            if self.do_not_update_rules:
                return

            # Clean up the temporary file
            if self.temp_path and self.temp_path.exists():
                self.temp_path.unlink()

            # Restore the original upgrade rules path in the zcl.json file
            if self.zcl:
                self.zcl["upgradeRules"] = self.original_upgrade_rules
                with open(self.zcl_json, 'w') as f:
                    json.dump(self.zcl, f, indent=4)

            return False

    return RulesPathContextManager(zcl_json)


def display_zap_message(output: subprocess.CompletedProcess[str]) -> None:
    """
    If the output contains the error from napi_throw, suggest user to use zap-sync to sync the ZAP file.
    """
    if 'Unknown attribute' in output.stdout:
        log.err("Your zcl.json file seems to be outdated. Please use 'west zap-sync' command to synchronize it with the newest Matter Data Model.")
        return
    else:
        print(f"output: {output.stdout}")


class ZapInstaller:
    INSTALL_DIR = Path('.zap-install')
    ZAP_URL_PATTERN = 'https://github.com/project-chip/zap/releases/download/%s/%s.zip'

    def __init__(self, matter_path: Path):
        self.matter_path = matter_path
        self.install_path = matter_path / ZapInstaller.INSTALL_DIR
        self.current_os = platform.system()

        def unzip_darwin(zip: Path, out: Path):
            subprocess.check_call(['unzip', zip, '-d', out])

        def unzip(zip: Path, out: Path):
            f = ZipFile(zip)
            f.extractall(out)
            f.close()

        if self.current_os == 'Linux':
            if platform.machine() == 'aarch64':
                self.package = 'zap-linux-arm64'
            else:
                self.package = 'zap-linux-x64'
            self.zap_exe = 'zap'
            self.zap_cli_exe = 'zap-cli'
            self.unzip = unzip
        elif self.current_os == 'Windows':
            if platform.machine() == 'ARM64':
                self.package = 'zap-win-arm64'
            else:
                self.package = 'zap-win-x64'
            self.zap_exe = 'zap.exe'
            self.zap_cli_exe = 'zap-cli.exe'
            self.unzip = unzip
        elif self.current_os == 'Darwin':
            if platform.machine() == 'arm64':
                self.package = 'zap-mac-arm64'
            else:
                self.package = 'zap-mac-x64'
            self.zap_exe = 'zap.app/Contents/MacOS/zap'
            self.zap_cli_exe = 'zap-cli'
            self.unzip = unzip_darwin
        else:
            raise RuntimeError(f"Unsupported platform: {self.current_os}")

    def get_install_path(self) -> Path:
        """
        Returns ZAP package installation directory.
        """
        return self.install_path

    def get_zap_path(self) -> Path:
        """
        Returns path to ZAP GUI.
        """
        return self.install_path / self.zap_exe

    def get_zap_cli_path(self) -> Path:
        """
        Returns path to ZAP CLI.
        """
        return self.install_path / self.zap_cli_exe

    def get_recommended_version(self) -> str:
        """
        Returns ZAP package recommended version as a tuple of integers.

        Reads the version from zap.version file in Matter SDK.
        Expected format: v{YEAR}.{MONTH}.{DAY}[-suffix]
        Example: v2025.09.23-nightly
        """
        zap_version_path = self.matter_path / DEFAULT_ZAP_VERSION_RELATIVE_PATH

        with open(zap_version_path, 'r') as f:
            return f.read().strip()

    def get_current_version(self) -> str:
        """
        Returns ZAP package current version as a tuple of integers.

        Parses the output of `zap-cli --version` to determine the current ZAP
        package version. If the ZAP package has not been installed yet,
        the method returns None.
        """
        try:
            output = subprocess.check_output(
                [self.get_zap_cli_path(), '--version']).decode('ascii').strip()
        except Exception:
            return None

        RE_VERSION = r'Version:\s*(\d+)\.(\d+)\.(\d+)'
        if match := re.search(RE_VERSION, output):
            return match.group(1) + '.' + match.group(2) + '.' + match.group(3)

        raise RuntimeError("Failed to find version in ZAP output")

    def install_zap(self, version: Tuple[int, int, int]) -> None:
        """
        Downloads and unpacks selected ZAP package version.
        """
        with tempfile.TemporaryDirectory() as temp_dir:
            url = ZapInstaller.ZAP_URL_PATTERN % (version, self.package)
            log.inf(f'Downloading {url}...')
            zip_file_path = str(Path(temp_dir).joinpath(f'{self.package}.zip'))

            # Handle SIGINT and SIGTERM to clean up broken files if the user cancels
            # the installation
            def handle_signal(signum, frame):
                log.inf('\nCancelled by user, cleaning up...')
                shutil.rmtree(self.install_path, ignore_errors=True)
                exit()

            signal.signal(signal.SIGINT, handle_signal)
            signal.signal(signal.SIGTERM, handle_signal)

            try:
                wget.download(url, out=zip_file_path)
            except Exception as e:
                raise RuntimeError(f'Failed to download ZAP package from {url}: {e}')

            shutil.rmtree(self.install_path, ignore_errors=True)

            log.inf('')  # Fix console after displaying wget progress bar
            log.inf(f'Unzipping ZAP package to {self.install_path}...')

            try:
                self.unzip(zip_file_path, self.install_path)
            except Exception as e:
                raise RuntimeError(f'Failed to unzip ZAP package: {e}')

            ZapInstaller.set_exec_permission(self.get_zap_path())
            ZapInstaller.set_exec_permission(self.get_zap_cli_path())

    def update_zap_if_needed(self) -> None:
        """
        Installs ZAP package if not up to date.

        Installs or overrides the previous ZAP package installation if the
        current version does not match the recommended version.
        """
        recommended_version = self.get_recommended_version()
        current_version = self.get_current_version()

        # Extract version without prefix and suffix for comparison
        # recommended_version format: v2025.09.23-nightly or v2025.09.23
        # current_version format: 2025.9.23
        recommended_version_clean = recommended_version
        if match := re.search(r'v?(\d+\.\d+\.\d+)', recommended_version):
            year, month, day = match.group(1).split('.')
            recommended_version_clean = f"{int(year)}.{int(month)}.{int(day)}"

            log.inf(f'ZAP installation directory: {self.install_path}')
            log.inf(f"Current ZAP version: {current_version}")

            if current_version:
                verdict = 'up to date' if current_version == recommended_version_clean else 'outdated'
                log.inf('Found ZAP {} ({})'.format(current_version, verdict))

            if current_version != recommended_version_clean:
                log.inf('Installing ZAP {}'.format(recommended_version))
                self.install_zap(recommended_version)

    @staticmethod
    def set_exec_permission(path: Path) -> None:
        os.chmod(path, os.stat(path).st_mode | stat.S_IEXEC)


def fix_sandbox_permissions(e: subprocess.CalledProcessError, zap_installer: ZapInstaller) -> None:
    """
    Fix sandbox permissions if needed.
    """
    if e.returncode == -5:
        log.inf("\nThe wrong sandbox permissions are used. Do you wanto to add the permissions to the sandbox?")
        answer = input("y/n: ")
        if answer == "y":
            subprocess.check_call(['sudo', 'chown', 'root', str(
                zap_installer.get_install_path().absolute() / 'chrome-sandbox')])
            subprocess.check_call(['sudo', 'chmod', '4755', str(zap_installer.get_install_path().absolute() / 'chrome-sandbox')])
            log.inf("Permissions added to the sandbox")
