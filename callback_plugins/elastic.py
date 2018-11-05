# Copyright (c) 2018, Michael Hatoum <michael@adaltas.com>

from __future__ import (absolute_import, division, print_function)
__metaclass__ = type

DOCUMENTATION = '''
    callback: elastic
    type: notification
    short_description: Sends events to Elasticsearch
    description:
        - This callback plugin will generate JSON objects and send them to Elasticsearch via CMRESHandler plugin.
    requirements:
        - whitelisting in configuration
        - python-dateutil (pip3 install python-dateutil)
        - python-elasticsearch-logger (pip3 install CMRESHandler)
    options:
        host:
            description: ES hostname
            required: true
        port:
            description: ES port
            required: true
        username:
            description: ES username
            required: true
        password:
            description: ES password
            required: true
        auth_type:
            description: ES Auth type (NO_AUTH = 0, BASIC_AUTH = 1)
            required: true
            default: 1
            choices: [0, 1]
        use_ssl:
            description: ES use SSL
            required: true
        verify_ssl:
            description: ES verify SSL
            required: true
        es_index_name:
            description: ES index name
            required: true
        app:
            description: App name
            required: true
        env:
            description: Environment name
            required: true
        raise_on_indexing_exceptions:
            description: raise on indexing exceptions
            required: true
        es_logger_name:
            description: ES logger name
            required: true
'''

EXAMPLES = '''
plugin: 'elastic'
host: '172.17.0.2'
port: 9200
username: 'elastic'
password: 'changeme'
auth_type: 1
use_ssl: False
verify_ssl: False
es_index_name: 'ansible-test'
app: 'ansible'
env: 'dev'
raise_on_indexing_exceptions: True
es_logger_name: 'ansible'
'''

from ansible.errors import AnsibleParserError, AnsibleError
from ansible.module_utils._text import to_bytes, to_native, to_text
from ansible.module_utils.common._collections_compat import Mapping, MutableMapping
from ansible.parsing.dataloader import DataLoader
from ansible.plugins.callback import CallbackBase

import os
import glob
import json
import socket
import uuid
import logging

try:
    from datetime import datetime
    from dateutil.relativedelta import relativedelta
except ImportError:
    raise AnsibleError('The Elasticsearch callback plugin requires python-dateutil (pip3 install python-dateutil).')

try:
    from cmreslogging.handlers import CMRESHandler
except ImportError:
    raise AnsibleError('The Elasticsearch callback plugin requires python-elasticsearch-logger (pip3 install CMRESHandler).')

try:
    from __main__ import cli
except ImportError:
    # using API w/o cli
    cli = False

class CallbackModule(CallbackBase):
    # Callback
    CALLBACK_VERSION = 2.0
    CALLBACK_NEEDS_WHITELIST = True
    CALLBACK_TYPE = 'aggregate'
    CALLBACK_NAME = 'elastic'

    NAME = CALLBACK_NAME
    _load_name = NAME

    def __init__(self):
        super(CallbackModule, self).__init__()

        # Initialize data loader
        self.loader = DataLoader()

        # Get the configuration
        self._config_data = self._read_config_data(self._get_config_file_name())

        # Initialize logger
        self._initialize_logger()

    ###########################################################################
    # Ansible
    ###########################################################################
    # Ansible ACTIONS
    ANSIBLE_ACTION_START = 'START'
    ANSIBLE_ACTION_RUNNING = 'RUNNING'
    ANSIBLE_ACTION_SKIPPED = 'SKIPPED'
    ANSIBLE_ACTION_FINISH = 'FINISH'

    # Ansible TYPE
    ANSIBLE_TYPE_PLAYBOOK = 'PLAYBOOK'
    ANSIBLE_TYPE_PLAY = 'PLAY'
    ANSIBLE_TYPE_TASK = 'TASK'
    ANSIBLE_TYPE_TASK_ASYNC = 'TASK ASYNC'
    ANSIBLE_TYPE_IMPORT = 'IMPORT'

    # Ansible STATUS
    ANSIBLE_STATUS_OK = 'OK'
    ANSIBLE_STATUS_FAILED = 'FAILED'
    ANSIBLE_STATUS_SKIPPED = 'SKIPPED'
    ANSIBLE_STATUS_IMPORTED = 'IMPORTED'
    ANSIBLE_STATUS_NOT_IMPORTED = 'NOT IMPORTED'
    ANSIBLE_STATUS_UNREACHABLE = 'UNREACHABLE'

    # Ansible TASK TYPE
    ANSIBLE_TTYPE_SETUP = 'setup' # deprecated
    ANSIBLE_TTYPE_GATHERING_FACTS = 'Gathering Facts'
    ANSIBLE_TTYPE_TASK = 'task'
    ANSIBLE_TTYPE_INCLUDE = 'include' # deprecated

    # Ansible RESULT
    ANSIBLE_RESULT_CHANGED = 'changed'

    def v2_playbook_on_start(self, playbook):
        self.ansible_playbook_id = str(uuid.uuid1())
        self.ansible_playbook_time_start = datetime.utcnow()

        if playbook._file_name:
          self.ansible_playbook_name = playbook._file_name

        data = self.base_data.copy()
        data['ansible_time_start'] = self.ansible_playbook_time_start

        data['ansible_type'] = CallbackModule.ANSIBLE_TYPE_PLAYBOOK
        data['ansible_action'] = CallbackModule.ANSIBLE_ACTION_START
        data['ansible_status'] = CallbackModule.ANSIBLE_STATUS_OK

        data['ansible_playbook_id'] = self.ansible_playbook_id
        data['ansible_playbook_name'] = self.ansible_playbook_name
        data['ansible_playbook_file_name'] = playbook._file_name

        if self._es_server_is_up:
          self._logger.info("START PLAYBOOK | " + data['ansible_playbook_file_name'], extra=data)

    def v2_playbook_on_play_start(self, play):
        self.ansible_play_id = str(play._uuid)

        if play.name:
            self.ansible_play_name = play.name

    def v2_playbook_on_task_start(self, task, is_conditional):
        self.ansible_task_id = str(task._uuid)
        self.ansible_task_time_start = datetime.utcnow()

        if task.name:
            self.ansible_task_name = task.name

        data = self.base_data.copy()
        data['ansible_time_start'] = self.ansible_task_time_start

        data['ansible_type'] = CallbackModule.ANSIBLE_TYPE_TASK
        data['ansible_action'] = CallbackModule.ANSIBLE_ACTION_START
        data['ansible_status'] = CallbackModule.ANSIBLE_STATUS_OK

        data['ansible_playbook_id'] = self.ansible_playbook_id
        data['ansible_playbook_name'] = self.ansible_playbook_name
        data['ansible_play_id'] = self.ansible_play_id
        data['ansible_play_name'] = self.ansible_play_name

        data['ansible_task_id'] = self.ansible_task_id
        data['ansible_task_name'] = self.ansible_task_name

        if self._es_server_is_up:
            if self.ansible_task_name != CallbackModule.ANSIBLE_TTYPE_GATHERING_FACTS and "Include" not in self.ansible_task_name:
                self._logger.info("START TASK | " + data['ansible_task_name'], extra=data)

    def v2_playbook_on_stats(self, stats):
        summarize_stat = {}
        for host in stats.processed.keys():
            summarize_stat[host] = stats.summarize(host)

        data = self.base_data.copy()
        data['ansible_time_start'] = self.ansible_playbook_time_start
        data['ansible_time_end'] = datetime.utcnow()
        data['ansible_time_duration'] = self._get_time_diff(data['ansible_time_start'], data['ansible_time_end'])

        data['ansible_type'] = CallbackModule.ANSIBLE_TYPE_PLAYBOOK
        data['ansible_action'] = CallbackModule.ANSIBLE_ACTION_FINISH

        if self.errors == 0:
            data['ansible_status'] = CallbackModule.ANSIBLE_STATUS_OK
        else:
            data['ansible_status'] = CallbackModule.ANSIBLE_STATUS_FAILED

        data['ansible_playbook_id'] = self.ansible_playbook_id
        data['ansible_playbook_name'] = self.ansible_playbook_name
        data['ansible_playbook_result'] = json.dumps(summarize_stat)

        if self._es_server_is_up:
            self._logger.info("FINISH PLAYBOOK | " + data['ansible_playbook_result'], extra=data)

    ###########################################################################
    # Runner
    ###########################################################################

    def v2_runner_on_ok(self, result, **kwargs):
        ttype = str(result._task).replace('TASK: ', '').replace('HANDLER: ', '')

        data = self.base_data.copy()
        data['ansible_time_start'] = self.ansible_task_time_start
        data['ansible_time_end'] = datetime.utcnow()
        data['ansible_time_duration'] = self._get_time_diff(data['ansible_time_start'], data['ansible_time_end'])

        data['ansible_type'] = CallbackModule.ANSIBLE_TYPE_TASK
        data['ansible_action'] = CallbackModule.ANSIBLE_ACTION_FINISH
        data['ansible_status'] = CallbackModule.ANSIBLE_STATUS_OK

        data['ansible_playbook_id'] = self.ansible_playbook_id
        data['ansible_playbook_name'] = self.ansible_playbook_name
        data['ansible_play_id'] = self.ansible_play_id
        data['ansible_play_name'] = self.ansible_play_name

        data['ansible_task_id'] = self.ansible_task_id
        data['ansible_task_name'] = self.ansible_task_name
        data['ansible_task_host'] = result._host.name

        if ttype != CallbackModule.ANSIBLE_TTYPE_GATHERING_FACTS:
            data['ansible_task_type'] = CallbackModule.ANSIBLE_TTYPE_TASK

        if CallbackModule.ANSIBLE_RESULT_CHANGED in result._result.keys():
            data['ansible_task_changed'] = result._result['changed']
        else:
            data['ansible_task_changed'] = False

        data['ansible_task_result'] = self._dump_results(result._result)

        if self._es_server_is_up:
            if 'include' not in data['ansible_task_result']:
                self._logger.info("TASK OK | " + data['ansible_task_name'] + " | RESULT |  " + data['ansible_task_result'], extra=data)

    def v2_runner_on_skipped(self, result, **kwargs):
        data = self.base_data.copy()
        data['ansible_time_start'] = self.ansible_task_time_start
        data['ansible_time_end'] = datetime.utcnow()
        data['ansible_time_duration'] = self._get_time_diff(data['ansible_time_start'], data['ansible_time_end'])

        data['ansible_type'] = CallbackModule.ANSIBLE_TYPE_TASK
        data['ansible_action'] = CallbackModule.ANSIBLE_ACTION_SKIPPED
        data['ansible_status'] = CallbackModule.ANSIBLE_STATUS_OK

        data['ansible_playbook_id'] = self.ansible_playbook_id
        data['ansible_playbook_name'] = self.ansible_playbook_name
        data['ansible_play_id'] = self.ansible_play_id
        data['ansible_play_name'] = self.ansible_play_name

        data['ansible_task_id'] = self.ansible_task_id
        data['ansible_task_name'] = self.ansible_task_name
        data['ansible_task_host'] = result._host.name
        data['ansible_task_result'] = self._dump_results(result._result)

        if self._es_server_is_up:
            if 'Include' not in data['ansible_task_name']:
                self._logger.info("TASK SKIPPED | " + data['ansible_task_name'], extra=data)

    def v2_runner_on_failed(self, result, **kwargs):
        data = self.base_data.copy()
        data['ansible_time_start'] = self.ansible_task_time_start
        data['ansible_time_end'] = datetime.utcnow()
        data['ansible_time_duration'] = self._get_time_diff(data['ansible_time_start'], data['ansible_time_end'])

        data['ansible_type'] = CallbackModule.ANSIBLE_TYPE_TASK
        data['ansible_status'] = CallbackModule.ANSIBLE_STATUS_FAILED

        data['ansible_playbook_id'] = self.ansible_playbook_id
        data['ansible_playbook_name'] = self.ansible_playbook_name
        data['ansible_play_id'] = self.ansible_play_id
        data['ansible_play_name'] = self.ansible_play_name

        data['ansible_task_id'] = self.ansible_task_id
        data['ansible_task_name'] = self.ansible_task_name
        data['ansible_task_host'] = result._host.name
        data['ansible_task_result'] = self._dump_results(result._result)
        data['ansible_task_error'] = True

        if CallbackModule.ANSIBLE_RESULT_CHANGED in result._result.keys():
            data['ansible_task_changed'] = result._result['changed']
        else:
            data['ansible_task_changed'] = False

        self.errors += 1

        if self._es_server_is_up:
            self._logger.error("TASK FAILED | " + data['ansible_task_name'] + " | HOST | " + data['ansible_task_host'] + " | RESULT | " + data['ansible_task_result'], extra=data)

    def v2_runner_on_unreachable(self, result, **kwargs):
        data = self.base_data.copy()
        data['ansible_time_start'] = self.ansible_task_time_start
        data['ansible_time_end'] = datetime.utcnow()
        data['ansible_time_duration'] = self._get_time_diff(data['ansible_time_start'], data['ansible_time_end'])

        data['ansible_type'] = CallbackModule.ANSIBLE_TYPE_TASK
        data['ansible_status'] = CallbackModule.ANSIBLE_STATUS_UNREACHABLE

        data['ansible_playbook_id'] = self.ansible_playbook_id
        data['ansible_playbook_name'] = self.ansible_playbook_name
        data['ansible_play_id'] = self.ansible_play_id
        data['ansible_play_name'] = self.ansible_play_name

        data['ansible_task_id'] = self.ansible_task_id
        data['ansible_task_name'] = self.ansible_task_name
        data['ansible_task_host'] = result._host.name
        data['ansible_task_result'] = self._dump_results(result._result)
        data['ansible_task_error'] = True

        self.errors += 1

        if self._es_server_is_up:
            self._logger.error("UNREACHABLE | " + data['ansible_task_name'] + " | HOST | " + data['ansible_task_host'] + " | RESULT | " + data['ansible_task_result'], extra=data)

    def v2_runner_on_async_failed(self, result, **kwargs):
        data = self.base_data.copy()
        data['ansible_time_start'] = self.ansible_task_time_start
        data['ansible_time_end'] = datetime.utcnow()
        data['ansible_time_duration'] = self._get_time_diff(data['ansible_time_start'], data['ansible_time_end'])

        data['ansible_type'] = CallbackModule.ANSIBLE_TYPE_TASK_ASYNC
        data['ansible_status'] = CallbackModule.ANSIBLE_STATUS_FAILED

        data['ansible_playbook_id'] = self.ansible_playbook_id
        data['ansible_playbook_name'] = self.ansible_playbook_name
        data['ansible_play_id'] = self.ansible_play_id
        data['ansible_play_name'] = self.ansible_play_name

        data['ansible_task_id'] = self.ansible_task_id
        data['ansible_task_name'] = self.ansible_task_name
        data['ansible_task_host'] = result._host.name
        data['ansible_task_result'] = self._dump_results(result._result)
        data['ansible_task_error'] = True

        self.errors += 1

        if self._es_server_is_up:
            self._logger.error("TASK ASYNC FAILED | " + data['ansible_task_name'] + " | HOST | " + data['ansible_task_host'] + " | RESULT | " + data['ansible_task_result'], extra=data)

    ###########################################################################
    # Elastic
    ###########################################################################

    def _initialize_logger(self):
        # initialize handler
        self.handler = CMRESHandler(
            hosts = [{'host': self._get_option('host'), 'port': self._get_option('port')}],
            auth_details = (self._get_option('username'), self._get_option('password')),
            auth_type = CMRESHandler.AuthType(self._get_option('auth_type')),
            use_ssl = self._get_option('use_ssl'),
            verify_ssl = self._get_option('verify_ssl'),
            es_index_name = self._get_option('es_index_name'),
            es_additional_fields = {'app': self._get_option('app'), 'env': self._get_option('env')},
            raise_on_indexing_exceptions = self._get_option('raise_on_indexing_exceptions')
        )

        # check if elastic server is up
        self._es_server_is_up = self.handler.test_es_source()

        # initialize logger
        self._logger = logging.getLogger(self._get_option('es_logger_name'))
        # FIXME self._logger.setLevel(self._get_option('es_logger_level'))
        self._logger.setLevel(logging.DEBUG)

        self._logger.addHandler(self.handler)

        #
        self.ansible_session = str(uuid.uuid1())
        self.ansible_version = os.popen("ansible --version | head -1").read()

        self.errors = 0
        self.base_data = {
          'ansible_global_session': self.ansible_session,
          'ansible_global_version': self.ansible_version
        }

        #
        self.options = {}
        if cli:
            self._options = cli.options
            self.base_data['ansible_global_checkmode'] = self._options.check
            self.base_data['ansible_global_tags'] = self._options.tags
            self.base_data['ansible_global_skip_tags'] = self._options.skip_tags
            self.base_data['ansible_global_inventory'] = self._options.inventory

    def _get_time_diff(self, time_start, time_end):
        time_duration = relativedelta(time_end, time_start)
        return '{h}h {m}m {s}s'.format(h=time_duration.hours, m=time_duration.minutes, s=time_duration.seconds)

    ###########################################################################
    # Configurations
    ###########################################################################
    def _get_option(self, option):
        return self._config_data.get(option)

    def _read_config_data(self, path):
        ''' validate config and set options as appropriate
            :arg path: path to common yaml format config file for this plugin
        '''
        config = {}
        try:
            # avoid loader cache so meta: refresh_inventory can pick up config changes
            # if we read more than once, fs cache should be good enough
            config = self.loader.load_from_file(path, cache=False)
        except Exception as e:
            raise AnsibleParserError(to_native(e))

        if not config:
            # no data
            raise AnsibleParserError("%s is empty" % (to_native(path)))
        elif config.get('plugin') != self.NAME:
            # this is not my config file
            raise AnsibleParserError("Incorrect plugin name in file: %s" % config.get('plugin', 'none found'))
        elif not isinstance(config, Mapping):
            # configs are dictionaries
            raise AnsibleParserError('inventory source has invalid structure, it should be a dictionary, got: %s' % type(config))

        return config

    def _get_config_file_name(self):
        ''' get the configuration file name
        '''
        path = os.path.dirname(os.path.abspath(__file__))
        file_name = None

        for filename in glob.glob(path + "/*." + self.NAME + ".yml"):
            file_name = filename

        if file_name == None:
            for filename in glob.glob(path + "/*." + self.NAME + ".yaml"):
                file_name = filename

        return file_name
