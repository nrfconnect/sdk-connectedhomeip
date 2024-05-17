# Copyright (c) 2024 Nordic Semiconductor ASA
#
# SPDX-License-Identifier: LicenseRef-Nordic-5-Clause

import argparse
import os
import platform
import re
import shutil
import stat
import subprocess
import tempfile
import wget

from collections import deque
from pathlib import Path
from typing import Tuple
from zipfile import ZipFile

from west import log

DEFAULT_MATTER_PATH = Path(__file__).parents[2]


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


class ZapInstaller:
    INSTALL_DIR = Path('.zap-install')
    ZAP_URL_PATTERN = 'https://github.com/project-chip/zap/releases/download/v%04d.%02d.%02d-nightly/%s.zip'

    def __init__(self, matter_path: Path):
        self.matter_path = matter_path
        self.install_path = matter_path / ZapInstaller.INSTALL_DIR

        def unzip_darwin(zip: Path, out: Path):
            subprocess.check_call(['unzip', zip, '-d', out])

        def unzip(zip: Path, out: Path):
            f = ZipFile(zip)
            f.extractall(out)
            f.close()

        current_os = platform.system()
        if current_os == 'Linux':
            self.package = 'zap-linux-x64'
            self.zap_exe = 'zap'
            self.zap_cli_exe = 'zap-cli'
            self.unzip = unzip
        elif current_os == 'Windows':
            self.package = 'zap-win-x64'
            self.zap_exe = 'zap.exe'
            self.zap_cli_exe = 'zap-cli.exe'
            self.unzip = unzip
        elif current_os == 'Darwin':
            self.package = 'zap-mac-x64'
            self.zap_exe = 'zap.app/Contents/MacOS/zap'
            self.zap_cli_exe = 'zap-cli'
            self.unzip = unzip_darwin
        else:
            raise RuntimeError(f"Unsupported platform: {current_os}")

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

    def get_recommended_version(self) -> Tuple[int, int, int]:
        """
        Returns ZAP package recommended version as a tuple of integers.

        Parses zap_execution.py script from Matter SDK to determine the minimum
        required ZAP package version.
        """
        RE_MIN_ZAP_VERSION = r'MIN_ZAP_VERSION\s*=\s*\'(\d+)\.(\d+)\.(\d+)'
        zap_execution_path = self.matter_path / 'scripts/tools/zap/zap_execution.py'

        with open(zap_execution_path, 'r') as f:
            if match := re.search(RE_MIN_ZAP_VERSION, f.read()):
                return tuple(int(group) for group in match.groups())
            raise RuntimeError(f'Failed to find MIN_ZAP_VERSION in {zap_execution_path}')

    def get_current_version(self) -> Tuple[int, int, int]:
        """
        Returns ZAP package current version as a tuple of integers.

        Parses the output of `zap --version` to determine the current ZAP
        package version. If the ZAP package has not been installed yet,
        the method returns None.
        """
        try:
            output = subprocess.check_output(
                [self.get_zap_path(), '--version']).decode('ascii').strip()
        except Exception:
            return None

        RE_VERSION = r'Version:\s*(\d+)\.(\d+)\.(\d+)'
        if match := re.search(RE_VERSION, output):
            return tuple(int(group) for group in match.groups())

        raise RuntimeError("Failed to find version in ZAP output")

    def install_zap(self, version: Tuple[int, int, int]) -> None:
        """
        Downloads and unpacks selected ZAP package version.
        """
        with tempfile.TemporaryDirectory() as temp_dir:
            url = ZapInstaller.ZAP_URL_PATTERN % (*version, self.package)
            log.inf(f'Downloading {url}...')

            try:
                wget.download(url, out=temp_dir)
            except Exception as e:
                raise RuntimeError(f'Failed to download ZAP package from {url}: {e}')

            shutil.rmtree(self.install_path, ignore_errors=True)

            log.inf('')  # Fix console after displaying wget progress bar
            log.inf(f'Unzipping ZAP package to {self.install_path}...')

            try:
                self.unzip(Path(temp_dir) / f'{self.package}.zip', self.install_path)
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

        log.inf(f'ZAP installation directory: {self.install_path}')

        if current_version:
            verdict = 'up to date' if current_version == recommended_version else 'outdated'
            log.inf('Found ZAP {}.{}.{} ({})'.format(*current_version, verdict))

        if current_version != recommended_version:
            log.inf('Installing ZAP {}.{}.{}'.format(*recommended_version))
            self.install_zap(recommended_version)

    @staticmethod
    def set_exec_permission(path: Path) -> None:
        os.chmod(path, os.stat(path).st_mode | stat.S_IEXEC)
