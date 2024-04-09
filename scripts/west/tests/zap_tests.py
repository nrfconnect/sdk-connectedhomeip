# Copyright (c) 2025 Nordic Semiconductor ASA
#
# SPDX-License-Identifier: LicenseRef-Nordic-5-Clause

import json
import os
import re
import shutil
import sys
import unittest
from argparse import Namespace
from pathlib import Path
from unittest.mock import patch

# fmt: off
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from zap_append import ZapAppend, add_cluster_to_zcl
from zap_common import (DEFAULT_APP_TEMPLATES_RELATIVE_PATH, DEFAULT_RULES_RELATIVE_PATH, DEFAULT_ZAP_GENERATE_RELATIVE_PATH,
                        DEFAULT_ZAP_VERSION_RELATIVE_PATH, DEFAULT_ZCL_JSON_RELATIVE_PATH, ZapInstaller, find_zap,
                        post_process_generated_files, update_zcl_in_zap)
from zap_generate import ZapGenerate
from zap_sync import ZapSync

# fmt: on

SCRIPT_DIR = Path(__file__).parent
MATTER_BASE = SCRIPT_DIR.parent.parent.parent

TEST_ZAP_FILE = SCRIPT_DIR.parent.parent.parent / "examples" / \
    "all-clusters-app" / "all-clusters-common" / "all-clusters-app.zap"
TEST_ZAP_FILE_FULL = SCRIPT_DIR / "test_full.zap"
BASE_ZCL_FILE = MATTER_BASE / DEFAULT_ZCL_JSON_RELATIVE_PATH
TEST_ZCL_FILE = SCRIPT_DIR / "zcl_test.json"
TEST_OBSOLETE_ZCL_FILE = SCRIPT_DIR / "zcl_obsolete.json"
TEST_OBSOLETE_ZAP_FILE = SCRIPT_DIR / "test_obsolete.zap"
APP_TEMPLATES_FILE = MATTER_BASE / DEFAULT_APP_TEMPLATES_RELATIVE_PATH
VERSION_FILE = MATTER_BASE / DEFAULT_ZAP_VERSION_RELATIVE_PATH
TEST_SAMPLES_FILE = SCRIPT_DIR / "zap_samples.yml"


class TestWestZap(unittest.TestCase):
    """Base test class with shared temporary directory"""

    @classmethod
    def setUpClass(cls):
        cls.test_dir = MATTER_BASE / "test_dir"
        cls.test_zap_file = cls.test_dir / "test.zap"
        cls.test_zap_file_full = cls.test_dir / "test_full.zap"
        cls.zcl_json_file = cls.test_dir / "zcl.json"
        cls.zcl_json_file_with_new_items = cls.test_dir / "zcl_test.json"
        cls.zcl_json_appended = cls.test_dir / "zcl_appended.json"
        cls.test_clusters = [SCRIPT_DIR / "Cluster1.xml", SCRIPT_DIR / "Cluster2.xml"]
        cls.app_templates = APP_TEMPLATES_FILE
        cls.version_file = cls.test_dir / 'scripts' / 'setup' / 'zap.version'
        cls.zap_output_dir = cls.test_dir / "zap-generated"
        cls.zap_output_dir_full = cls.test_dir / "zap-generated-full"
        cls.zap_output_dir_synced = cls.test_dir / "zap-generated-synced"
        cls.zap_output_dir_samples_yml = cls.test_dir / "zap-generated-samples-yml"
        cls.test_obsolete_zap_file = cls.test_dir / "test_obsolete.zap"
        cls.test_obsolete_zcl_file = cls.test_dir / "zcl_test_obsolete.json"
        cls.test_samples_file = cls.test_dir / "zap_samples.yml"

        cls.cluster_names = [cluster.stem for cluster in cls.test_clusters]

        # Prepare the test directory
        if not cls.test_dir.exists():
            cls.test_dir.mkdir(parents=True, exist_ok=True)

        cls.zcl_json_file.touch()
        cls.zcl_json_file_with_new_items.touch()

        # Ensure destination directories exist before copying
        cls.version_file.parent.mkdir(parents=True, exist_ok=True)

        shutil.copy(BASE_ZCL_FILE, cls.zcl_json_file)
        shutil.copy(TEST_ZCL_FILE, cls.zcl_json_file_with_new_items)
        shutil.copy(TEST_ZAP_FILE, cls.test_zap_file)
        shutil.copy(VERSION_FILE, cls.version_file)
        shutil.copy(TEST_SAMPLES_FILE, cls.test_samples_file)

        with open(cls.version_file, 'r') as f:
            cls.recommended_version = f.read().strip()

        # Initialize common zap installer
        cls.zap_installer = ZapInstaller(cls.test_dir)

    @classmethod
    def tearDownClass(cls):
        if cls.test_dir.exists():
            shutil.rmtree(cls.test_dir)

    def test_find_zap_files(self):
        """
        Checks whether the find_zap function finds the zap file in the current directory.

        We expect:
            - Single zap file is located in the current directory.
            - No zap files are located in the current directory after deleting the single zap file.
            - Multiple zap files are located in the current directory and the user selects the first zap file.
        """
        zap_file = self.test_zap_file

        result = find_zap(self.test_dir)
        self.assertEqual(result, zap_file)

        # No file should return None
        zap_file.unlink()
        result = find_zap(self.test_dir)
        self.assertIsNone(result)

        # Multiple file
        # Mock input to select first file
        zap1 = self.test_dir / "test1.zap"
        zap2 = self.test_dir / "test2.zap"
        zap1.touch()
        zap2.touch()
        with patch('builtins.input', return_value='0'):
            with patch('builtins.print'):
                result = find_zap(self.test_dir)
                self.assertIn(result, [zap1, zap2])

        # Clean up the test zap files
        zap1.unlink()
        zap2.unlink()

        # Restore the test zap file
        shutil.copy(TEST_ZAP_FILE, self.test_zap_file)

    def test_update_zcl_in_zap(self):
        """
        Checks whether the update_zcl_in_zap function updates the zcl.json file in the zap file.

        We expect:
            - The zcl.json file is updated in the zap file.
            - The app-templates.json file is updated in the zap file.
        """
        with open(self.test_zap_file, 'r') as f:
            zap_data = json.load(f)
            self.assertNotEqual(zap_data["package"][0]["path"], "zcl.json")
            self.assertNotEqual(zap_data["package"][1]["path"], "app-templates.json")

        result = update_zcl_in_zap(self.test_zap_file, self.zcl_json_file, self.app_templates)
        self.assertTrue(result)

        with open(self.test_zap_file, 'r') as f:
            updated_data = json.load(f)
            self.assertEqual(updated_data["package"][0]["path"], "zcl.json")
            self.assertEqual(updated_data["package"][1]["path"], "../src/app/zap-templates/app-templates.json")

    def test_post_process_generated_files(self):
        """
        Checks whether the post_process_generated_files function processes the generated files correctly.

        We expect:
            - The test file is processed correctly with no newline.
            - The test file is processed correctly with multiple newlines.
        """
        test_file = self.test_dir / "test.txt"

        # Test file with no newline
        with open(test_file, 'w') as f:
            f.write("test content")

        post_process_generated_files(self.test_dir, "manufacturer_specific")

        with open(test_file, 'r') as f:
            content = f.read()
            self.assertTrue(content.endswith('\n'))
            self.assertEqual(content, "test content\n")

        with open(test_file, 'w') as f:
            f.write("# Cluster generated code for constants and metadata based on /home/xxx/ncs/nrf/samples/matter/manufacturer_specific/src/default_zap/manufacturer_specific.matter\n")
            f.write("// based on /home/xxx/ncs/nrf/samples/matter/manufacturer_specific/src/default_zap/manufacturer_specific.matter\n")

        post_process_generated_files(self.test_dir, "manufacturer_specific")
        with open(test_file, 'r') as f:
            content = f.readlines()
            self.assertEqual(len(content), 0)

        # Test file with multiple newlines
        with open(test_file, 'w') as f:
            f.write("test content\n\n\n")

        post_process_generated_files(self.test_dir, "manufacturer_specific")

        with open(test_file, 'r') as f:
            content = f.read()
            self.assertTrue(content.endswith('\n'))
            self.assertEqual(content.count('\n'), 1)

    def test_zap_installer_init(self):
        """
        Checks whether the ZapInstaller class is initialized correctly.

        We expect:
            - The ZapInstaller class is initialized correctly on Linux.
            - The ZapInstaller class is initialized correctly on macOS.
            - The ZapInstaller class is initialized correctly on Windows.
        """
        # Linux
        with patch('platform.system', return_value='Linux'):

            with patch('platform.machine', return_value='aarch64'):
                installer = ZapInstaller(self.test_dir)
                self.assertEqual(installer.package, 'zap-linux-arm64')

            with patch('platform.machine', return_value='x86_64'):
                installer = ZapInstaller(self.test_dir)
                self.assertEqual(installer.package, 'zap-linux-x64')

        # macOS
        with patch('platform.system', return_value='Darwin'):
            with patch('platform.machine', return_value='arm64'):
                installer = ZapInstaller(self.test_dir)
                self.assertEqual(installer.package, 'zap-mac-arm64')

        # Windows
        with patch('platform.system', return_value='Windows'):
            with patch('platform.machine', return_value='AMD64'):
                installer = ZapInstaller(self.test_dir)
                self.assertEqual(installer.package, 'zap-win-x64')

    def test_get_paths(self):
        """
        Checks whether the get_install_path, get_zap_path, and get_zap_cli_path functions return the correct paths.

        We expect:
            - The install path is returned correctly.
            - The zap path is returned correctly.
            - The zap CLI path is returned correctly.
        """
        # Install path
        installer = self.zap_installer
        expected = self.test_dir / '.zap-install'
        self.assertEqual(installer.get_install_path(), expected)

        # Zap path
        installer = self.zap_installer
        expected = self.test_dir / '.zap-install' / installer.zap_exe
        self.assertEqual(installer.get_zap_path(), expected)

        # Zap CLI path
        installer = self.zap_installer
        expected = self.test_dir / '.zap-install' / installer.zap_cli_exe
        self.assertEqual(installer.get_zap_cli_path(), expected)

    def test_version(self):
        """
        Checks whether the get_recommended_version function returns the correct version.

        We expect:
            - The recommended version is returned correctly.
            - The current version is returned correctly.
        """

        installer = self.zap_installer
        version = installer.get_recommended_version()
        self.assertEqual(version, self.recommended_version)

        installer = self.zap_installer

        with patch('subprocess.check_output', side_effect=Exception()):
            version = installer.get_current_version()
            self.assertIsNone(version)

    def test_append_cluster(self):
        """
        Checks whether the append_cluster function appends the cluster to the ZCL file correctly.

        We expect:
            - The cluster is appended to the ZCL file correctly.
            - The custom attributes are added to the ZCL file correctly.
        """
        add_cluster_to_zcl(BASE_ZCL_FILE, self.test_clusters, self.zcl_json_file, matter_path=MATTER_BASE)

        with open(self.zcl_json_file, 'r') as f:
            output_data = json.load(f)
            self.assertIn("Cluster1.xml", output_data["xmlFile"])
            self.assertIn("Cluster2.xml", output_data["xmlFile"])
            self.assertIn("Cluster 1", output_data["attributeAccessInterfaceAttributes"])
            self.assertIn("attr1", output_data["attributeAccessInterfaceAttributes"]["Cluster 1"])
            self.assertIn("attr2", output_data["attributeAccessInterfaceAttributes"]["Cluster 1"])
            self.assertIn("Cluster 2", output_data["attributeAccessInterfaceAttributes"])
            self.assertIn("attr2", output_data["attributeAccessInterfaceAttributes"]["Cluster 2"])

    def test_install_zap(self):
        """
        Checks whether the install_zap function installs the ZAP package correctly.

        We expect:
            - The ZAP package is installed correctly.
            - The ZAP package is not installed if the current ver sion is the same as the recommended version.
            - The ZAP package is installed.
        """
        zap_installer = self.zap_installer

        # Test when the current version is the same as the recommended version
        with patch.object(zap_installer, 'get_current_version', return_value=self.recommended_version):
            with patch.object(zap_installer, 'install_zap') as mock_install:
                zap_installer.update_zap_if_needed()
                mock_install.assert_called_once_with(self.recommended_version)

        # Test when the current version is different from the recommended version
        with patch.object(zap_installer, 'get_current_version', return_value="2025.01.15"):
            with patch.object(zap_installer, 'install_zap') as mock_install:
                zap_installer.update_zap_if_needed()
                mock_install.assert_called_once_with(self.recommended_version)

        # Test when the current version is None
        with patch.object(zap_installer, 'get_current_version', return_value=None):
            with patch.object(zap_installer, 'install_zap') as mock_install:
                zap_installer.update_zap_if_needed()
                mock_install.assert_called_once_with(self.recommended_version)

        if match := re.search(r'v?(\d+\.\d+\.\d+)', self.recommended_version):
            year, month, day = match.group(1).split('.')
            version_clean = f"{int(year)}.{int(month)}.{int(day)}"

        # Download the ZAP package
        zap_installer.update_zap_if_needed()

        self.assertTrue(zap_installer.get_current_version() == version_clean)

    def test_zap_generate(self):
        """
        Checks whether the zap_generate function generates the ZAP package correctly.

        We expect:
            - Zap files are generated correctly.
            - The data model is re-generated for --full argument and all zap files for new clusters are generated.
        """
        zap_installer = self.zap_installer
        self.assertTrue(zap_installer.get_current_version() != "")

        # Run zap-generate command for simple generation
        with patch('zap_generate.get_zap_generate_path', return_value=MATTER_BASE / DEFAULT_ZAP_GENERATE_RELATIVE_PATH):
            with patch('zap_generate.get_app_templates_path', return_value=MATTER_BASE / DEFAULT_APP_TEMPLATES_RELATIVE_PATH):
                with patch('zap_generate.ZapInstaller', return_value=zap_installer):
                    ZapGenerate().do_run(Namespace(zap_file=self.test_zap_file,
                                                   output=self.zap_output_dir,
                                                   matter_path=self.test_dir,
                                                   full=False,
                                                   keep_previous=False,
                                                   zcl=None,
                                                   yaml=None), [])

        self.assertTrue(self.zap_output_dir.exists())
        self.assertTrue((self.zap_output_dir.parent / "test.matter").exists())
        self.assertTrue((self.zap_output_dir / "callback-stub.cpp").exists())
        self.assertTrue((self.zap_output_dir / "CodeDrivenCallback.h").exists())
        self.assertTrue((self.zap_output_dir / "CodeDrivenInitShutdown.cpp").exists())
        self.assertTrue((self.zap_output_dir / "endpoint_config.h").exists())
        self.assertTrue((self.zap_output_dir / "gen_config.h").exists())
        self.assertTrue((self.zap_output_dir / "IMClusterCommandHandler.cpp").exists())
        self.assertTrue((self.zap_output_dir / "PluginApplicationCallbacks.h").exists())

    def test_zap_append(self):
        """
        Checks whether the zap_append function appends the cluster to the ZAP file correctly.
        """

        if self.zcl_json_appended.exists():
            self.zcl_json_appended.unlink()

        ZapAppend().do_run(Namespace(base=None, matter=MATTER_BASE, output=self.zcl_json_appended, clusters=self.test_clusters), [])

        self.assertTrue(self.zcl_json_appended.exists())

        with open(self.zcl_json_appended, 'r') as f:
            output_data = json.load(f)
            self.assertIn("Cluster1.xml", output_data["xmlFile"])
            self.assertIn("Cluster2.xml", output_data["xmlFile"])
            self.assertIn("Cluster 1", output_data["attributeAccessInterfaceAttributes"])
            self.assertIn("attr1", output_data["attributeAccessInterfaceAttributes"]["Cluster 1"])
            self.assertIn("attr2", output_data["attributeAccessInterfaceAttributes"]["Cluster 1"])
            self.assertIn("Cluster 2", output_data["attributeAccessInterfaceAttributes"])
            self.assertIn("attr2", output_data["attributeAccessInterfaceAttributes"]["Cluster 2"])

    def test_zap_generate_full(self):
        """
        Checks whether the zap_generate function generates the ZAP package correctly with the full argument.

        Use the same zcl file from the zap_append_test
        """

        shutil.copy(TEST_ZAP_FILE_FULL, self.test_zap_file_full)

        self.assertTrue(self.zcl_json_appended.exists())

        # Run zap-generate command for full generation
        # Use the full zap file to generate the full data model.
        with patch('zap_generate.get_zap_generate_path', return_value=MATTER_BASE / DEFAULT_ZAP_GENERATE_RELATIVE_PATH):
            with patch('zap_generate.get_app_templates_path', return_value=MATTER_BASE / DEFAULT_APP_TEMPLATES_RELATIVE_PATH):
                with patch('zap_generate.ZapInstaller', return_value=self.zap_installer):
                    ZapGenerate().do_run(Namespace(zap_file=self.test_zap_file_full,
                                                   output=self.zap_output_dir_full,
                                                   matter_path=MATTER_BASE,
                                                   full=True,
                                                   keep_previous=False,
                                                   zcl=self.zcl_json_appended,
                                                   yaml=None), [])

        # Check full generation
        self._check_full_generation(self.zap_output_dir_full, self.cluster_names)

    def test_generate_from_yaml(self):
        """
        Checks whether the zap_generate function generates the ZAP package correctly from a yaml file.
        """

        # Copy the zap file and zcl to compare later.
        zap_to_compare = self.test_dir / "zap_to_comapre.zap"
        zcl_to_compare = self.test_dir / "zcl_to_compare.json"
        shutil.copy(self.test_zap_file_full, zap_to_compare)
        shutil.copy(self.zcl_json_appended, zcl_to_compare)

        # Replace the base_dir relative to the ZEPHYR_BASE directory.
        with open(self.test_samples_file, 'r') as f:
            ZEPHYR_BASE = os.environ.get('ZEPHYR_BASE', "")
            samples_yml_content = f.read()
            samples_yml_content = samples_yml_content.replace(
                "base_dir: ../modules/lib/matter/test_dir", f"base_dir: {self.test_dir.relative_to(Path(ZEPHYR_BASE), walk_up=True)}")
            with open(self.test_samples_file, 'w') as f:
                f.write(samples_yml_content)

            # Run generate using the yaml file
        with patch('zap_generate.get_zap_generate_path', return_value=MATTER_BASE / DEFAULT_ZAP_GENERATE_RELATIVE_PATH):
            with patch('zap_generate.get_app_templates_path', return_value=MATTER_BASE / DEFAULT_APP_TEMPLATES_RELATIVE_PATH):
                with patch('zap_generate.ZapInstaller', return_value=self.zap_installer):
                    ZapGenerate().do_run(Namespace(zap_file=None, output=self.zap_output_dir_samples_yml,
                                                   matter_path=MATTER_BASE, full=None, keep_previous=False, zcl=None, yaml=self.test_samples_file), [])

        # Check full generation
        self._check_full_generation(self.zap_output_dir_samples_yml, self.cluster_names)

        # Check whether all generated files are the same as the ones generated from the zap_generate_full test.
        failures = []

        # Recursively collect all files from both directories
        def collect_files(directory):
            """Recursively collect all files in a directory."""
            files = {}
            for file_path in directory.rglob("*"):
                if file_path.is_file():
                    # Get relative path from the directory root
                    rel_path = file_path.relative_to(directory)
                    files[rel_path] = file_path
            return files

        full_files = collect_files(self.zap_output_dir_full)
        samples_yml_files = collect_files(self.zap_output_dir_samples_yml)

        # Check all files from zap_output_dir_full
        for rel_path, full_file in full_files.items():
            samples_yml_file = self.zap_output_dir_samples_yml / rel_path

            if rel_path not in samples_yml_files:
                failures.append(f"File missing in samples_yml: {rel_path}")
            else:
                try:
                    with open(full_file, 'r', encoding='utf-8', errors='ignore') as f:
                        full_content = f.read()
                    with open(samples_yml_file, 'r', encoding='utf-8', errors='ignore') as f:
                        samples_yml_content = f.read()

                    if full_content != samples_yml_content:
                        failures.append(f"File content differs: {rel_path}")
                except Exception as e:
                    failures.append(f"Error comparing file {rel_path}: {str(e)}")

        # Check for files in samples_yml that are not in full
        for rel_path in samples_yml_files:
            if rel_path not in full_files:
                failures.append(f"Extra file in samples_yml: {rel_path}")

        # Print all failures
        if failures:
            print("\n" + "=" * 80)
            print(f"Found {len(failures)} file comparison failure(s):")
            print("=" * 80)
            for failure in failures:
                print(f"  - {failure}")
            print("=" * 80 + "\n")

        # Assert that there are no failures
        self.assertEqual(len(failures), 0, f"Found {len(failures)} file comparison failure(s). See output above for details.")

        # Compare zap_from_yml.zap with self.test_zap_file_full
        with open(zap_to_compare, "rb") as f1, open(self.test_zap_file_full, "rb") as f2:
            zap_to_compare_content = f1.read()
            zap_full_content = f2.read()
            self.assertEqual(zap_to_compare_content, zap_full_content, "zap_to_compare.zap and test_zap_file_full differ")

        # Compare zcl_from_yml.json with zcl_json_appended
        with open(zcl_to_compare, "rb") as f1, open(self.zcl_json_appended, "rb") as f2:
            zcl_to_compare_content = f1.read()
            zcl_appended_content = f2.read()
            self.assertEqual(zcl_to_compare_content, zcl_appended_content, "zcl_to_compare.json and zcl_json_appended differ")

    def test_zap_synchronize(self):
        """
        Checks whether the zap_sync function synchronizes the ZAP file correctly.

        We expect:
            - The ZAP file still contains the custom clusters after synchronization.
            - The zcl.json file is synchronized with the Matter SDK, so contains the new items from the Matter SDK, but also contains the custom clusters.
            - West zap-generate still works without any errors.
        """

        # Input files should exist.
        self.assertTrue(self.zcl_json_appended.exists())
        shutil.copy(TEST_ZAP_FILE_FULL, self.test_zap_file_full)

        # Copy the obsolete zcl.json file to the test directory.
        shutil.copy(TEST_OBSOLETE_ZCL_FILE, self.test_obsolete_zcl_file)
        # Copy the obsolete zap file to the test directory.
        shutil.copy(TEST_OBSOLETE_ZAP_FILE, self.test_obsolete_zap_file)

        # Run zap-sync command
        with patch('zap_common.get_rules_path', return_value=MATTER_BASE / DEFAULT_RULES_RELATIVE_PATH):
            ZapSync().do_run(Namespace(zap_file=self.test_obsolete_zap_file, zcl_json=self.test_obsolete_zcl_file,
                                       matter_path=MATTER_BASE, clusters=self.test_clusters), [])

        # Check whether the custom clusters are still present in the ZAP file.
        with open(self.test_obsolete_zap_file, 'r') as f:
            content = f.read()
            self.assertIn("Cluster 1", content)
            self.assertIn("Cluster 2", content)
        # Check whether the custom clusters are still present in the zcl.json file.
        with open(self.test_obsolete_zcl_file, 'r') as f:
            content = f.read()
            self.assertIn("Cluster1", content)
            self.assertIn("Cluster2", content)

        # Run zap-generate command
        with patch('zap_generate.get_zap_generate_path', return_value=MATTER_BASE / DEFAULT_ZAP_GENERATE_RELATIVE_PATH):
            with patch('zap_generate.get_app_templates_path', return_value=MATTER_BASE / DEFAULT_APP_TEMPLATES_RELATIVE_PATH):
                with patch('zap_generate.ZapInstaller', return_value=self.zap_installer):
                    ZapGenerate().do_run(Namespace(zap_file=self.test_obsolete_zap_file, output=self.zap_output_dir_synced,
                                                   matter_path=MATTER_BASE, full=True, keep_previous=False, zcl=self.test_obsolete_zcl_file, yaml=None), [])

        # Check full generation
        self._check_full_generation(self.zap_output_dir_synced, self.cluster_names)

    def _check_full_generation(self, output_dir, clusters):
        self.assertTrue(output_dir.exists())
        self.assertTrue((output_dir.parent / "test_full.matter").exists())
        self.assertTrue((output_dir / "callback-stub.cpp").exists())
        self.assertTrue((output_dir / "CodeDrivenCallback.h").exists())
        self.assertTrue((output_dir / "CodeDrivenInitShutdown.cpp").exists())
        self.assertTrue((output_dir / "endpoint_config.h").exists())
        self.assertTrue((output_dir / "gen_config.h").exists())
        self.assertTrue((output_dir / "IMClusterCommandHandler.cpp").exists())
        self.assertTrue((output_dir / "PluginApplicationCallbacks.h").exists())
        # Check the attributes directory whereas all the files are generated and contains the new clusters.
        self.assertTrue((output_dir / "app-common" / "zap-generated" / "attributes" / "Accessors.cpp").exists())
        self.assertTrue((output_dir / "app-common" / "zap-generated" / "attributes" / "Accessors.h").exists())
        with open(output_dir / "app-common" / "zap-generated" / "attributes" / "Accessors.cpp", 'r') as f:
            content = f.read()
            self.assertIn("Cluster1", content)
            self.assertIn("Cluster2", content)
        with open(output_dir / "app-common" / "zap-generated" / "attributes" / "Accessors.h", 'r') as f:
            content = f.read()
            self.assertIn("Cluster1", content)
            self.assertIn("Cluster2", content)
        # Check the ids directory whereas all the files are generated and contains the new clusters.
        self.assertTrue((output_dir / "app-common" / "zap-generated" / "ids" / "Attributes.h").exists())
        self.assertTrue((output_dir / "app-common" / "zap-generated" / "ids" / "Clusters.h").exists())
        self.assertTrue((output_dir / "app-common" / "zap-generated" / "ids" / "Commands.h").exists())
        self.assertTrue((output_dir / "app-common" / "zap-generated" / "ids" / "Events.h").exists())
        with open(output_dir / "app-common" / "zap-generated" / "ids" / "Attributes.h", 'r') as f:
            content = f.read()
            self.assertIn("Cluster1", content)
            self.assertIn("Cluster2", content)
        with open(output_dir / "app-common" / "zap-generated" / "ids" / "Clusters.h", 'r') as f:
            content = f.read()
            self.assertIn("Cluster1", content)
            self.assertIn("Cluster2", content)
        with open(output_dir / "app-common" / "zap-generated" / "ids" / "Commands.h", 'r') as f:
            content = f.read()
            self.assertIn("Cluster1", content)
            self.assertIn("Cluster2", content)
        with open(output_dir / "app-common" / "zap-generated" / "ids" / "Events.h", 'r') as f:
            content = f.read()
            self.assertIn("Cluster1", content)
            self.assertIn("Cluster2", content)
        # Check if the new device has been added to the device type list.
        self.assertTrue((output_dir / "devices" / "Ids.h").exists())
        self.assertTrue((output_dir / "devices" / "Types.h").exists())
        with open(output_dir / "devices" / "Ids.h", 'r') as f:
            content = f.read()
            self.assertIn("NewDevice", content)
        with open(output_dir / "devices" / "Types.h", 'r') as f:
            content = f.read()
            self.assertIn("NewDevice", content)
        # Check if the new clusters exists.
        for cluster in clusters:
            self.assertTrue((output_dir / "clusters" / cluster / "AttributeIds.h").exists())
            self.assertTrue((output_dir / "clusters" / cluster / "Attributes.h").exists())
            self.assertTrue((output_dir / "clusters" / cluster / "Attributes.ipp").exists())
            self.assertTrue((output_dir / "clusters" / cluster / "BUILD.gn").exists())
            self.assertTrue((output_dir / "clusters" / cluster / "ClusterId.h").exists())
            self.assertTrue((output_dir / "clusters" / cluster / "CommandIds.h").exists())
            self.assertTrue((output_dir / "clusters" / cluster / "Commands.h").exists())
            self.assertTrue((output_dir / "clusters" / cluster / "Commands.ipp").exists())
            self.assertTrue((output_dir / "clusters" / cluster / "Enums.h").exists())
            self.assertTrue((output_dir / "clusters" / cluster / "EnumsCheck.h").exists())
            self.assertTrue((output_dir / "clusters" / cluster / "EventIds.h").exists())
            self.assertTrue((output_dir / "clusters" / cluster / "Events.h").exists())
            self.assertTrue((output_dir / "clusters" / cluster / "Events.ipp").exists())
            self.assertTrue((output_dir / "clusters" / cluster / "Metadata.h").exists())
            self.assertTrue((output_dir / "clusters" / cluster / "MetadataProvider.h").exists())
            self.assertTrue((output_dir / "clusters" / cluster / "Structs.h").exists())
            self.assertTrue((output_dir / "clusters" / cluster / "Structs.ipp").exists())


def suite():
    """
    Create a test suite with tests in a specific order.
    Modify the test_order list to control the execution sequence.
    """
    # Define the desired test execution order
    test_order = [
        'test_find_zap_files',
        'test_update_zcl_in_zap',
        'test_post_process_generated_files',
        'test_zap_installer_init',
        'test_get_paths',
        'test_version',
        'test_append_cluster',
        'test_install_zap',
        'test_zap_generate',
        'test_zap_append',
        'test_zap_generate_full',
        'test_generate_from_yaml',
        # 'test_zap_synchronize',
    ]

    loader = unittest.TestLoader()
    suite = unittest.TestSuite()

    # Load tests in the specified order
    for test_name in test_order:
        try:
            suite.addTest(TestWestZap(test_name))
        except AttributeError:
            # Test method doesn't exist, skip it
            pass

    # Add any remaining tests that weren't in the order list
    all_tests = loader.getTestCaseNames(TestWestZap)
    remaining_tests = [test for test in all_tests if test not in test_order]
    remaining_tests.sort()  # Sort alphabetically for consistency

    for test_name in remaining_tests:
        suite.addTest(TestWestZap(test_name))

    return suite


if __name__ == "__main__":
    # Run tests using the custom suite
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite())
    # Exit with non-zero code if tests failed or had errors
    sys.exit(0 if result.wasSuccessful() else 1)
