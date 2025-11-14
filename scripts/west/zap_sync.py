# Copyright (c) 2025 Nordic Semiconductor ASA
#
# SPDX-License-Identifier: LicenseRef-Nordic-5-Clause

import argparse
import shutil
import subprocess
from pathlib import Path
from textwrap import dedent

from west import log
from west.commands import WestCommand
from zap_append import add_cluster_to_zcl
from zap_common import (DEFAULT_MATTER_PATH, ZapInstaller, display_zap_message, existing_dir_path, existing_file_path, find_zap,
                        fix_sandbox_permissions, get_app_templates_path, get_default_zcl_json_path, update_zcl_in_zap)


class ZapSync(WestCommand):
    def __init__(self):
        super().__init__(
            'zap-sync',
            'Synchronize the ZAP and zcl.json files with the Matter SDK',
            dedent('''
            Synchronize the ZAP and zcl.json files with the Matter SDK.

            This command will synchronize the ZAP file with the Matter Data Model and the zcl.json file with the base zcl.json file in the Matter SDK.
            '''))

    def do_add_parser(self, parser_adder):
        parser = parser_adder.add_parser(self.name,
                                         help=self.help,
                                         formatter_class=argparse.RawDescriptionHelpFormatter,
                                         description=self.description)
        parser.add_argument('-z', '--zap-file', type=existing_file_path,
                            help='Path to data model configuration file (*.zap)')
        parser.add_argument('-j', '--zcl-json', type=str,
                            help='Path to data model definition file (zcl.json). If new clusters are added using --clusters, the new zcl.json file will be created and used.')
        parser.add_argument('-c', '--clusters', nargs='+',
                            help="Paths to the XML files that contain the external cluster definitions")
        parser.add_argument('-m', '--matter-path', type=existing_dir_path,
                            default=DEFAULT_MATTER_PATH, help=f'Path to Matter SDK. Default is set to {DEFAULT_MATTER_PATH}')
        return parser

    def do_run(self, args, unknown_args):

        zap_file_path = args.zap_file or find_zap()

        if zap_file_path is None:
            log.err("ZAP file not found!")
            return

        default_zcl_path = get_default_zcl_json_path(args.matter_path)
        app_templates_path = get_app_templates_path(args.matter_path)

        zap_installer = ZapInstaller(args.matter_path)
        zap_installer.update_zap_if_needed()

        if args.zcl_json:
            # Provided zcl.json file exists, so we need to remove it and create a copy of the default zcl.json file.
            zcl_file_path = Path(args.zcl_json).absolute()
            if zcl_file_path.exists():
                zcl_file_path.unlink()
            shutil.copy(default_zcl_path, zcl_file_path)
        else:
            # No zcl.json file provided, so we need to create a new one because a path was provided.
            zcl_file_path = default_zcl_path

        log.inf(f"Synchronizing zcl.json file ({zcl_file_path.absolute()})...")

        if args.clusters:
            log.inf(f"Appending custom clusters to the zcl.json file ({args.clusters})...")
            # Append the new clusters, if zcl.json was provided, it will be updated, if not, clusters will be added to the default zcl.json file.
            add_cluster_to_zcl(default_zcl_path, args.clusters, zcl_file_path, args.matter_path)

        update_zcl_in_zap(zap_file_path, zcl_file_path, app_templates_path)

        log.inf(f"Synchronizing the ZAP file ({zap_file_path.absolute()})...")
        # Update the zap file with all the changes
        self.run_zap_convert(zap_installer, zap_file_path, zcl_file_path, app_templates_path, args.matter_path)

    def run_zap_convert(self, installer: ZapInstaller, zap_file_path: Path, zcl_file_path: Path, app_templates_path: Path, matter_path: Path):

        def run_zap():
            cmd = [installer.get_zap_cli_path()]
            cmd += ["convert"]
            cmd += [zap_file_path.absolute()]
            cmd += ["--zcl", zcl_file_path.absolute()]
            cmd += ["--gen", app_templates_path.absolute()]
            cmd += ["--out", zap_file_path.absolute()]
            cmd += ["--tempState"]

            output = subprocess.run([str(x) for x in cmd], capture_output=True, text=True)
            display_zap_message(output)
            return output

        try:
            output = run_zap()
        except subprocess.CalledProcessError as e:
            fix_sandbox_permissions(e, installer)
            output = run_zap()
