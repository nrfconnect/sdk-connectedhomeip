# Copyright (c) 2025 Nordic Semiconductor ASA
#
# SPDX-License-Identifier: LicenseRef-Nordic-5-Clause

import argparse
import subprocess
from pathlib import Path
from textwrap import dedent

from west import log
from west.commands import WestCommand
from zap_append import add_cluster_to_zcl
from zap_common import (DEFAULT_APP_TEMPLATES_RELATIVE_PATH, DEFAULT_MATTER_PATH, DEFAULT_ZCL_JSON_RELATIVE_PATH, ZapInstaller,
                        existing_dir_path, existing_file_path, find_zap, fix_sandbox_permissions, synchronize_zcl_with_base,
                        update_zcl_in_zap)


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

        if args.zcl_json:
            zcl_file_path = Path(args.zcl_json).absolute()
        else:
            zcl_file_path = args.matter_path.joinpath(DEFAULT_ZCL_JSON_RELATIVE_PATH).absolute()

        if not zcl_file_path.exists():
            print(f"ZCL file not found: {zcl_file_path}")
            return False

        app_templates_path = args.matter_path.joinpath(DEFAULT_APP_TEMPLATES_RELATIVE_PATH)

        zap_installer = ZapInstaller(args.matter_path)
        zap_installer.update_zap_if_needed()

        # zcl.json file was provided, so synchronize it with the Matter SDK
        if args.zcl_json:
            if not args.clusters:
                print("Clusters are not provided, so the zcl.json file cannot be synchronized")
                return False

            # First we need to update the zap file with the original zcl.json file path
            # To catch all the changes from the Matter SDK
            self.run_zap_convert(zap_installer, zap_file_path, args.matter_path.joinpath(
                DEFAULT_ZCL_JSON_RELATIVE_PATH).absolute(), app_templates_path)

            # Then we need to synchronize the zcl.json file with the Matter SDK
            # After this step we have a clean zcl.json file with all the changes from the Matter SDK,
            # but without the custom clusters.
            synchronize_zcl_with_base(zcl_file_path, args.matter_path)

            # Now add the new clusters again to the zcl.json file
            add_cluster_to_zcl(zcl_file_path, args.clusters, zcl_file_path, args.matter_path)
            update_zcl_in_zap(zap_file_path, zcl_file_path, app_templates_path)

        # Update the zap file with all the changes
        self.run_zap_convert(zap_installer, zap_file_path, zcl_file_path, app_templates_path)

    def run_zap_convert(self, installer: ZapInstaller, zap_file_path: Path, zcl_file_path: Path, app_templates_path: Path):
        cmd = [installer.get_zap_cli_path()]
        cmd += ["convert"]
        cmd += [zap_file_path.absolute()]
        cmd += ["--zcl", zcl_file_path.absolute()]
        cmd += ["--gen", app_templates_path.absolute()]
        cmd += ["--out", zap_file_path.absolute()]
        cmd += ["--tempState"]

        try:
            self.check_call([str(x) for x in cmd])
        except subprocess.CalledProcessError as e:
            fix_sandbox_permissions(e)
