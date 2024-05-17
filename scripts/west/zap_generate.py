# Copyright (c) 2024 Nordic Semiconductor ASA
#
# SPDX-License-Identifier: LicenseRef-Nordic-5-Clause

import argparse
import os
import sys

from pathlib import Path
from textwrap import dedent

from west import log
from west.commands import CommandError, WestCommand

from zap_common import existing_file_path, existing_dir_path, find_zap, ZapInstaller, DEFAULT_MATTER_PATH


class ZapGenerate(WestCommand):

    def __init__(self):
        super().__init__(
            'zap-generate',  # gets stored as self.name
            'Generate Matter data model files with ZAP',  # self.help
            # self.description:
            dedent('''
            Generate Matter data model files with the use of ZAP Tool
            based on the .zap template file defined for your application.'''))

    def do_add_parser(self, parser_adder):
        parser = parser_adder.add_parser(self.name,
                                         help=self.help,
                                         formatter_class=argparse.RawDescriptionHelpFormatter,
                                         description=self.description)
        parser.add_argument('-z', '--zap-file', type=existing_file_path,
                            help='Path to data model configuration file (*.zap)')
        parser.add_argument('-o', '--output', type=Path,
                            help='Path where to store the generated files')
        parser.add_argument('-m', '--matter-path', type=existing_dir_path,
                            default=DEFAULT_MATTER_PATH, help='Path to Matter SDK')
        return parser

    def do_run(self, args, unknown_args):
        if args.zap_file:
            zap_file_path = args.zap_file.absolute()
        else:
            zap_file_path = find_zap()

        if not zap_file_path:
            raise CommandError("No valid .zap file provided")

        if args.output:
            output_path = args.output
        else:
            output_path = zap_file_path.parent / "zap-generated"

        app_templates_path = args.matter_path / "src/app/zap-templates/app-templates.json"
        zap_generate_path = args.matter_path / "scripts/tools/zap/generate.py"

        zap_installer = ZapInstaller(args.matter_path)
        zap_installer.update_zap_if_needed()

        # make sure that the generate.py script uses the proper zap_cli binary (handled by west)
        os.environ["ZAP_INSTALL_PATH"] = str(zap_installer.get_zap_cli_path().parent.absolute())

        cmd = [sys.executable, zap_generate_path]
        cmd += [zap_file_path]
        cmd += ["-t", app_templates_path]
        cmd += ["-o", output_path]

        self.check_call([str(x) for x in cmd])

        log.inf(f"Done. Files generated in {output_path}")
