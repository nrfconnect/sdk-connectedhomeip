# Copyright (c) 2024 Nordic Semiconductor ASA
#
# SPDX-License-Identifier: LicenseRef-Nordic-5-Clause

import argparse
import os
import shutil
import sys
from pathlib import Path
from textwrap import dedent

from west import log
from west.commands import CommandError, WestCommand
from zap_common import DEFAULT_MATTER_PATH, ZapInstaller, existing_dir_path, existing_file_path, find_zap

# fmt: off
scripts_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(scripts_dir)
sys.path.append(os.path.join(scripts_dir, 'tools'))
from tools.zap_regen_all import JinjaCodegenTarget, ZAPGenerateTarget, ZapInput

# fmt: on


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
        parser.add_argument('-o', '--output', type=existing_dir_path,
                            help='Path where to store the generated files')
        parser.add_argument('-m', '--matter-path', type=existing_dir_path,
                            default=DEFAULT_MATTER_PATH, help='Path to Matter SDK')
        parser.add_argument('-f', '--full', action='store_true', help='Generate full data model files')
        parser.add_argument('-k', '--keep-previous', action='store_true', help='Keep previously generated files')
        parser.add_argument('-j', '--zcl', action='store_true', help='Generate clusters from zcl.json')
        return parser

    def build_command(self, zap_file_path, output_path, templates_path=None):
        if templates_path is None:
            # Generate the .matter file from the .zap file
            cmd = [sys.executable, self.zap_generate_path, zap_file_path, "-o", output_path]
        else:
            # Generate source files from the .zap file
            cmd = [sys.executable, self.zap_generate_path, zap_file_path, "-o", output_path, "-t", templates_path]
        return [str(x) for x in cmd]

    def do_run(self, args, unknown_args):
        self.zap_generate_path = args.matter_path / "scripts/tools/zap/generate.py"

        if args.zap_file:
            zap_file_path = args.zap_file.absolute()
        else:
            zap_file_path = find_zap()

        if not zap_file_path:
            raise CommandError("No valid .zap file provided")

        if args.output:
            output_path = args.output.absolute()
        else:
            output_path = zap_file_path.parent / "zap-generated"

        templates_path = args.matter_path / "src/app/common/templates/templates.json"
        app_templates_path = args.matter_path / "src/app/zap-templates/app-templates.json"

        zap_installer = ZapInstaller(args.matter_path)
        zap_installer.update_zap_if_needed()

        # make sure that the generate.py script uses the proper zap_cli binary (handled by west)
        os.environ["ZAP_INSTALL_PATH"] = str(zap_installer.get_zap_cli_path().parent.absolute())

        # Make sure that output directory exists
        output_path.mkdir(exist_ok=True)

        if not args.keep_previous:
            self.clear_generated_files(output_path)
            if args.full:
                log.inf(f"Clearing output directory: {output_path}")
                shutil.rmtree(output_path)
                output_path.mkdir(exist_ok=True)

        # Generate source files
        self.check_call(self.build_command(zap_file_path, output_path, app_templates_path))

        # Generate .matter file
        self.check_call(self.build_command(zap_file_path, output_path))

        if args.full:
            # Full build is about generating an apropertiate Matter data model files in a specific directory layout.
            # Currently, we must align to the following directory layout:
            # sample/
            #   |_ src/
            #     |_ default_zap/
            #       |_ zcl.xml
            #       |_ sample.zap
            #       |_ sample.matter
            #       |_ clusters/
            #         |_ *All clusters*
            #       |_ app-common
            #         |_ zap-generated/
            #           |_ attributes/
            #           |_ ids/
            #
            # Generation of the full data model files consist of three steps:
            # 1. ZAPGenerateTarget generates "app-common/zap-generated" directory and a part of "clusters/" directory.
            #    It generates Attrbutes.h/cpp, Events.h/cpp, Commands.h/cpp files for each cluster.
            # 2. JinjaCodegenTarget generates "clusters/" directory.
            #    It generates "AttributeIds.h/cpp", "EventIds.h", "CommandIds.h" files for each cluster.
            #    It generates also BUILD.gn files that are used to configure build system.
            #
            # Currently, we must call JinjaCodegenTarget twice:
            # - for all clusters using controller-clusters.matter file to generate all clusters defined in the Matter spec.
            # - for the sample's .matter file to generate the new data model files that are not defined in the Matter spec.
            #
            # To generate the full data model files, we utilizes classes defined in the matter/scripts/tools/zap_regen_all.py file.
            # These classes are supposed to be called from the matter root directory, so we must temporarily change the current working directory to the matter root directory.
            zcl_file = args.zcl or zap_file_path.parent / "zcl.json"
            zap_input = ZapInput.FromPropertiesJson(zcl_file)
            template = 'src/app/common/templates/templates.json'
            zap_output_dir = output_path / 'app-common' / 'zap-generated'
            codegen_output_dir = output_path / 'clusters'

            # Temporarily change directory to matter_path so JinjaCodegenTarget and ZAPGenerateTarget can find their scripts
            original_cwd = os.getcwd()
            os.chdir(args.matter_path)
            try:
                ZAPGenerateTarget(zap_input, template=template, output_dir=zap_output_dir).generate()
                JinjaCodegenTarget(
                    generator="cpp-sdk",
                    idl_path="src/controller/data_model/controller-clusters.matter",
                    output_directory=codegen_output_dir).generate()
                JinjaCodegenTarget(
                    generator="cpp-sdk",
                    idl_path=zap_file_path.with_suffix(".matter"),
                    output_directory=codegen_output_dir).generate()
            finally:
                # Restore original working directory
                os.chdir(original_cwd)

        log.inf(f"Done. Files generated in {output_path}")

    def clear_generated_files(self, path: Path):
        log.inf("Clearing previously generated files:")
        for file in path.iterdir():
            if file.is_file():
                with open(file, 'r') as f:
                    for line in f.readlines():
                        if "// THIS FILE IS GENERATED BY ZAP" in line:
                            log.inf(f"\tRemoving {file}")
                            file.unlink()
                            break
