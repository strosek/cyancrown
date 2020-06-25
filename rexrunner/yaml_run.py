#!/usr/bin/env python3

import argparse
import logging
import os
import re
import shlex
import subprocess
import requests
import yaml
from datetime import datetime
from sys import exit

RUN_OUTPUT_FILENAME = datetime.now().strftime('run_log_%y%m%d_%H.%M.%S.log')

class TestData:
    """ Class to parse a dict and store test attributes """

    def __init__(self, data_dict):
        for name, value in data_dict.items():
            setattr(self, name, self._dict_to_attribute(value))

    def _dict_to_attribute(self, value):
        if isinstance(value, dict):
            return TestData(value)
        else:
            return value


class Validator:
    """ Validate a test """

    def validate_match_output(self, output, expression):
        """ Validate that output string matches a regular expression """
        if re.search(expression, output, flags=re.MULTILINE | re.DOTALL):
            return 0
        else:
            return 1

    def validate_match_ec(self, exit_code, expected_exit_code):
        """ Validate that exit code matches the expected exit code """
        if exit_code == expected_exit_code:
            return 0
        else:
            return 1

    def validate_match_cmd_output(self, command, expression):
        """ Validate that command output matches a regular expression """
        try:
            validate_proc = subprocess.Popen(shlex.split(command), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                             universal_newlines=True)
            stdout, stderr = validate_proc.communicate()
            return self.validate_match_output(stdout, expression)
        except (subprocess.CalledProcessError, FileNotFoundError) as err:
            return 1

    def run_validator(self, verify_name, test_exit_code, test_output, verify_value):
        """ Run the corresponding validation """
        if verify_name == 'MATCH_OUTPUT':
            return self.validate_match_output(test_output, verify_value)
        elif verify_name == 'MATCH_EC':
            return self.validate_match_ec(test_exit_code, verify_value)
        elif verify_name == 'MATCH_CMD_OUTPUT':
            return self.validate_match_cmd_output(verify_value['command'], verify_value['output'])
        elif verify_name == 'NOT_MATCH_CMD_OUTPUT':
            return not self.validate_match_cmd_output(verify_value['command'], verify_value['output'])

    def run_validators(self, verify_list, test_exit_code, test_output):
        """ Run all the validators of a test and collect the result. Return True if test fails """
        fail_status = False
        for verify in verify_list:
            fail_status |= self.run_validator(verify['name'], test_exit_code, test_output, verify['value'])
        return fail_status


class Test:
    """ Class to execute a test """
    def __init__(self, test):
        self.log = logging.getLogger()
        self.test = test

    def get_timeout(self, timeout_value):
        """
        Convert time string to seconds and return the value
        String should be in XhYmZs format
        """
        if isinstance(timeout_value, int):
            return
        time_string = timeout_value.lower()
        seconds = 0
        if 'h' in time_string:
            hours, time_string = time_string.split('h')
            seconds += 3600 * int(hours)
        if 'm' in time_string:
            minutes, time_string = time_string.split('m')
            seconds += 60 * int(minutes)
        if time_string:
            seconds += int(time_string.split('s')[0])
        return seconds

    def execute(self):
        """ Run a test and return exit code, stdout and stderr """
        try:
            self.log.debug(f"Executing command: {self.test.command}")
            test_proc = subprocess.Popen(shlex.split(self.test.command), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                         universal_newlines=True)

            stdout, stderr = test_proc.communicate(timeout=self.get_timeout(self.test.timeout))
            return_code = test_proc.returncode
        except subprocess.TimeoutExpired as err:
            self.log.error(f"Timeout value exceeded. Expected time: {self.test.timeout}")
            return_code, stdout, stderr = (1, '', '')
        except subprocess.CalledProcessError as err:
            self.log.error(f"Command execution failed")
            return_code, stdout, stderr = (1, err.output, '')
        except FileNotFoundError as err:
            self.log.error(f"Command executable not found")
            return_code, stdout, stderr = (1, err.strerror, '')
        self.log.debug("Execution finished")
        return return_code, stdout.strip(), stderr.strip()

    def verify(self, test_exit_code, test_output):
        """ Verify with the pass criteria """
        result = Validator().run_validators(self.test.verify, test_exit_code, test_output)
        status = 'PASS' if not result else 'FAIL'
        self.log.info(f"Test {self.test.name}")
        self.log.info(f"    Result: {status}")
        self.log.debug(f"   Command: {self.test.command}")
        self.log.debug(f"   STDOUT:\n{test_output}")
        self.log.debug(f"   EXIT CODE:{test_exit_code}")
        return result

    def run(self):
        """ Run a test and verify its result """
        ret_code, stdout, stderr = self.execute()
        return self.verify(ret_code, stdout)


class Runner:
    """ Runner class """

    def __init__(self):
        """ Initialize tests """
        self.log = logging.getLogger()
        self._setup_logging()
        self.data_list = []

    def _setup_logging(self):
        """ Create and configure a logger """
        formatter = logging.Formatter('%(asctime)s - %(funcName)-14s - %(levelname)-8s - %(message)s')
        self.log.setLevel(logging.DEBUG)
        sh = logging.StreamHandler()
        sh.setLevel(logging.DEBUG)
        sh.setFormatter(formatter)
        self.log.addHandler(sh)
        fh = logging.FileHandler(RUN_OUTPUT_FILENAME)
        fh.setLevel(logging.DEBUG)
        fh.setFormatter(formatter)
        self.log.addHandler(fh)

    def load_tests(self, file_path):
        """ Open a file and create TestData objects """
        try:
            with open(file_path) as tests:
                self.log.info(f"Loading from file: {file_path}")
                yaml_dict = yaml.safe_load(tests)
                for id, data in yaml_dict.items():
                    self.data_list.append((id, TestData(data)))
            self.log.info(f"Finished loading. Found {len(self.data_list)} tests")
        except (yaml.YAMLError, FileNotFoundError) as err:
            self.log.error(err)
            exit(1)

    def run(self):
        """ Run all tests defined on self.data_list """
        count = len(self.data_list)
        failures = 0
        for id, data in self.data_list:
            test = Test(data)
            self.log.debug(f"Running test ID: {id}")
            result = test.run()
            if result:
                failures += 1
        self.log.info(f"Total tests run: {count}")
        self.log.info(f"  PASS: {count - failures}")
        self.log.info(f"  FAIL: {failures}")
        self.log.info(f"Log file: {RUN_OUTPUT_FILENAME}")
        exit(failures)


def main():
    """ Argument parsing and test run """
    parser = argparse.ArgumentParser()
    parser.add_argument("-i", "--input_file", help="Input file")
    args = parser.parse_args()
    if not args.file:
        print("Input file required (-i|--input_file)")
        exit(1)
    test_runner = Runner()
    test_runner.load_tests(args.file)
    test_runner.run()


if __name__ == '__main__':
    main()
